from __future__ import annotations

import uuid
from typing import Any

from pydantic import BaseModel, Field

from testagent.common.logging import get_logger

logger = get_logger(__name__)

_VALID_STATUSES: frozenset[str] = frozenset({"pending", "in_progress", "completed"})


class TodoItem(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    content: str
    status: str = "pending"
    priority: int = 0


class TodoManager:
    """Agent 任务追踪器——在 Agent Loop 中追踪当前任务进度"""

    def __init__(self) -> None:
        self._items: dict[str, TodoItem] = {}

    def add(self, content: str, priority: int = 0) -> str:
        item = TodoItem(content=content, priority=priority)
        self._items[item.id] = item
        logger.debug(
            "TodoItem added",
            extra={"extra_data": {"todo_id": item.id, "content": content, "priority": priority}},
        )
        return item.id

    def update(self, todo_id: str, status: str) -> None:
        if todo_id not in self._items:
            logger.warning(
                "TodoItem not found for update",
                extra={"extra_data": {"todo_id": todo_id}},
            )
            return
        if status not in _VALID_STATUSES:
            logger.warning(
                "Invalid TodoItem status",
                extra={"extra_data": {"todo_id": todo_id, "status": status}},
            )
            return
        self._items[todo_id].status = status
        logger.debug(
            "TodoItem updated",
            extra={"extra_data": {"todo_id": todo_id, "status": status}},
        )

    def get_pending(self) -> list[TodoItem]:
        return [item for item in self._items.values() if item.status == "pending"]

    def get_next(self) -> TodoItem | None:
        pending = self.get_pending()
        if not pending:
            return None
        return max(pending, key=lambda item: item.priority)

    def format_for_prompt(self) -> str:
        if not self._items:
            return "No tasks tracked."
        lines: list[str] = ["# Current Task Progress", ""]
        sorted_items = sorted(
            self._items.values(),
            key=lambda item: (-item.priority, item.id),
        )
        for item in sorted_items:
            status_marker = {
                "completed": "[x]",
                "in_progress": "[~]",
                "pending": "[ ]",
            }.get(item.status, "[?]")
            lines.append(f"- {status_marker} (P{item.priority}) {item.content}")
        return "\n".join(lines)

    def to_dict(self) -> dict[str, Any]:
        return {
            "items": [item.model_dump() for item in self._items.values()],
        }
