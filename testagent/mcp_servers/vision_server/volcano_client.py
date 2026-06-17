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

## 最重要的规则：坐标映射

截图很可能是被压缩过的，你看到的像素坐标**不是**手机屏幕的真实坐标。
你必须返回**基于手机实际分辨率的百分比坐标**，而不是基于截图像素的坐标。

### 映射步骤（必须严格执行）
1. 确定手机实际分辨率：通常是 1080x2400（宽x高）
2. 确定截图的实际像素尺寸（可能被压缩，如 480x1067 或 480x2214）
3. 找到目标元素在截图中的像素位置 (sx, sy)
4. 计算百分比坐标时，**不要用截图总高度**，而是用以下公式：
   - X 百分比 = sx / 截图宽度 × 100%（因为截图宽度通常等于手机宽度）
   - Y 百分比 = sy / 单屏高度 × 100%（单屏高度 = 截图宽度 / 0.45）

### 示例
截图尺寸：宽 480px，高 2214px（长截图）
手机分辨率：1080x2400
目标元素在截图中的位置：(30, 270)

计算：
- X 百分比 = 30 / 480 = 6.25%
- 单屏高度 = 480 / 0.45 = 1067px
- Y 百分比 = 270 / 1067 = 25.3%

正确结果：(6.3%, 25.3%)
错误结果（千万不要这样算）：(6.3%, 270/2214=12.2%)

### 常见错误
- ❌ 用截图总高度算 Y 百分比（长截图的总高度远大于单屏高度，会导致 Y 坐标偏小）
- ❌ 直接返回截图像素坐标（截图像素 ≠ 手机像素）
- ✅ 始终用"截图宽度 / 0.45"作为单屏高度来计算 Y 百分比

## 分析规范
1. 仔细查看截图中的所有 UI 元素，包括应用图标、按钮、输入框、文字标签等
2. 如果找到目标元素，返回其精确百分比坐标（按上述映射步骤计算）
3. 坐标格式：返回元素在屏幕中的百分比位置（0-100%）
4. 如果目标元素不在当前屏幕中，指出当前屏幕上有什么，并建议如何导航（滑动方向）找到目标

## 百分比坐标格式
你返回的坐标必须是相对于手机单屏的百分比（0-100），格式为：
- 中心点百分比坐标: (pct_x%, pct_y%)
- 边界框百分比坐标: [pct_x1%, pct_y1%, pct_x2%, pct_y2%]

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
