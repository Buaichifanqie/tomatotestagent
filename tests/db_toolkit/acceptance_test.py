"""验收测试：用内存 SQLite 验证 db_toolkit 完整流程。

运行: python -m pytest tests/db_toolkit/acceptance_test.py -v -s
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

# 确保项目根目录在 path 中
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from testagent.db_toolkit.connection import ConnectionManager
from testagent.db_toolkit.env import clear_cache, detect_environment
from testagent.db_toolkit.models import DbEnv, Environment, SqlOpType
from testagent.db_toolkit.safety import SafetyGuard
from testagent.db_toolkit.schema import SchemaInspector
from testagent.db_toolkit.cleanup import CleanupTracker
from testagent.db_toolkit.tools import ToolkitState, handle_db_inspect, handle_db_query, handle_db_execute, handle_db_cleanup


DB_URL = "sqlite+aiosqlite://"  # 内存数据库


async def setup_test_db(conn_mgr: ConnectionManager):
    """创建测试表并插入一些数据。"""
    engine = await conn_mgr.get_engine(DB_URL)
    async with engine.connect() as conn:
        await conn.execute(__import__("sqlalchemy").text("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                email TEXT,
                is_test INTEGER DEFAULT 0
            )
        """))
        await conn.execute(__import__("sqlalchemy").text("""
            CREATE TABLE IF NOT EXISTS orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                amount REAL,
                status TEXT DEFAULT 'pending'
            )
        """))
        await conn.execute(__import__("sqlalchemy").text(
            "INSERT INTO users (name, email, is_test) VALUES ('alice', 'alice@test.com', 0)"
        ))
        await conn.execute(__import__("sqlalchemy").text(
            "INSERT INTO users (name, email, is_test) VALUES ('bob', 'bob@test.com', 0)"
        ))
        await conn.commit()
    print("  [OK] 测试数据库初始化完成")


async def test_environment_detection():
    """验收点 1: 环境检测"""
    clear_cache()

    # 内存 SQLite 无关键词，默认应该是 PRODUCTION
    env = detect_environment(DB_URL)
    assert env.level == Environment.PRODUCTION, f"Expected PRODUCTION, got {env.level}"
    print(f"  [OK] 环境检测: {env.level.value} (detected_by={env.detected_by})")

    # 带 config_env 覆盖
    env_test = detect_environment(DB_URL, config_env="test")
    assert env_test.level == Environment.TEST
    print(f"  [OK] 配置覆盖: {env_test.level.value} (detected_by={env_test.detected_by})")

    return env_test  # 返回 TEST 环境用于后续测试


async def test_safety_guard():
    """验收点 2: 安全守卫"""
    guard = SafetyGuard()

    prod_env = DbEnv(level=Environment.PRODUCTION, connection_url=DB_URL, detected_by="default")
    test_env = DbEnv(level=Environment.TEST, connection_url=DB_URL, detected_by="config")

    # PROD 只允许 SELECT
    guard.check(prod_env, SqlOpType.SELECT, "SELECT 1")
    print("  [OK] PROD 环境: SELECT 允许")

    try:
        guard.check(prod_env, SqlOpType.INSERT, "INSERT INTO users (name) VALUES ('x')")
        assert False, "Should have raised"
    except Exception as e:
        print(f"  [OK] PROD 环境: INSERT 被拦截 ({e.code})")

    # TEST 允许所有操作
    for op in SqlOpType:
        guard.check(test_env, op, f"{op.value} ...")
    print("  [OK] TEST 环境: 所有操作允许")

    # SQL 安全检查
    try:
        guard.check(test_env, SqlOpType.SELECT, "SELECT 1; DROP TABLE users")
        assert False
    except Exception:
        print("  [OK] 多语句 SQL 被拦截")

    try:
        guard.check(test_env, SqlOpType.SELECT, "SELECT * -- comment")
        assert False
    except Exception:
        print("  [OK] SQL 注释被拦截")


async def test_schema_inspection(conn_mgr: ConnectionManager):
    """验收点 3: 表结构查询"""
    inspector = SchemaInspector(conn_mgr)

    tables = await inspector.get_tables(DB_URL, dialect="sqlite")
    assert "users" in tables and "orders" in tables
    print(f"  [OK] 表列表: {tables}")

    columns = await inspector.get_columns(DB_URL, "users", dialect="sqlite")
    col_names = [c.name for c in columns]
    assert "id" in col_names and "name" in col_names
    print(f"  [OK] users 表列: {col_names}")

    sample = await inspector.get_sample_data(DB_URL, "users", limit=2)
    assert len(sample) == 2
    print(f"  [OK] 样本数据: {sample[0]}")

    full_schema = await inspector.get_full_schema(DB_URL)
    assert len(full_schema) == 2
    prompt = SchemaInspector.format_for_prompt(full_schema)
    assert "CREATE TABLE users" in prompt
    print(f"  [OK] Schema 格式化输出长度: {len(prompt)} 字符")


