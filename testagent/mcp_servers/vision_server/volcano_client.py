from __future__ import annotations

import asyncio
import base64
import struct
from typing import Any

import httpx

from testagent.common.logging import get_logger

logger = get_logger(__name__)


def _get_image_dimensions(b64: str) -> tuple[int, int]:
    """Extract image dimensions from base64-encoded PNG/JPEG data.

    Reads only the header bytes — does not decode the full image.
    Returns (width, height). Falls back to (1080, 2400) on failure.
    """
    try:
        raw = base64.b64decode(b64[:64])  # first 64 bytes is enough for headers
        # PNG: bytes 16-23 contain width and height as big-endian uint32
        if raw[:8] == b"\x89PNG\r\n\x1a\n":
            w, h = struct.unpack(">II", raw[16:24])
            return int(w), int(h)
        # JPEG: need to scan for SOF marker
        if raw[:2] == b"\xff\xd8":
            # For JPEG, we need more bytes to find SOF
            raw_full = base64.b64decode(b64[:4096])
            i = 2
            while i < len(raw_full) - 1:
                if raw_full[i] == 0xFF:
                    marker = raw_full[i + 1]
                    if marker in (0xC0, 0xC1, 0xC2):
                        h_val, w_val = struct.unpack(">HH", raw_full[i + 5:i + 9])
                        return int(w_val), int(h_val)
                    length = struct.unpack(">H", raw_full[i + 2:i + 4])[0]
                    i += 2 + length
                else:
                    i += 1
    except Exception:
        pass
    return 1080, 2400

DEFAULT_API_URL = "https://ark.cn-beijing.volces.com/api/v3"
_CHAT_COMPLETIONS_PATH = "/chat/completions"
DEFAULT_MODEL = "doubao-seed-2-0-lite-260428"
DEFAULT_TIMEOUT = 60
DEFAULT_MAX_RETRIES = 2

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
        token_tracker: Any = None,
    ) -> None:
        self._api_key = api_key
        self._api_url = api_url
        self._model = model
        self._timeout = timeout
        self._max_retries = max_retries
        self._token_tracker = token_tracker

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
            device_width: Real device screen width in pixels (unused, kept for API compat)
            device_height: Real device screen height in pixels (unused, kept for API compat)

        Returns:
            Parsed response with content text, plus image_width/image_height
        """
        if not self._api_key:
            return {"error": "API key not configured"}

        # Extract actual image dimensions from the base64 data
        img_w, img_h = _get_image_dimensions(image_base64)

        sys_prompt = _VISION_SYSTEM_PROMPT

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
        # Use shorter connect timeout to fail fast on DNS errors
        httpx_timeout = httpx.Timeout(connect=10, read=self._timeout, write=self._timeout, pool=10)
        async with httpx.AsyncClient(timeout=httpx_timeout) as client:
            for attempt in range(self._max_retries):
                try:
                    endpoint = self._api_url.rstrip("/") + _CHAT_COMPLETIONS_PATH
                    response = await client.post(endpoint, headers=headers, json=payload)
                    response.raise_for_status()
                    result = response.json()
                    content = result["choices"][0]["message"]["content"]

                    # Record token usage and print immediately
                    if self._token_tracker:
                        usage = result.get("usage", {})
                        if usage:
                            pt = usage.get("prompt_tokens", 0)
                            ct = usage.get("completion_tokens", 0)
                            tt = usage.get("total_tokens", 0)
                            self._token_tracker.record(
                                category="vision",
                                prompt_tokens=pt,
                                completion_tokens=ct,
                                total_tokens=tt,
                            )
                            if tt > 0:
                                _detail = f"{pt}↑ {ct}↓ = {tt}" if pt and ct else str(tt)
                                print(f"      \033[35m[Vision tokens] {_detail}\033[0m")

                    return {
                        "content": content,
                        "finish_reason": result["choices"][0].get("finish_reason", ""),
                        "image_width": img_w,
                        "image_height": img_h,
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
