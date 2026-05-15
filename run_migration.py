import asyncio

from testagent.db.migrate_sqlite_to_pg import run_migration


async def main():
    print("=" * 60)
    print("  SQLite → PostgreSQL 数据迁移")
    print("=" * 60)

    stats = await run_migration(
        sqlite_path="./testagent.db",
        pg_user="testagent",
        pg_password="testagent",
        pg_host="localhost",
        pg_port=5432,
        pg_db="testagent",
    )

    print()
    print(f"{'表名':<20} {'源行数':<10} {'目标行数':<10} {'耗时':<8} {'状态'}")
    print("-" * 60)
    for s in stats:
        status = "[OK]" if s.completed else "[FAIL]"
        print(f"{s.table_name:<20} {s.source_count:<10} {s.target_count:<10} {s.duration_ms:<8.0f}ms {status}")

    all_done = all(s.completed for s in stats)
    print()
    print(f"{'[OK] 迁移完成' if all_done else '[ERROR] 迁移有失败的表'}")
    print()


asyncio.run(main())
