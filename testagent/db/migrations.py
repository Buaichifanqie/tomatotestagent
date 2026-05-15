from __future__ import annotations

import asyncio
from pathlib import Path

from alembic import command
from alembic.config import Config

from testagent.common.errors import DatabaseError
from testagent.common.logging import get_logger
from testagent.config.settings import get_settings

logger = get_logger(__name__)

_ALEMBIC_DIR = Path(__file__).resolve().parent / "alembic"
_ALEMBIC_INI = Path(__file__).resolve().parent.parent.parent / "alembic.ini"


def _get_alembic_config(database_url: str | None = None) -> Config:
    if not _ALEMBIC_INI.exists():
        raise DatabaseError(
            f"alembic.ini not found at {_ALEMBIC_INI}",
            code="DB_ALEMBIC_INI_MISSING",
            details={"path": str(_ALEMBIC_INI)},
        )
    config = Config(str(_ALEMBIC_INI))
    config.set_main_option("script_location", str(_ALEMBIC_DIR))
    if database_url is None:
        settings = get_settings()
        database_url = settings.database_url
    config.set_main_option("sqlalchemy.url", database_url)
    return config


def upgrade_head(database_url: str | None = None) -> None:
    try:
        config = _get_alembic_config(database_url)
        command.upgrade(config, "head")
        logger.info("Database migrated to head successfully")
    except DatabaseError:
        raise
    except Exception as exc:
        raise DatabaseError(
            "Failed to upgrade database to head",
            code="DB_MIGRATION_UPGRADE_FAILED",
            details={"error": str(exc)},
        ) from exc


def downgrade(revision: str = "-1", database_url: str | None = None) -> None:
    try:
        config = _get_alembic_config(database_url)
        command.downgrade(config, revision)
        logger.info("Database downgraded to revision %s", revision)
    except DatabaseError:
        raise
    except Exception as exc:
        raise DatabaseError(
            f"Failed to downgrade database to revision {revision}",
            code="DB_MIGRATION_DOWNGRADE_FAILED",
            details={"revision": revision, "error": str(exc)},
        ) from exc


def generate_migration(message: str, database_url: str | None = None, autogenerate: bool = True) -> None:
    try:
        config = _get_alembic_config(database_url)
        command.revision(config, message=message, autogenerate=autogenerate)
        logger.info("Migration revision generated: %s", message)
    except DatabaseError:
        raise
    except Exception as exc:
        raise DatabaseError(
            f"Failed to generate migration: {message}",
            code="DB_MIGRATION_GENERATE_FAILED",
            details={"message": message, "error": str(exc)},
        ) from exc


def get_current_revision(database_url: str | None = None) -> str | None:
    try:
        config = _get_alembic_config(database_url)
        from alembic.script import ScriptDirectory

        script = ScriptDirectory.from_config(config)
        head = script.get_current_head()
        return head
    except Exception as exc:
        raise DatabaseError(
            "Failed to get current migration revision",
            code="DB_MIGRATION_REVISION_FAILED",
            details={"error": str(exc)},
        ) from exc


async def async_upgrade_head(database_url: str | None = None) -> None:
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, upgrade_head, database_url)


async def async_downgrade(revision: str = "-1", database_url: str | None = None) -> None:
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, downgrade, revision, database_url)
