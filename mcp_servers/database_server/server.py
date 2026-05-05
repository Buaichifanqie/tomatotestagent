from __future__ import annotations

import json
from typing import Any, ClassVar

from testagent.mcp_servers.base import BaseMCPServer
from testagent.mcp_servers.database_server.tools import db_cleanup, db_query, db_seed


class DatabaseMCPServer(BaseMCPServer):
    server_name = "database_server"

    _tools_spec: ClassVar[list[dict[str, object]]] = [
        {
            "name": "db_query",
            "description": "Execute SQL SELECT query, return {success, columns, rows, row_count, truncated}",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "database_url": {"type": "string", "description": "SQLAlchemy async database URL (e.g. sqlite+aiosqlite:///test.db)"},
                    "sql": {"type": "string", "description": "SQL query to execute (SELECT only)"},
                    "params": {"type": "object", "description": "Query parameters as key-value pairs"},
                    "max_rows": {"type": "integer", "description": "Maximum rows to return, default 100"},
                },
                "required": ["database_url", "sql"],
            },
        },
        {
            "name": "db_seed",
            "description": "Insert seed data into a table, return {success, inserted_count, table}",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "database_url": {"type": "string", "description": "SQLAlchemy async database URL"},
                    "table": {"type": "string", "description": "Target table name"},
                    "data": {
                        "type": "array",
                        "items": {"type": "object"},
                        "description": "List of row dicts to insert",
                    },
                    "truncate_first": {
                        "type": "boolean",
                        "description": "Truncate table before seeding, default false",
                    },
                },
                "required": ["database_url", "table", "data"],
            },
        },
        {
            "name": "db_cleanup",
            "description": "Clean up test data from database tables, return {success, cleaned_tables}",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "database_url": {"type": "string", "description": "SQLAlchemy async database URL"},
                    "tables": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Specific tables to clean (clears all if not specified)",
                    },
                    "schema": {"type": "string", "description": "Database schema name, default 'public'"},
                },
                "required": ["database_url"],
            },
        },
    ]

    _tool_registry: ClassVar[dict[str, Any]] = {
        "db_query": db_query,
        "db_seed": db_seed,
        "db_cleanup": db_cleanup,
    }

    async def list_tools(self) -> list[dict[str, object]]:
        return self._tools_spec

    async def call_tool(self, tool_name: str, arguments: dict[str, object]) -> object:
        tool = self._tool_registry.get(tool_name)
        if tool is None:
            return json.dumps({"error": f"Unknown tool: {tool_name}"})
        try:
            from inspect import iscoroutinefunction

            if iscoroutinefunction(tool):
                result = await tool(**arguments)
            else:
                result = tool(**arguments)
            if isinstance(result, (dict, list)):
                return json.dumps(result)
            return str(result)
        except Exception as e:
            return json.dumps({"error": str(e)})

    async def list_resources(self) -> list[dict[str, object]]:
        return [
            {
                "uri": "db://schema",
                "name": "Database Schema",
                "mimeType": "application/json",
                "description": "Database schema including tables, columns, types, and constraints",
            },
            {
                "uri": "db://data_dictionary",
                "name": "Data Dictionary",
                "mimeType": "application/json",
                "description": "Data dictionary with column descriptions, types, and relationships",
            },
        ]

    async def health_check(self) -> bool:
        return True
