from __future__ import annotations

import asyncio
from typing import Any

import httpx

from testagent.common.logging import get_logger

logger = get_logger(__name__)

DEFAULT_API_URL = "https://open.bigmodel.cn/api/paas/v4/chat/completions"
DEFAULT_MODEL = "glm-4.6v-flash"
DEFAULT_TIMEOUT = 30
DEFAULT_MAX_RETRIES = 3

_VISION_SYSTEM_PROMPT = """你是一个手机界面分析专家。你的任务是分析手机截图，找出用户指定的 UI 元素。

## 最重要的规则：坐标映射

截图很可能是被压缩过的，你看到的像素坐标**不是**手机屏幕的真实坐标。
你必须返回**基于手机实际分辨率的坐标**，而不是基于截图像素的坐标。

### 映射步骤（必须严格执行）
1. 确定手机实际分辨率：通常是 1080x2400（宽x高）
2. 确定截图的实际像素尺寸（可能被压缩，如 480x1067 或 480x2214）
3. 找到目标元素在截图中的像素位置 (sx, sy)
4. 映射到手机实际坐标：
   - 实际 X = sx × (手机宽度 / 截图宽度)
   - 实际 Y = sy × (手机单屏高度 / 截图单屏高度)
   - 其中：截图单屏高度 = 截图宽度 / 0.45

### 示例
截图尺寸：宽 480px，高 2214px（长截图）
手机分辨率：1080x2400
目标元素在截图中的位置：(30, 270)

计算：
- 实际 X = 30 × (1080 / 480) = 67.5 ≈ 68px
- 截图单屏高度 = 480 / 0.45 = 1067px
- 实际 Y = 270 × (2400 / 1067) = 607 ≈ 608px

正确结果：(68, 608)
错误结果（千万不要这样算）：(68, 270) — 这是截图像素，不是手机坐标！

### 常见错误
- ❌ 直接返回截图像素坐标（截图像素 ≠ 手机像素）
- ❌ 用截图总高度算 Y（长截图总高度远大于单屏高度）
- ✅ 始终用"截图宽度 / 0.45"作为单屏高度来映射 Y 坐标

## 分析规范
1. 仔细查看截图中的所有 UI 元素，包括应用图标、按钮、输入框、文字标签等
2. 如果找到目标元素，返回其在手机屏幕上的实际坐标（按上述映射步骤计算）
3. 坐标格式：返回元素在手机屏幕中的像素坐标（基于 1080x2400 分辨率）
4. 如果目标元素不在当前屏幕中，指出当前屏幕上有什么，并建议如何导航（滑动方向）找到目标

## 坐标返回格式
你可以在描述中包含坐标信息，格式为：
- 中心点坐标: (x, y) — 基于手机实际分辨率
- 边界框: [x1, y1, x2, y2]

## 滑动建议
如果目标不在当前屏幕，建议滑动方向（swipe_left/swipe_right/swipe_up/swipe_down），并说明原因。"""


class OpenAIVisionClient:
    """OpenAI-compatible vision API client for visual analysis."""

    def __init__(
        self,
        api_key: str,
        api_url: str = DEFAULT_API_URL,
        model: str = DEFAULT_MODEL,
        timeout: int = DEFAULT_TIMEOUT,
        max_retries: int = DEFAULT_MAX_RETRIES,
    ) -> None:
        self._api_key = api_key
        self._api_url = api_url
        self._model = model
        self._timeout = timeout
        self._max_retries = max_retries

    @property
    def is_configured(self) -> bool:
        """Check if the client has a non-empty API key configured."""
        return bool(self._api_key)

    async def analyze(self, image_base64: str, prompt: str) -> dict[str, Any]:
        """Send screenshot to vision API for analysis.

        Args:
            image_base64: Base64-encoded PNG screenshot (data URL prefix added automatically)
            prompt: User prompt describing what to find/analyze

        Returns:
            Parsed response with content text
        """
        if not self._api_key:
            return {"error": "API key not configured"}

        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

        payload = {
            "model": self._model,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/png;base64,{image_base64}",
                            },
                        },
                        {
                            "type": "text",
                            "text": f"{_VISION_SYSTEM_PROMPT}\n\n{prompt}",
                        },
                    ],
                }
            ],
            "temperature": 0.1,
            "max_tokens": 1024,
        }

        last_exception: Exception | None = None
        async with httpx.AsyncClient(timeout=httpx.Timeout(self._timeout)) as client:
            for attempt in range(self._max_retries):
                try:
                    response = await client.post(self._api_url, headers=headers, json=payload)
                    response.raise_for_status()
                    result = response.json()
                    content = result["choices"][0]["message"]["content"]
                    return {
                        "content": content,
                        "finish_reason": result["choices"][0].get("finish_reason", ""),
                    }
                except httpx.HTTPStatusError as e:
                    status = e.response.status_code
                    body_preview = e.response.text[:300] if e.response.text else ""
                    # Only retry on server errors (5xx) and rate limits (429)
                    if status >= 500 or status == 429:
                        if attempt < self._max_retries - 1:
                            # Rate limits need longer backoff; 5xx use shorter
                            wait = 4**attempt if status == 429 else 2**attempt
                            logger.warning(
                                "Vision API error, retrying",
                                extra={
                                    "extra_data": {
                                        "attempt": attempt + 1,
                                        "wait": wait,
                                        "error": str(e),
                                        "body": body_preview,
                                    }
                                },
                            )
                            await asyncio.sleep(wait)
                            continue
                    last_exception = e
                    logger.error(
                        "Vision API request failed",
                        extra={
                            "extra_data": {
                                "status": status,
                                "error": str(e),
                                "body": body_preview,
                            }
                        },
                    )
                    break
                except Exception as e:
                    last_exception = e
                    logger.error(
                        "Vision API unexpected error",
                        extra={"extra_data": {"error": str(e)}},
                    )
                    break

        return {"error": str(last_exception) if last_exception else "Unknown error"}
