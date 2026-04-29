from __future__ import annotations

import json
import logging
import re
import sys
from datetime import datetime, timezone

_PII_PATTERNS: list[tuple[str, str]] = [
    (r"[1-9]\d{5}(?:19|20)\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])\d{3}[\dXx]", "身份证"),
    (r"(?<!\d)1[3-9]\d{9}(?!\d)", "手机号"),
    (r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}", "邮箱"),
]

_API_KEY_PATTERN: re.Pattern[str] = re.compile(
    r"(sk-|sk_live_|sk_test_|key-|token\s*[:=]\s*)[a-zA-Z0-9_\-]{8,}",
    re.IGNORECASE,
)


def mask_pii(text: str) -> str:
    result = text
    for pattern, label in _PII_PATTERNS:
        result = re.sub(pattern, f"[{label}已脱敏]", result)
    return result


def mask_api_key(text: str) -> str:
    def _replace(m: re.Match[str]) -> str:
        prefix = m.group(1)
        full = m.group(0)
        suffix = full[len(prefix) :]
        if len(suffix) <= 4:
            return f"{prefix}***"
        return f"{prefix}{suffix[:4]}***"

    return _API_KEY_PATTERN.sub(_replace, text)


def _sanitize(text: str) -> str:
    return mask_api_key(mask_pii(text))


class StructuredFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        log_entry: dict[str, object] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),  # noqa: UP017
            "level": record.levelname,
            "logger": record.name,
            "message": _sanitize(record.getMessage()),
        }
        if record.exc_info and record.exc_info[0] is not None:
            log_entry["exception"] = _sanitize(self.formatException(record.exc_info))
        if hasattr(record, "extra_data") and isinstance(record.extra_data, dict):
            sanitized_extra: dict[str, object] = {}
            for k, v in record.extra_data.items():
                sanitized_extra[k] = _sanitize(str(v)) if isinstance(v, str) else v
            log_entry["extra"] = sanitized_extra
        return json.dumps(log_entry, ensure_ascii=False)


def get_logger(name: str) -> logging.Logger:
    logger = logging.getLogger(name)
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(StructuredFormatter())
        logger.addHandler(handler)
        logger.setLevel(logging.DEBUG)
    return logger
