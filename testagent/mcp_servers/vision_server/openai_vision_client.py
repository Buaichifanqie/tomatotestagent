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

## 坐标规则

返回的百分比坐标是**相对于当前截图**的，不是相对于手机屏幕的。
- X 百分比 = 元素在截图中的水平位置 / 截图宽度 × 100%
- Y 百分比 = 元素在截图中的垂直位置 / 截图高度 × 100%

直接用你的眼睛估算元素在截图中的位置比例即可，不需要做任何换算。

## 分析规范
1. 仔细查看截图中的所有 UI 元素，包括应用图标、按钮、输入框、文字标签等
2. 如果找到目标元素，返回其在截图中的百分比坐标
3. 如果目标元素不在当前屏幕中，指出当前屏幕上有什么，并建议如何导航（滑动方向）找到目标

## 空间常识校验（输出坐标前必须检查）
- **同区域控件一致性**：如果你要寻找的元素（如：播放按钮）与之前找过的元素（如：暂停按钮）属于同一功能组件（如播放器控制栏），它们的 Y 坐标百分比应该非常接近。
- **常见布局校验**：播放器的控制按钮通常位于播放器区域的底部，Y 坐标通常在截图的 20% - 40% 之间。如果你算出的 Y 坐标明显偏离此范围，请重新检查。

## 百分比坐标格式
返回的坐标是相对于截图的百分比（0-100），格式为：
- 中心点百分比坐标: (pct_x%, pct_y%)
- 边界框百分比坐标: [pct_x1%, pct_y1%, pct_x2%, pct_y2%]

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
