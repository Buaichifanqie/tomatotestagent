"""App account configuration loader.

Reads login credentials from configs/app_accounts.yaml for
automated login when test cases require logged_in state.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

_CONFIG_PATH = Path(__file__).resolve().parent.parent.parent / "configs" / "app_accounts.yaml"

_cache: dict[str, Any] | None = None


def _load_config() -> dict[str, Any]:
    """Load and cache the YAML config."""
    global _cache
    if _cache is not None:
        return _cache

    if not _CONFIG_PATH.exists():
        _cache = {}
        return _cache

    try:
        data = yaml.safe_load(_CONFIG_PATH.read_text(encoding="utf-8"))
        _cache = data if isinstance(data, dict) else {}
    except Exception:
        _cache = {}

    return _cache


def get_login_config(package: str) -> dict[str, str] | None:
    """Get login configuration for an app by package name.

    Args:
        package: Android app package name (e.g. "tv.danmaku.bili")

    Returns:
        Dict with keys: name, login_method, account, password, entry
        Returns None if no config found for this package.
    """
    config = _load_config()
    apps = config.get("apps", {})
    if not isinstance(apps, dict):
        return None

    app_config = apps.get(package)
    if not isinstance(app_config, dict):
        return None

    # Validate required fields
    required = {"login_method", "account", "password"}
    if not required.issubset(app_config.keys()):
        return None

    return {
        "name": app_config.get("name", package),
        "login_method": str(app_config["login_method"]),
        "account": str(app_config["account"]),
        "password": str(app_config["password"]),
        "entry": str(app_config.get("entry", "")),
    }


def has_login_config(package: str) -> bool:
    """Check if login config exists for the given package."""
    return get_login_config(package) is not None


def reload_config() -> None:
    """Force reload the config from disk (useful after edits)."""
    global _cache
    _cache = None
