"""Tests for testagent.db_ops.connection — ConnectionManager."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from testagent.db_ops.connection import ConnectionManager, _mask_url


# ---------------------------------------------------------------------------
# _mask_url helper
# ---------------------------------------------------------------------------


class TestMaskUrl:
    def test_masks_password(self):
        url = "mysql+aiomysql://user:secret123@host:3306/db"
        masked = _mask_url(url)
        assert "secret123" not in masked
        assert "user:***@" in masked

    def test_no_password(self):
        url = "sqlite+aiosqlite:///tmp/test.db"
        masked = _mask_url(url)
        assert masked == url

    def test_complex_password(self):
        url = "postgresql+asyncpg://admin:p%40ss!w0rd@db.example.com/mydb"
        masked = _mask_url(url)
        assert "p%40ss!w0rd" not in masked
        assert "admin:***@" in masked


# ---------------------------------------------------------------------------
# ConnectionManager
# ---------------------------------------------------------------------------


class TestConnectionManagerNormalizeUrl:
    def test_mysql_shorthand(self):
        mgr = ConnectionManager()
        result = mgr._normalize_url("mysql://user:pass@host/db")
        assert result == "mysql+aiomysql://user:pass@host/db"

    def test_postgresql_shorthand(self):
        mgr = ConnectionManager()
        result = mgr._normalize_url("postgresql://user:pass@host/db")
        assert result == "postgresql+asyncpg://user:pass@host/db"

    def test_sqlite_shorthand(self):
        mgr = ConnectionManager()
        result = mgr._normalize_url("sqlite:///tmp/test.db")
        assert result == "sqlite+aiosqlite:///tmp/test.db"

    def test_already_full_url(self):
        mgr = ConnectionManager()
        url = "mysql+aiomysql://user:pass@host/db"
        assert mgr._normalize_url(url) == url

    def test_unknown_dialect_unchanged(self):
        mgr = ConnectionManager()
        url = "oracle://user:pass@host/db"
        assert mgr._normalize_url(url) == url


class TestConnectionManagerGetEngine:
    @pytest.mark.asyncio
    async def test_creates_engine_and_caches(self):
        mgr = ConnectionManager()
        mock_engine = MagicMock()

        with patch(
            "testagent.db_ops.connection.create_async_engine",
            return_value=mock_engine,
        ) as mock_create:
            engine1 = await mgr.get_engine("sqlite+aiosqlite:///tmp/test.db")
            engine2 = await mgr.get_engine("sqlite+aiosqlite:///tmp/test.db")

        assert engine1 is mock_engine
        assert engine2 is mock_engine
        mock_create.assert_called_once()  # cached, only created once

    @pytest.mark.asyncio
    async def test_different_urls_get_different_engines(self):
        mgr = ConnectionManager()
        engine_a = MagicMock(name="engine_a")
        engine_b = MagicMock(name="engine_b")

        with patch(
            "testagent.db_ops.connection.create_async_engine",
            side_effect=[engine_a, engine_b],
        ):
            e1 = await mgr.get_engine("sqlite+aiosqlite:///tmp/a.db")
            e2 = await mgr.get_engine("sqlite+aiosqlite:///tmp/b.db")

        assert e1 is not e2

    @pytest.mark.asyncio
    async def test_engine_creation_failure_raises_db_connection_error(self):
        mgr = ConnectionManager()

        with patch(
            "testagent.db_ops.connection.create_async_engine",
            side_effect=RuntimeError("driver not found"),
        ):
            with pytest.raises(Exception) as exc_info:
                await mgr.get_engine("mysql+aiomysql://user:pass@host/db")
            assert "ENGINE_CREATE_FAILED" in str(exc_info.value) or "Failed to create engine" in str(exc_info.value)


class TestConnectionManagerClose:
    @pytest.mark.asyncio
    async def test_close_specific_engine(self):
        mgr = ConnectionManager()
        mock_engine = AsyncMock()
        with patch(
            "testagent.db_ops.connection.create_async_engine",
            return_value=mock_engine,
        ):
            await mgr.get_engine("sqlite+aiosqlite:///tmp/test.db")
            await mgr.close("sqlite+aiosqlite:///tmp/test.db")

        mock_engine.dispose.assert_awaited_once()
        assert len(mgr._engines) == 0

    @pytest.mark.asyncio
    async def test_close_all_engines(self):
        mgr = ConnectionManager()
        engine_a = AsyncMock(name="a")
        engine_b = AsyncMock(name="b")

        with patch(
            "testagent.db_ops.connection.create_async_engine",
            side_effect=[engine_a, engine_b],
        ):
            await mgr.get_engine("sqlite+aiosqlite:///tmp/a.db")
            await mgr.get_engine("sqlite+aiosqlite:///tmp/b.db")
            await mgr.close()

        engine_a.dispose.assert_awaited_once()
        engine_b.dispose.assert_awaited_once()
        assert len(mgr._engines) == 0

    @pytest.mark.asyncio
    async def test_close_nonexistent_url_is_noop(self):
        mgr = ConnectionManager()
        # Should not raise
        await mgr.close("sqlite+aiosqlite:///tmp/nonexistent.db")

    @pytest.mark.asyncio
    async def test_close_none_closes_all(self):
        mgr = ConnectionManager()
        mock_engine = AsyncMock()
        with patch(
            "testagent.db_ops.connection.create_async_engine",
            return_value=mock_engine,
        ):
            await mgr.get_engine("sqlite+aiosqlite:///tmp/test.db")
            await mgr.close(None)

        mock_engine.dispose.assert_awaited_once()
        assert len(mgr._engines) == 0


class TestConnectionManagerTestConnection:
    @pytest.mark.asyncio
    async def test_test_connection_success(self):
        mgr = ConnectionManager()
        mock_engine = MagicMock()
        mock_engine.dialect.name = "sqlite"
        mock_engine.dialect.server_version_info = None

        mock_conn = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar.return_value = 1
        mock_conn.execute.return_value = mock_result
        mock_conn.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_conn.__aexit__ = AsyncMock(return_value=False)

        with patch(
            "testagent.db_ops.connection.create_async_engine",
            return_value=mock_engine,
        ):
            mock_engine.connect.return_value = mock_conn
            result = await mgr.test_connection("sqlite+aiosqlite:///tmp/test.db")

        assert result["connected"] is True
        assert result["dialect"] == "sqlite"

    @pytest.mark.asyncio
    async def test_test_connection_failure(self):
        mgr = ConnectionManager()
        mock_engine = MagicMock()
        mock_engine.dialect.name = "sqlite"

        mock_conn = AsyncMock()
        mock_conn.execute.side_effect = RuntimeError("connection refused")
        mock_conn.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_conn.__aexit__ = AsyncMock(return_value=False)

        with patch(
            "testagent.db_ops.connection.create_async_engine",
            return_value=mock_engine,
        ):
            mock_engine.connect.return_value = mock_conn
            with pytest.raises(Exception) as exc_info:
                await mgr.test_connection("sqlite+aiosqlite:///tmp/test.db")
            assert "CONNECTION_TEST_FAILED" in str(exc_info.value) or "Connection test failed" in str(exc_info.value)


class TestConnectionManagerGetConnection:
    @pytest.mark.asyncio
    async def test_get_connection_success(self):
        mgr = ConnectionManager()
        mock_engine = MagicMock()
        mock_conn = AsyncMock()
        mock_engine.connect = AsyncMock(return_value=mock_conn)

        with patch(
            "testagent.db_ops.connection.create_async_engine",
            return_value=mock_engine,
        ):
            conn = await mgr.get_connection("sqlite+aiosqlite:///tmp/test.db")

        assert conn is mock_conn

    @pytest.mark.asyncio
    async def test_get_connection_failure(self):
        mgr = ConnectionManager()
        mock_engine = MagicMock()

        with patch(
            "testagent.db_ops.connection.create_async_engine",
            return_value=mock_engine,
        ):
            mock_engine.connect.side_effect = RuntimeError("pool exhausted")
            with pytest.raises(Exception) as exc_info:
                await mgr.get_connection("sqlite+aiosqlite:///tmp/test.db")
            assert "CONNECTION_FAILED" in str(exc_info.value) or "Failed to connect" in str(exc_info.value)
