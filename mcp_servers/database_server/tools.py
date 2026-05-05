from __future__ import annotations

from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine


async def _get_engine(database_url: str) -> AsyncEngine:
    return create_async_engine(database_url, pool_pre_ping=True)


async def db_query(
    database_url: str,
    sql: str,
    params: dict[str, object] | None = None,
    max_rows: int = 100,
) -> dict[str, Any]:
    engine = await _get_engine(database_url)
    try:
        async with engine.connect() as conn:
            stmt = text(sql)
            result = await conn.execute(stmt, parameters=params or {})

            if result.returns_rows:
                columns = list(result.keys())
                rows: list[dict[str, object]] = []
                for row in result.fetchmany(max_rows):
                    row_dict: dict[str, object] = {}
                    for idx, col in enumerate(columns):
                        val = row[idx]
                        if hasattr(val, "isoformat"):
                            val = val.isoformat()
                        row_dict[col] = val
                    rows.append(row_dict)

                return {
                    "success": True,
                    "columns": columns,
                    "rows": rows,
                    "row_count": len(rows),
                    "truncated": result.rowcount is not None and result.rowcount > max_rows,
                }
            else:
                await conn.commit()
                return {
                    "success": True,
                    "row_count": result.rowcount if result.rowcount is not None else 0,
                }
    except Exception as e:
        return {"error": str(e)}
    finally:
        await engine.dispose()


async def db_seed(
    database_url: str,
    table: str,
    data: list[dict[str, object]],
    truncate_first: bool = False,
) -> dict[str, Any]:
    engine = await _get_engine(database_url)
    try:
        async with engine.connect() as conn:
            if truncate_first:
                await conn.execute(text(f"TRUNCATE TABLE {table}"))

            if not data:
                return {"success": True, "inserted_count": 0}

            columns = list(data[0].keys())
            col_names = ", ".join(columns)
            placeholders = ", ".join([f":{col}" for col in columns])
            insert_sql = f"INSERT INTO {table} ({col_names}) VALUES ({placeholders})"

            total_inserted = 0
            for row in data:
                await conn.execute(text(insert_sql), parameters=row)
                total_inserted += 1

            await conn.commit()
            return {
                "success": True,
                "inserted_count": total_inserted,
                "table": table,
            }
    except Exception as e:
        return {"error": str(e)}
    finally:
        await engine.dispose()


async def db_cleanup(
    database_url: str,
    tables: list[str] | None = None,
    schema: str = "public",
) -> dict[str, Any]:
    engine = await _get_engine(database_url)
    try:
        async with engine.connect() as conn:
            if tables is not None:
                for table in tables:
                    await conn.execute(text(f"DELETE FROM {schema}.{table}"))
            else:
                result = await conn.execute(
                    text(
                        "SELECT tablename FROM pg_tables WHERE schemaname = :schema "
                        "UNION ALL "
                        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
                    ),
                    parameters={"schema": schema},
                )
                existing_tables = [row[0] for row in result]
                for table in existing_tables:
                    await conn.execute(text(f"DELETE FROM {table}"))

            await conn.commit()
            return {"success": True, "cleaned_tables": tables or ["all"]}
    except Exception as e:
        return {"error": str(e)}
    finally:
        await engine.dispose()
