from __future__ import annotations

import time
from typing import Any

_screenshot_store: dict[str, dict[str, Any]] = {}
_next_id: int = 0


def store_screenshot(base64_data: str) -> str:
    """Store a screenshot and return a reference key."""
    global _next_id
    _next_id += 1
    key = f"screenshot_{_next_id}_{int(time.time())}"
    _screenshot_store[key] = {
        "base64": base64_data,
        "format": "png",
        "timestamp": time.time(),
    }
    return key


def get_screenshot(key: str) -> str | None:
    """Retrieve a screenshot's base64 data by reference key."""
    entry = _screenshot_store.get(key)
    if entry is None:
        return None
    return entry["base64"]


def _cleanup_old_screenshots(max_age: float = 300.0) -> None:
    """Remove screenshots older than max_age seconds."""
    now = time.time()
    stale = [k for k, v in _screenshot_store.items() if now - v["timestamp"] > max_age]
    for k in stale:
        del _screenshot_store[k]
