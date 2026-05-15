from __future__ import annotations

import os
import re
from typing import ClassVar

from testagent.common.errors import ConfigError
from testagent.common.logging import get_logger

_logger = get_logger(__name__)

_KEYRING_AVAILABLE: bool = False

try:
    import keyring

    _KEYRING_AVAILABLE = True
except ImportError:
    keyring = None  # type: ignore[assignment]


class KeyManager:
    _ENV_PREFIX: str = "TESTAGENT_"

    @staticmethod
    def get_key(service: str, key_name: str) -> str:
        env_key = f"{KeyManager._ENV_PREFIX}{service.upper()}_{key_name.upper()}"
        value = os.environ.get(env_key)
        if value:
            _logger.debug("Key retrieved from env: %s", env_key)
            return value

        if _KEYRING_AVAILABLE:
            stored = keyring.get_password(service, key_name)
            if stored is not None:
                _logger.debug("Key retrieved from keyring: %s/%s", service, key_name)
                return stored

        raise ConfigError(
            f"Key not found for service={service!r}, key_name={key_name!r}",
            code="KEY_NOT_FOUND",
            details={"env_var": env_key, "keyring_available": _KEYRING_AVAILABLE},
        )

    @staticmethod
    def set_key(service: str, key_name: str, value: str) -> None:
        if not _KEYRING_AVAILABLE:
            raise ConfigError(
                "keyring package is not installed; cannot store key securely",
                code="KEYRING_UNAVAILABLE",
            )
        keyring.set_password(service, key_name, value)
        _logger.debug("Key stored to keyring: %s/%s", service, key_name)

    @staticmethod
    def mask(value: str, visible_prefix: int = 4) -> str:
        if len(value) <= visible_prefix:
            return "***"
        return f"{value[:visible_prefix]}{'*' * (len(value) - visible_prefix)}"


class DataSanitizer:
    PII_PATTERNS: ClassVar[dict[str, str]] = {
        "id_card": r"[1-9]\d{5}(?:19|20)\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])\d{3}[\dXx]",
        "phone": r"(?<!\d)1[3-9]\d{9}(?!\d)",
        "email": r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}",
        "api_key": r"(sk-|sk_live_|sk_test_|key-|token\s*[:=]\s*)[a-zA-Z0-9_\-]{8,}",
    }

    _COMPILED: ClassVar[dict[str, re.Pattern[str]]] = {
        name: re.compile(pattern) for name, pattern in PII_PATTERNS.items()
    }

    _LABELS: ClassVar[dict[str, str]] = {
        "id_card": "[\u8eab\u4efd\u8bc1\u5df2\u8131\u654f]",
        "phone": "[\u624b\u673a\u53f7\u5df2\u8131\u654f]",
        "email": "[\u90ae\u7bb1\u5df2\u8131\u654f]",
        "api_key": "[API Key\u5df2\u8131\u654f]",
    }

    @staticmethod
    def sanitize(text: str) -> str:
        result = text
        for name, pattern in DataSanitizer._COMPILED.items():
            if name == "api_key":
                result = DataSanitizer._mask_api_key_match(result, pattern)
            else:
                label = DataSanitizer._LABELS[name]
                result = pattern.sub(label, result)
        return result

    @staticmethod
    def sanitize_dict(data: dict[str, object]) -> dict[str, object]:
        return DataSanitizer._sanitize_value(data)  # type: ignore[return-value]

    @staticmethod
    def _mask_api_key_match(text: str, pattern: re.Pattern[str]) -> str:
        def _replace(m: re.Match[str]) -> str:
            prefix = m.group(1)
            full = m.group(0)
            suffix = full[len(prefix) :]
            if len(suffix) <= 4:
                return f"{prefix}***"
            return f"{prefix}{suffix[:4]}***"

        return pattern.sub(_replace, text)

    @staticmethod
    def _sanitize_value(value: object) -> object:
        if isinstance(value, str):
            return DataSanitizer.sanitize(value)
        if isinstance(value, dict):
            return {k: DataSanitizer._sanitize_value(v) for k, v in value.items()}
        if isinstance(value, list):
            return [DataSanitizer._sanitize_value(item) for item in value]
        return value
