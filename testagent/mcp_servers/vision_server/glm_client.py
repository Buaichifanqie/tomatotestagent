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

## 分析规范
1. 仔细查看截图中的所有 UI 元素，包括应用图标、按钮、输入框、文字标签等
2. 如果找到目标元素，返回其精确坐标
3. 坐标格式：返回元素在截图中的像素坐标
4. 如果目标元素不在当前屏幕中，指出当前屏幕上有什么，并建议如何导航（滑动方向）找到目标

## 坐标返回格式
你可以在描述中包含坐标信息，格式为：
- 中心点坐标: (x, y)
- 边界框: [x1, y1, x2, y2]
- 也可以同时提供两种格式

## 滑动建议
如果目标不在当前屏幕，建议滑动方向（swipe_left/swipe_right/swipe_up/swipe_down），并说明原因。"""


class GLMClient:
    """GLM-4.6V-Flash API client for visual analysis."""

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
        """Send screenshot to GLM API for analysis.

        Args:
            image_base64: Base64-encoded PNG screenshot (without data:image prefix)
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
                    # Only retry on server errors (5xx) and rate limits (429)
                    if status >= 500 or status == 429:
                        if attempt < self._max_retries - 1:
                            wait = 2**attempt
                            logger.warning(
                                "GLM API error, retrying",
                                extra={
                                    "extra_data": {
                                        "attempt": attempt + 1,
                                        "wait": wait,
                                        "error": str(e),
                                    }
                                },
                            )
                            await asyncio.sleep(wait)
                            continue
                    last_exception = e
                    logger.error(
                        "GLM API request failed",
                        extra={"extra_data": {"status": status, "error": str(e)}},
                    )
                except Exception as e:
                    last_exception = e
                    logger.error(
                        "GLM API unexpected error",
                        extra={"extra_data": {"error": str(e)}},
                    )
                    break

        return {"error": str(last_exception) if last_exception else "Unknown error"}
