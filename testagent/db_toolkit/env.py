from __future__ import annotations

import re

from testagent.db_toolkit.models import DbEnv, Environment

# Cache: (url, config_env) -> DbEnv
_cache: dict[tuple[str, str | None], DbEnv] = {}

# Keywords in the database name (path segment) that indicate test environment
_TEST_PATTERNS = re.compile(
    r"/(?:test|staging|dev|mock)[_\w.]*$",
    re.IGNORECASE,
)
_PROD_PATTERNS = re.compile(
    r"/(?:prod|production)[_\w.]*$",
    re.IGNORECASE,
)


def detect_environment(
    connection_url: str,
    config_env: str | None = None,
) -> DbEnv:
    """Detect whether a database connection targets a test or production environment.

    Priority:
    1. Explicit config_env parameter
    2. URL pattern matching (database name keywords)
    3. Default to PRODUCTION (safe-by-default)
    """
    cache_key = (connection_url, config_env)
    if cache_key in _cache:
        return _cache[cache_key]

    if config_env is not None:
        level = _parse_config_env(config_env)
        env = DbEnv(
            level=level,
            connection_url=connection_url,
            detected_by="config",
        )
    else:
        env = _detect_from_url(connection_url)

    _cache[cache_key] = env
    return env


def _parse_config_env(config_env: str) -> Environment:
    normalized = config_env.strip().lower()
    if normalized in ("test", "testing", "staging", "dev", "development"):
        return Environment.TEST
    return Environment.PRODUCTION


def _detect_from_url(connection_url: str) -> DbEnv:
    # Extract the database name from the URL path
    # Format: scheme://user:pass@host/dbname?params
    match = re.search(r"/([^/?]+)(?:\?|$)", connection_url)
    db_name = match.group(1) if match else ""

    if _TEST_PATTERNS.search(f"/{db_name}"):
        return DbEnv(
            level=Environment.TEST,
            connection_url=connection_url,
            detected_by="url_pattern",
        )

    if _PROD_PATTERNS.search(f"/{db_name}"):
        return DbEnv(
            level=Environment.PRODUCTION,
            connection_url=connection_url,
            detected_by="url_pattern",
        )

    return DbEnv(
        level=Environment.PRODUCTION,
        connection_url=connection_url,
        detected_by="default",
    )


def clear_cache() -> None:
    """Clear the environment cache. Used in tests."""
    _cache.clear()
