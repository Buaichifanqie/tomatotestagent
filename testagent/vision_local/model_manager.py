"""YOLO 已训练模型管理。

支持列出/删除/选择已训练的 YOLO 模型。
参考 mobile_vision 的 ``app/yolo/controller.py``（get_models/delete_model/create_model）。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class ModelManager:
    """管理已训练的 YOLO 模型。"""

    def __init__(self, models_dir: str = "./models/yolo") -> None:
        self._root = Path(models_dir)
        self._root.mkdir(parents=True, exist_ok=True)
        self._manifest_path = self._root / "manifest.json"

    def _load_manifest(self) -> dict[str, Any]:
        """加载模型清单。"""
        if self._manifest_path.exists():
            return json.loads(self._manifest_path.read_text(encoding="utf-8"))
        return {"models": [], "default": ""}

    def _save_manifest(self, manifest: dict[str, Any]) -> None:
        """保存模型清单。"""
        self._manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def register_model(
        self,
        name: str,
        model_path: str,
        metrics: dict[str, Any] | None = None,
        dataset_name: str = "",
        classes: list[str] | None = None,
    ) -> dict[str, Any]:
        """注册一个训练好的模型。

        Args:
            name: 模型名称。
            model_path: 模型文件路径（best.pt）。
            metrics: 评估指标 {mAP50, precision, recall}。
            dataset_name: 训练所用的数据集名称。
            classes: 模型识别的类别列表。

        Returns:
            模型信息 dict。
        """
        manifest = self._load_manifest()
        model_path_obj = Path(model_path)

        entry = {
            "name": name,
            "path": str(model_path_obj.resolve()),
            "size": model_path_obj.stat().st_size if model_path_obj.exists() else 0,
            "metrics": metrics or {},
            "dataset_name": dataset_name,
            "classes": classes or [],
        }

        # 去重
        manifest["models"] = [m for m in manifest["models"] if m["name"] != name]
        manifest["models"].append(entry)

        if not manifest["default"]:
            manifest["default"] = name

        self._save_manifest(manifest)
        return entry

    def list_models(self) -> list[dict[str, Any]]:
        """列出所有已训练的模型。"""
        manifest = self._load_manifest()
        default = manifest.get("default", "")
        models = []
        for m in manifest.get("models", []):
            m_copy = dict(m)
            m_copy["is_default"] = m["name"] == default
            models.append(m_copy)
        return models

    def get_default_model(self) -> str:
        """获取默认模型路径。"""
        manifest = self._load_manifest()
        default_name = manifest.get("default", "")
        if not default_name:
            return ""
        default_path = ""
        for m in manifest.get("models", []):
            if m["name"] == default_name:
                default_path = m.get("path", "")
                break
        return default_path

    def set_default_model(self, name: str) -> bool:
        """设置默认模型。

        Args:
            name: 模型名称。

        Returns:
            True 成功，False 模型不存在。
        """
        manifest = self._load_manifest()
        exists = any(m["name"] == name for m in manifest["models"])
        if not exists:
            return False
        manifest["default"] = name
        self._save_manifest(manifest)
        return True

    def delete_model(self, name: str) -> bool:
        """删除模型。

        Args:
            name: 模型名称。

        Returns:
            True 成功，False 模型不存在。
        """
        manifest = self._load_manifest()
        original_len = len(manifest["models"])
        manifest["models"] = [m for m in manifest["models"] if m["name"] != name]
        if len(manifest["models"]) == original_len:
            return False

        if manifest.get("default") == name:
            manifest["default"] = manifest["models"][0]["name"] if manifest["models"] else ""

        self._save_manifest(manifest)
        return True
