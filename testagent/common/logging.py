from __future__ import annotations

import json
import logging
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

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
        # Default to WARNING for clean CLI output; set TESTAGENT_LOG_LEVEL=DEBUG for verbose
        import os
        level_name = os.environ.get("TESTAGENT_LOG_LEVEL", "WARNING").upper()
        logger.setLevel(getattr(logging, level_name, logging.WARNING))
    return logger


class PlainFormatter(logging.Formatter):
    """Simple plain-text formatter for log files."""
    def format(self, record: logging.LogRecord) -> str:
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        msg = _sanitize(record.getMessage())
        exc = ""
        if record.exc_info and record.exc_info[0] is not None:
            exc = "\n" + self.formatException(record.exc_info)
        return f"[{ts}] [{record.levelname:7s}] {record.name}: {msg}{exc}"


_file_handler: logging.FileHandler | None = None


def setup_file_logging(log_dir: str | Path, filename: str = "execution.log") -> Path:
    """Add a file handler to capture all logs to a file.

    Args:
        log_dir: Directory to save the log file.
        filename: Log file name (default: execution.log).

    Returns:
        Path to the created log file.
    """
    global _file_handler
    log_dir = Path(log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / filename

    # Remove existing file handler if any
    if _file_handler is not None:
        logging.getLogger().removeHandler(_file_handler)
        _file_handler.close()

    _file_handler = logging.FileHandler(log_path, encoding="utf-8")
    _file_handler.setFormatter(PlainFormatter())
    _file_handler.setLevel(logging.DEBUG)  # Capture everything to file

    # Attach to root logger so all modules' logs are captured
    root = logging.getLogger()
    root.addHandler(_file_handler)
    root.setLevel(logging.DEBUG)  # Let handlers control their own levels

    return log_path
