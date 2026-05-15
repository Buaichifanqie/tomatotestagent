from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest

from testagent.common.errors import ConfigError
from testagent.common.security import DataSanitizer, KeyManager


class TestKeyManagerGetKey:
    def test_get_key_from_env_takes_priority(self) -> None:
        env_key = f"{KeyManager._ENV_PREFIX}MYSERVICE_MY_API_KEY"
        with patch.dict(os.environ, {env_key: "env-secret-value"}, clear=False):
            result = KeyManager.get_key("myservice", "my_api_key")
        assert result == "env-secret-value"

    def test_get_key_env_var_naming_convention(self) -> None:
        env_key = f"{KeyManager._ENV_PREFIX}OPENAI_API_KEY"
        with patch.dict(os.environ, {env_key: "sk-test-key"}, clear=False):
            result = KeyManager.get_key("openai", "api_key")
        assert result == "sk-test-key"

    def test_get_key_falls_back_to_keyring(self) -> None:
        mock_keyring = MagicMock()
        mock_keyring.get_password.return_value = "keyring-secret"
        with (
            patch.dict(os.environ, {}, clear=True),
            patch("testagent.common.security._KEYRING_AVAILABLE", True),
            patch("testagent.common.security.keyring", mock_keyring),
        ):
            result = KeyManager.get_key("openai", "api_key")
        assert result == "keyring-secret"
        mock_keyring.get_password.assert_called_once_with("openai", "api_key")

    def test_get_key_raises_config_error_when_not_found(self) -> None:
        mock_keyring = MagicMock()
        mock_keyring.get_password.return_value = None
        with (
            patch.dict(os.environ, {}, clear=True),
            patch("testagent.common.security._KEYRING_AVAILABLE", True),
            patch("testagent.common.security.keyring", mock_keyring),
            pytest.raises(ConfigError) as exc_info,
        ):
            KeyManager.get_key("missing_service", "missing_key")
        assert exc_info.value.code == "KEY_NOT_FOUND"
        assert "missing_service" in exc_info.value.message
        assert "missing_key" in exc_info.value.message

    def test_get_key_raises_config_error_when_keyring_unavailable(self) -> None:
        with (
            patch.dict(os.environ, {}, clear=True),
            patch("testagent.common.security._KEYRING_AVAILABLE", False),
            pytest.raises(ConfigError) as exc_info,
        ):
            KeyManager.get_key("openai", "api_key")
        assert exc_info.value.code == "KEY_NOT_FOUND"

    def test_get_key_env_takes_priority_over_keyring(self) -> None:
        env_key = f"{KeyManager._ENV_PREFIX}OPENAI_API_KEY"
        mock_keyring = MagicMock()
        mock_keyring.get_password.return_value = "keyring-value"
        with (
            patch.dict(os.environ, {env_key: "env-value"}, clear=False),
            patch("testagent.common.security._KEYRING_AVAILABLE", True),
            patch("testagent.common.security.keyring", mock_keyring),
        ):
            result = KeyManager.get_key("openai", "api_key")
        assert result == "env-value"
        mock_keyring.get_password.assert_not_called()


class TestKeyManagerSetKey:
    def test_set_key_stores_to_keyring(self) -> None:
        mock_keyring = MagicMock()
        with (
            patch("testagent.common.security._KEYRING_AVAILABLE", True),
            patch("testagent.common.security.keyring", mock_keyring),
        ):
            KeyManager.set_key("openai", "api_key", "my-secret")
        mock_keyring.set_password.assert_called_once_with("openai", "api_key", "my-secret")

    def test_set_key_raises_when_keyring_unavailable(self) -> None:
        with (
            patch("testagent.common.security._KEYRING_AVAILABLE", False),
            pytest.raises(ConfigError) as exc_info,
        ):
            KeyManager.set_key("openai", "api_key", "my-secret")
        assert exc_info.value.code == "KEYRING_UNAVAILABLE"


class TestKeyManagerMask:
    def test_mask_default_visible_prefix(self) -> None:
        result = KeyManager.mask("sk-1234abcd")
        assert result == "sk-1*******"

    def test_mask_custom_visible_prefix(self) -> None:
        result = KeyManager.mask("sk-1234abcd", visible_prefix=6)
        assert result == "sk-123*****"

    def test_mask_value_shorter_than_prefix(self) -> None:
        result = KeyManager.mask("abc", visible_prefix=4)
        assert result == "***"

    def test_mask_value_equal_to_prefix(self) -> None:
        result = KeyManager.mask("abcd", visible_prefix=4)
        assert result == "***"

    def test_mask_value_longer_than_prefix(self) -> None:
        result = KeyManager.mask("abcdef", visible_prefix=4)
        assert result == "abcd**"

    def test_mask_preserves_prefix_portion(self) -> None:
        api_key = "sk-proj-abc123XYZ789"
        result = KeyManager.mask(api_key, visible_prefix=4)
        assert result.startswith("sk-p")
        assert "***" not in result[:4]


