"""YOLO 训练封装。

封装 ultralytics YOLO 训练流程，支持取消、进度回调、GPU 内存清理。
参考 mobile_vision 的 ``models/yolo/trainer.py``。
"""
from __future__ import annotations

import gc
import os
from pathlib import Path
from typing import Any, Callable


class YOLOTrainer:
    """封装 ultralytics YOLO 训练流程。"""

    def __init__(
        self,
        base_model: str = "yolov8n.pt",
        device: str = "cpu",
    ) -> None:
        """初始化训练器。

        Args:
            base_model: 基础模型权重路径（默认 yolov8n.pt，自动下载）。
            device: 训练设备 ("cpu" / "cuda:0" / "mps")。
        """
        self._base_model = base_model
        self._device = device
        self._model: Any = None
        self._aborted = False

    def load_model(self) -> None:
        """加载 YOLO 基础模型。"""
        from ultralytics import YOLO

        self._model = YOLO(self._base_model)

    async def train(
        self,
        data_yaml: str,
        model_name: str = "",
        epochs: int = 100,
        batch_size: int = 16,
        imgsz: int = 640,
        patience: int = 20,
        progress_callback: Callable[[int, int], None] | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """运行 YOLO 训练。

        Args:
            data_yaml: data.yaml 文件路径。
            model_name: 自定义模型名称（用于保存）。
            epochs: 训练轮数。
            batch_size: 批次大小。
            imgsz: 输入图像大小。
            patience: 早停耐心值。
            progress_callback: 进度回调 (current_epoch, total_epochs)。
            **kwargs: 传递给 ultralytics YOLO.train() 的额外参数。

        Returns:
            dict 包含 status, best_model, metrics。
        """
        if self._model is None:
            self.load_model()

        self._aborted = False

        # 注册 epoch 回调
        class EpochCallback:
            def __init__(
                self,
                trainer: YOLOTrainer,
                progress_cb: Callable[[int, int], None] | None,
            ) -> None:
                self._trainer = trainer
                self._progress_cb = progress_cb

            def on_epoch_end(self, trainer_obj: Any) -> None:
                if self._trainer._aborted:
                    raise _TrainingCancelledError("训练已被用户取消")
                if self._progress_cb:
                    current = getattr(trainer_obj, "epoch", 0) + 1
                    total = getattr(trainer_obj, "epochs", epochs)
                    self._progress_cb(current, total)

        try:
            self._model.add_callback(
                "on_train_epoch_end",
                EpochCallback(self, progress_callback).on_epoch_end,
            )

            results = self._model.train(
                data=data_yaml,
                epochs=epochs,
                batch=batch_size,
                imgsz=imgsz,
                patience=patience,
                device=self._device,
                **kwargs,
            )

        except _TrainingCancelledError:
            self._cleanup_gpu()
            # 仍然尝试返回当前最好的模型
            best_path = self._find_best_model()
            return {
                "status": "cancelled",
                "best_model": best_path or "",
                "metrics": {},
            }
        except Exception as e:
            self._cleanup_gpu()
            return {
                "status": "failed",
                "error": str(e),
                "best_model": "",
                "metrics": {},
            }

        # 提取指标
        results_dict = getattr(results, "results_dict", {})
        metrics = {
            "mAP50": float(results_dict.get("metrics/mAP50(B)", 0)),
            "mAP50-95": float(results_dict.get("metrics/mAP50-95(B)", 0)),
            "precision": float(results_dict.get("metrics/precision(B)", 0)),
            "recall": float(results_dict.get("metrics/recall(B)", 0)),
        }

        best_path = self._find_best_model()

        self._cleanup_gpu()
        return {
            "status": "completed",
            "best_model": best_path or "",
            "metrics": metrics,
        }

    def _find_best_model(self) -> str | None:
        """查找训练产出的最佳模型路径。"""
        if self._model is None:
            return None
        try:
            save_dir = Path(self._model.trainer.save_dir) if hasattr(self._model, "trainer") and self._model.trainer else None  # type: ignore[union-attr]
            if save_dir:
                weights_dir = save_dir / "weights"
                best = weights_dir / "best.pt"
                if best.exists():
                    return str(best)
                last = weights_dir / "last.pt"
                if last.exists():
                    return str(last)
        except Exception:
            pass
        return None

    def abort(self) -> None:
        """请求取消训练。"""
        self._aborted = True

    def _cleanup_gpu(self) -> None:
        """清理 GPU 内存。"""
        import torch

        if torch.cuda.is_available():
            torch.cuda.empty_cache()
            torch.cuda.synchronize()
        self._model = None
        gc.collect()


class _TrainingCancelledError(Exception):
    """训练被用户取消。"""
    pass