async def test_tool_db_inspect(state: ToolkitState):
    """验收点 4: db_inspect 工具"""
    result = await handle_db_inspect(state, {"connection_url": DB_URL})
    assert "tables" in result
    assert result["total_tables"] == 2
    assert result["environment"] == "test"
    print(f"  [OK] db_inspect: {result['total_tables']} 张表, 环境={result['environment']}")
    for t in result["tables"]:
        print(f"       - {t['name']}: {len(t['columns'])} 列, 样本={len(t.get('sample_data', []))} 行")


async def test_tool_db_query(state: ToolkitState):
    """验收点 5: db_query 工具"""
    result = await handle_db_query(state, {
        "connection_url": DB_URL,
        "sql": "SELECT * FROM users WHERE name = :name",
        "params": {"name": "alice"},
    })
    assert result["success"] is True
    assert len(result["data"]) == 1
    assert result["data"][0]["name"] == "alice"
    print(f"  [OK] db_query: 查询到 {result['rows_affected']} 行, 耗时 {result['duration_ms']}ms")


async def test_tool_db_execute(state: ToolkitState):
    """验收点 6: db_execute 工具（两步确认流程）"""
    # Step 1: 预览
    preview = await handle_db_execute(state, {
        "connection_url": DB_URL,
        "sql": "INSERT INTO users (name, email, is_test) VALUES (:name, :email, 1)",
        "params": {"name": "test_user_001", "email": "test@example.com"},
        "confirm": False,
    })
    assert preview["preview"] is True
    assert preview["op_type"] == "INSERT"
    print(f"  [OK] db_execute 预览: {preview['op_type']} - {preview['message']}")

    # Step 2: 确认执行
    result = await handle_db_execute(state, {
        "connection_url": DB_URL,
        "sql": "INSERT INTO users (name, email, is_test) VALUES (:name, :email, 1)",
        "params": {"name": "test_user_001", "email": "test@example.com"},
        "confirm": True,
    })
    assert result["success"] is True
    assert result["rows_affected"] == 1
    print(f"  [OK] db_execute 执行: {result['op_type']}, 影响 {result['rows_affected']} 行")

    # 验证数据已插入
    check = await handle_db_query(state, {
        "connection_url": DB_URL,
        "sql": "SELECT * FROM users WHERE name = :name",
        "params": {"name": "test_user_001"},
    })
    assert len(check["data"]) == 1
    print(f"  [OK] 验证插入: 查询到 test_user_001")


async def test_tool_db_cleanup(state: ToolkitState):
    """验收点 7: db_cleanup 工具"""
    # 检查有追踪记录
    records = state.cleanup_tracker.get_records()
    assert len(records) > 0
    print(f"  [OK] 清理追踪: {len(records)} 条记录待清理")

    # 执行清理
    result = await handle_db_cleanup(state, {"connection_url": DB_URL})
    assert result["cleaned"] > 0
    print(f"  [OK] db_cleanup: 清理了 {result['cleaned']}/{result['total']} 条操作")

    # 验证数据已删除
    check = await handle_db_query(state, {
        "connection_url": DB_URL,
        "sql": "SELECT * FROM users WHERE name = :name",
        "params": {"name": "test_user_001"},
    })
    assert len(check["data"]) == 0
    print(f"  [OK] 验证清理: test_user_001 已删除")


async def test_prod_blocks_write():
    """验收点 8: PROD 环境阻止写操作"""
    prod_env = DbEnv(level=Environment.PRODUCTION, connection_url=DB_URL, detected_by="default")
    prod_state = ToolkitState(env=prod_env, conn_manager=ConnectionManager())

    try:
        await handle_db_execute(prod_state, {
            "connection_url": DB_URL,
            "sql": "INSERT INTO users (name) VALUES ('hacker')",
            "confirm": True,
        })
        assert False, "Should have raised"
    except Exception as e:
        print(f"  [OK] PROD 写操作被拦截: {e.code}")

    # 但查询应该可以
    result = await handle_db_query(prod_state, {
        "connection_url": DB_URL,
        "sql": "SELECT * FROM users",
    })
    assert result["success"] is True
    print(f"  [OK] PROD 查询允许: {result['rows_affected']} 行")


async def main():
    print("\n" + "=" * 60)
    print("  DB Toolkit 验收测试")
    print("=" * 60)

    conn_mgr = ConnectionManager()

    print("\n[0] 初始化测试数据库...")
    await setup_test_db(conn_mgr)

    print("\n[1] 环境检测")
    test_env = await test_environment_detection()

    print("\n[2] 安全守卫")
    await test_safety_guard()

    print("\n[3] 表结构查询")
    await test_schema_inspection(conn_mgr)

    # 创建 TEST 环境的 ToolkitState
    state = ToolkitState(env=test_env, conn_manager=conn_mgr)

    print("\n[4] db_inspect 工具")
    await test_tool_db_inspect(state)

    print("\n[5] db_query 工具")
    await test_tool_db_query(state)

    print("\n[6] db_execute 工具（两步确认）")
    await test_tool_db_execute(state)

    print("\n[7] db_cleanup 工具")
    await test_tool_db_cleanup(state)

    print("\n[8] PROD 环境写操作拦截")
    await test_prod_blocks_write()

    # 清理
    await conn_mgr.close()

    print("\n" + "=" * 60)
    print("  所有验收测试通过!")
    print("=" * 60 + "\n")


if __name__ == "__main__":
    asyncio.run(main())