class TestDataSanitizerSanitize:
    def test_sanitize_phone_number(self) -> None:
        result = DataSanitizer.sanitize("user phone is 13812345678")
        assert "13812345678" not in result
        assert "[\u624b\u673a\u53f7\u5df2\u8131\u654f]" in result

    def test_sanitize_id_card(self) -> None:
        result = DataSanitizer.sanitize("id: 110101199003076534")
        assert "110101199003076534" not in result
        assert "[\u8eab\u4efd\u8bc1\u5df2\u8131\u654f]" in result

    def test_sanitize_email(self) -> None:
        result = DataSanitizer.sanitize("contact: user@example.com")
        assert "user@example.com" not in result
        assert "[\u90ae\u7bb1\u5df2\u8131\u654f]" in result

    def test_sanitize_id_card_with_x(self) -> None:
        result = DataSanitizer.sanitize("id: 11010119900307653X")
        assert "11010119900307653X" not in result
        assert "[\u8eab\u4efd\u8bc1\u5df2\u8131\u654f]" in result

    def test_sanitize_api_key(self) -> None:
        result = DataSanitizer.sanitize("key: sk-abcdefghijklmnop")
        assert "sk-abcdefghijklmnop" not in result
        assert "sk-abcd***" in result

    def test_sanitize_api_key_sk_live(self) -> None:
        result = DataSanitizer.sanitize("key: sk_live_abcdefghijklmnop")
        assert "sk_live_abcdefghijklmnop" not in result
        assert "sk_live_abcd***" in result

    def test_sanitize_multiple_pii_types(self) -> None:
        text = "phone 13900001111 email test@test.com id 110101199003076534"
        result = DataSanitizer.sanitize(text)
        assert "13900001111" not in result
        assert "test@test.com" not in result
        assert "110101199003076534" not in result
        assert "[\u624b\u673a\u53f7\u5df2\u8131\u654f]" in result
        assert "[\u90ae\u7bb1\u5df2\u8131\u654f]" in result
        assert "[\u8eab\u4efd\u8bc1\u5df2\u8131\u654f]" in result

    def test_sanitize_no_pii_unchanged(self) -> None:
        text = "hello world no pii here"
        assert DataSanitizer.sanitize(text) == text

    def test_pii_patterns_contains_expected_keys(self) -> None:
        assert "id_card" in DataSanitizer.PII_PATTERNS
        assert "phone" in DataSanitizer.PII_PATTERNS
        assert "email" in DataSanitizer.PII_PATTERNS
        assert "api_key" in DataSanitizer.PII_PATTERNS


class TestDataSanitizerSanitizeDict:
    def test_sanitize_flat_dict(self) -> None:
        data = {"phone": "13812345678", "name": "Alice"}
        result = DataSanitizer.sanitize_dict(data)  # type: ignore[arg-type]
        assert result["phone"] == "[\u624b\u673a\u53f7\u5df2\u8131\u654f]"
        assert result["name"] == "Alice"

    def test_sanitize_nested_dict(self) -> None:
        data = {"user": {"email": "alice@example.com", "age": 30}}
        result = DataSanitizer.sanitize_dict(data)  # type: ignore[arg-type]
        assert result["user"]["email"] == "[\u90ae\u7bb1\u5df2\u8131\u654f]"
        assert result["user"]["age"] == 30

    def test_sanitize_dict_with_list(self) -> None:
        data = {"phones": ["13812345678", "13900001111"]}
        result = DataSanitizer.sanitize_dict(data)  # type: ignore[arg-type]
        assert result["phones"][0] == "[\u624b\u673a\u53f7\u5df2\u8131\u654f]"
        assert result["phones"][1] == "[\u624b\u673a\u53f7\u5df2\u8131\u654f]"

    def test_sanitize_deeply_nested(self) -> None:
        data = {"level1": {"level2": {"id_card": "110101199003076534"}}}
        result = DataSanitizer.sanitize_dict(data)  # type: ignore[arg-type]
        assert result["level1"]["level2"]["id_card"] == "[\u8eab\u4efd\u8bc1\u5df2\u8131\u654f]"

    def test_sanitize_dict_preserves_non_string_values(self) -> None:
        data = {"count": 42, "active": True, "ratio": 3.14, "name": None}
        result = DataSanitizer.sanitize_dict(data)  # type: ignore[arg-type]
        assert result["count"] == 42
        assert result["active"] is True
        assert result["ratio"] == 3.14
        assert result["name"] is None

    def test_sanitize_dict_with_api_key(self) -> None:
        data = {"api_key": "sk-abcdefghijklmnop", "service": "openai"}
        result = DataSanitizer.sanitize_dict(data)  # type: ignore[arg-type]
        assert result["api_key"] == "sk-abcd***"
        assert result["service"] == "openai"

    def test_sanitize_dict_mixed_nested_structure(self) -> None:
        data = {
            "user": {
                "contacts": [
                    {"type": "phone", "value": "13812345678"},
                    {"type": "email", "value": "test@example.com"},
                ],
                "api_key": "sk_live_secret123456789",
            },
            "metadata": {"count": 10},
        }
        result = DataSanitizer.sanitize_dict(data)  # type: ignore[arg-type]
        assert result["user"]["contacts"][0]["value"] == "[\u624b\u673a\u53f7\u5df2\u8131\u654f]"
        assert result["user"]["contacts"][1]["value"] == "[\u90ae\u7bb1\u5df2\u8131\u654f]"
        assert result["user"]["api_key"] == "sk_live_secr***"
        assert result["metadata"]["count"] == 10

    def test_sanitize_empty_dict(self) -> None:
        assert DataSanitizer.sanitize_dict({}) == {}  # type: ignore[arg-type]

    def test_sanitize_dict_does_not_mutate_original(self) -> None:
        data = {"phone": "13812345678"}
        original_data = data.copy()
        DataSanitizer.sanitize_dict(data)  # type: ignore[arg-type]
        assert data == original_data
