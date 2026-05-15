from __future__ import annotations

from typing import Any

RAG_COLLECTIONS: dict[str, dict[str, Any]] = {
    "req_docs": {"description": "需求文档 PRD", "access": ["planner"]},
    "api_docs": {"description": "OpenAPI/Swagger 规范", "access": ["planner", "executor"]},
    "defect_history": {"description": "历史缺陷", "access": ["planner", "analyzer"]},
    "test_reports": {"description": "历史测试报告", "access": ["analyzer"]},
    "locator_library": {"description": "UI 定位器库", "access": ["executor"]},
    "failure_patterns": {"description": "失败模式库", "access": ["analyzer"]},
}


class CollectionManager:
    def get_accessible_collections(self, agent_type: str) -> list[str]:
        accessible: list[str] = []
        for name, info in RAG_COLLECTIONS.items():
            access_list = info.get("access")
            if isinstance(access_list, list) and agent_type in access_list:
                accessible.append(name)
        return accessible

    def get_description(self, collection_name: str) -> str:
        info = RAG_COLLECTIONS.get(collection_name)
        if info is None:
            return ""
        desc = info.get("description", "")
        return str(desc) if desc is not None else ""
