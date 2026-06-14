from __future__ import annotations

import pytest

from testagent.db_toolkit.connection import ConnectionManager, _mask_url


class TestMaskUrl:
    def test_masks_password(self):
        assert _mask_url("mysql://user:secret@host/db") == "mysql://user:***@host/db"

    def test_no_password(self):
        assert _mask_url("sqlite:///test.db") == "sqlite:///test.db"


class TestNormalizeUrl:
    def test_mysql_short(self):
        mgr = ConnectionManager()
        assert mgr._normalize_url("mysql://host/db") == "mysql+aiomysql://host/db"

    def test_postgresql_short(self):
        mgr = ConnectionManager()
        assert mgr._normalize_url("postgresql://host/db") == "postgresql+asyncpg://host/db"

    def test_sqlite_short(self):
        mgr = ConnectionManager()
        assert mgr._normalize_url("sqlite:///test.db") == "sqlite+aiosqlite:///test.db"

    def test_already_full_driver(self):
        mgr = ConnectionManager()
        assert mgr._normalize_url("mysql+aiomysql://host/db") == "mysql+aiomysql://host/db"


class TestConnectionManager:
    def test_engine_caching(self):
        mgr = ConnectionManager()
        url = "sqlite+aiosqlite://"
        e1 = mgr._create_engine(url)
        e2 = mgr._create_engine(url)
        assert e1 is e2

    @pytest.mark.asyncio
    async def test_close_all(self):
        mgr = ConnectionManager()
        url = "sqlite+aiosqlite://"
        mgr._create_engine(url)
        assert len(mgr._engines) == 1
        await mgr.close()
        assert len(mgr._engines) == 0
