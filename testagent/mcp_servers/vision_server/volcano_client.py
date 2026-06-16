from __future__ import annotations

import asyncio
from typing import Any

import httpx

from testagent.common.logging import get_logger

logger = get_logger(__name__)

DEFAULT_API_URL = "https://ark.cn-beijing.volces.com/api/v3"
_CHAT_COMPLETIONS_PATH = "/chat/completions"
DEFAULT_MODEL = "doubao-seed-2-0-lite-260428"
DEFAULT_TIMEOUT = 60
DEFAULT_MAX_RETRIES = 2

_VISION_SYSTEM_PROMPT = """你是一个手机界面分析专家。你的任务是分析手机截图，找出用户指定的 UI 元素。

## 分析规范
1. 仔细查看截图中的所有 UI 元素，包括应用图标、按钮、输入框、文字标签等
2. 如果找到目标元素，返回其精确百分比坐标
3. 坐标格式：返回元素在屏幕中的百分比位置（0-100%）
4. 如果目标元素不在当前屏幕中，指出当前屏幕上有什么，并建议如何导航（滑动方向）找到目标

## 百分比坐标格式
你返回的坐标必须是相对于屏幕的百分比（0-100），格式为：
- 中心点百分比坐标: (pct_x%, pct_y%)
- 边界框百分比坐标: [pct_x1%, pct_y1%, pct_x2%, pct_y2%]
- 也可以同时提供两种格式

例如：如果元素在屏幕正中央，返回 (50%, 50%)；如果在左上角，返回 (25%, 25%)。

## 长截图处理（重要）
截图可能是长截图（页面滚动截图），而非单屏截图。长截图的高度远大于手机屏幕高度。
- 百分比坐标必须基于**手机单屏尺寸**（通常是 1080x2400 或类似比例），而非截图的总像素尺寸
- 判断方法：如果截图的宽高比明显小于正常手机屏幕的宽高比（约 0.45），则为长截图
- 对于长截图，先估算元素在"第一屏"内的位置，再计算相对于单屏的百分比
- Y 坐标的估算：单屏高度约占长截图总高度的（手机屏幕宽高比 / 截图宽高比），例如截图宽 480 高 2214 时，单屏高度约为 480/0.45 ≈ 1067px

## 滑动建议
如果目标不在当前屏幕，建议滑动方向（swipe_left/swipe_right/swipe_up/swipe_down），并说明原因。"""


class VolcanoVisionClient:
    """Volcano Engine (火山方舟) multimodal vision API client.

    Uses the OpenAI-compatible Chat Completions API format.
    Base URL + /chat/completions forms the endpoint.
    """

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

    async def analyze(
        self,
        image_base64: str,
        prompt: str,
        device_width: int | None = None,
        device_height: int | None = None,
    ) -> dict[str, Any]:
        """Send screenshot to Volcano Engine API for analysis.

        Args:
            image_base64: Base64-encoded screenshot (without data:image prefix)
            prompt: User prompt describing what to find/analyze
            device_width: Real device screen width in pixels (for coordinate conversion)
            device_height: Real device screen height in pixels (for coordinate conversion)

        Returns:
            Parsed response with content text
        """
        if not self._api_key:
            return {"error": "API key not configured"}

        # Inject device dimensions into the system prompt so the model returns
        # percentage-based coordinates that can be converted accurately.
        sys_prompt = _VISION_SYSTEM_PROMPT
        if device_width and device_height:
            sys_prompt += (
                f"\n\n## 设备信息\n"
                f"实际设备屏幕分辨率为 {device_width}x{device_height} 像素。\n"
                f"截图中的坐标 = 百分比 × {device_width}（宽度）× {device_height}（高度）。\n"
                f"请严格使用百分比坐标，不要使用像素坐标。"
            )

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
                            "text": f"{sys_prompt}\n\n{prompt}",
                        },
                    ],
                }
            ],
            "temperature": 0.1,
            "max_tokens": 2048,
        }

        last_exception: Exception | None = None
        async with httpx.AsyncClient(timeout=httpx.Timeout(self._timeout)) as client:
            for attempt in range(self._max_retries):
                try:
                    endpoint = self._api_url.rstrip("/") + _CHAT_COMPLETIONS_PATH
                    response = await client.post(endpoint, headers=headers, json=payload)
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
                    error_detail = str(e) or repr(e) or type(e).__name__
                    logger.error(
                        "Vision API unexpected error: %s",
                        error_detail,
                        extra={"extra_data": {"error": error_detail, "type": type(e).__name__}},
                    )
                    break

        return {"error": str(last_exception) if last_exception else "Unknown error"}
