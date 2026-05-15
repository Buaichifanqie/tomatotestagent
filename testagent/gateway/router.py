from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Body, Depends, HTTPException
from fastapi import Path as PathParam

from testagent.agent.quality_trends import QualityTrendsAnalyzer
from testagent.common import get_logger
from testagent.common.errors import MCPServerUnavailableError, TestAgentError
from testagent.gateway.session import SessionManager

if TYPE_CHECKING:
    from testagent.gateway.mcp_registry import MCPRegistry

_logger = get_logger(__name__)

_SKILLS_DIR = Path(__file__).resolve().parent.parent.parent / "skills"

_YAML_FRONT_MATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)

router = APIRouter()

_session_manager_instance: SessionManager | None = None
_registry_instance: Any = None


def _get_session_manager() -> SessionManager:
    global _session_manager_instance
    if _session_manager_instance is None:
        _session_manager_instance = SessionManager()
    return _session_manager_instance


def set_session_manager(manager: SessionManager) -> None:
    global _session_manager_instance
    _session_manager_instance = manager


def _get_mcp_registry() -> Any:
    global _registry_instance
    if _registry_instance is None:
        from testagent.config.settings import get_settings
        from testagent.gateway.mcp_registry import MCPRegistry

        _registry_instance = MCPRegistry(get_settings())
    return _registry_instance


def set_mcp_registry(registry: Any) -> None:
    global _registry_instance
    _registry_instance = registry


def _parse_skill_file(filepath: Path) -> dict[str, Any] | None:
    try:
        content = filepath.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None

    match = _YAML_FRONT_MATTER_RE.match(content)
    if not match:
        return None

    yaml_text = match.group(1)
    body = content[match.end() :].strip()

    try:
        import yaml

        metadata = yaml.safe_load(yaml_text)
    except Exception:
        return None

    if not isinstance(metadata, dict):
        return None

    return {
        "name": metadata.get("name", filepath.stem),
        "version": metadata.get("version", "0.1.0"),
        "description": metadata.get("description", ""),
        "trigger": metadata.get("trigger", ""),
        "required_mcp_servers": metadata.get("required_mcp_servers", []),
        "required_rag_collections": metadata.get("required_rag_collections", []),
        "body_preview": body[:500] if body else "",
    }


def _list_skill_files() -> list[Path]:
    if not _SKILLS_DIR.is_dir():
        return []
    return sorted(_SKILLS_DIR.glob("*.md"))


# --- Session endpoints ---


@router.post("/api/v1/sessions", status_code=201)
async def create_session(
    body: dict[str, object] = Body(...),
    session_manager: SessionManager = Depends(_get_session_manager),
) -> dict[str, Any]:
    skill_name = str(body.get("skill_name", body.get("name", "manual")))
    test_type = str(body.get("test_type", "api"))
    environment = str(body.get("environment", "dev"))
    session = await session_manager.create_session(
        name=skill_name,
        trigger_type="manual",
        input_context={
            "test_type": test_type,
            "skill_name": skill_name,
            "environment": environment,
        },
    )
    return session


@router.get("/api/v1/sessions")
async def list_sessions(
    page: int = 1,
    page_size: int = 20,
    status: str | None = None,
    session_manager: SessionManager = Depends(_get_session_manager),
) -> dict[str, Any]:
    all_sessions = await session_manager.list_sessions()
    if status:
        all_sessions = [s for s in all_sessions if s.get("status") == status]
    total = len(all_sessions)
    start = (page - 1) * page_size
    items = all_sessions[start : start + page_size]
    return {"items": items, "total": total, "page": page, "page_size": page_size}


@router.get("/api/v1/sessions/{session_id}")
async def get_session(
    session_id: str = PathParam(..., description="Test session ID"),
    session_manager: SessionManager = Depends(_get_session_manager),
) -> dict[str, Any]:
    session = await session_manager.get_session(session_id)
    return session


@router.get("/api/v1/sessions/{session_id}/plan")
async def get_session_plan(
    session_id: str = PathParam(...),
    session_manager: SessionManager = Depends(_get_session_manager),
) -> dict[str, Any]:
    await session_manager.get_session(session_id)
    return {"tasks": [], "strategy": "sequential", "session_id": session_id}


@router.get("/api/v1/sessions/{session_id}/plans")
async def get_session_plans(
    session_id: str = PathParam(...),
    session_manager: SessionManager = Depends(_get_session_manager),
) -> list[dict[str, Any]]:
    await session_manager.get_session(session_id)
    return []


@router.get("/api/v1/sessions/{session_id}/results")
async def get_session_results(
    session_id: str = PathParam(...),
    session_manager: SessionManager = Depends(_get_session_manager),
) -> dict[str, Any]:
    await session_manager.get_session(session_id)
    return {"items": [], "total": 0, "page": 1, "page_size": 20}


@router.get("/api/v1/results/{task_id}")
async def get_task_result(
    task_id: str = PathParam(...),
) -> dict[str, Any]:
    return {"id": task_id, "status": "unknown", "message": "Not yet implemented"}


@router.post("/api/v1/sessions/{session_id}/cancel")
async def cancel_session(
    session_id: str = PathParam(...),
    session_manager: SessionManager = Depends(_get_session_manager),
) -> dict[str, Any]:
    session = await session_manager.cancel_session(session_id)
    return session


# --- Skill endpoints ---


@router.get("/api/v1/skills")
async def list_skills() -> dict[str, Any]:
    skills: list[dict[str, Any]] = []
    for filepath in _list_skill_files():
        parsed = _parse_skill_file(filepath)
        if parsed is not None:
            skills.append(parsed)
    return {"items": skills, "total": len(skills)}


@router.get("/api/v1/skills/{skill_name}")
async def get_skill_detail(
    skill_name: str = PathParam(...),
) -> dict[str, Any]:
    for filepath in _list_skill_files():
        if filepath.stem == skill_name or filepath.stem.lower() == skill_name.lower():
            parsed = _parse_skill_file(filepath)
            if parsed is not None:
                return parsed

    raise HTTPException(
        status_code=404,
        detail={
            "code": "SKILL_NOT_FOUND",
            "message": f"Skill '{skill_name}' not found",
        },
    )


# --- MCP endpoints ---


@router.get("/api/v1/mcp/servers")
async def list_mcp_servers(
    registry: MCPRegistry = Depends(_get_mcp_registry),
) -> list[dict[str, Any]]:
    try:
        servers = await registry.list_servers()
        return [
            {
                "name": s.name,
                "status": s.status,
                "tools_count": len(s.tools),
                "resources_count": len(s.resources),
            }
            for s in servers
        ]
    except Exception as exc:
        from fastapi import HTTPException

        raise HTTPException(
            status_code=500,
            detail={
                "code": "MCP_LIST_FAILED",
                "message": f"Failed to list MCP servers: {exc}",
            },
        ) from exc


@router.post("/api/v1/mcp/servers", status_code=201)
async def register_mcp_server(
    body: dict[str, object] = Body(...),
    registry: MCPRegistry = Depends(_get_mcp_registry),
) -> dict[str, Any]:
    server_name = str(body.get("name", body.get("server_name", "")))
    command = str(body.get("command", ""))
    raw_args = body.get("args")
    raw_env = body.get("env")

    if not server_name or not command:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "INVALID_MCP_CONFIG",
                "message": "Both 'name' and 'command' are required",
            },
        )

    try:
        from testagent.models.mcp_config import MCPConfig

        config = MCPConfig(
            server_name=server_name,
            command=command,
            args=raw_args if isinstance(raw_args, dict) else {},
            env=raw_env if isinstance(raw_env, dict) else {},
        )
        info = await registry.register(config)
        return {
            "name": info.name,
            "status": info.status,
            "tools_count": len(info.tools),
        }
    except TestAgentError:
        raise
    except Exception as exc:
        from fastapi import HTTPException

        raise HTTPException(
            status_code=500,
            detail={
                "code": "MCP_REGISTER_FAILED",
                "message": f"Failed to register MCP server: {exc}",
            },
        ) from exc


@router.get("/api/v1/mcp/servers/{server_name}/health")
async def check_mcp_health(
    server_name: str = PathParam(...),
    registry: MCPRegistry = Depends(_get_mcp_registry),
) -> dict[str, Any]:
    try:
        info = await registry.lookup(server_name)
        return {
            "name": info.name,
            "status": info.status,
            "healthy": info.status == "healthy",
        }
    except MCPServerUnavailableError as exc:
        from fastapi import HTTPException

        raise HTTPException(
            status_code=404,
            detail={
                "code": exc.code,
                "message": exc.message,
            },
        ) from exc


# --- RAG endpoints ---


@router.post("/api/v1/rag/index")
async def trigger_rag_index(
    body: dict[str, object] = Body(...),
) -> dict[str, Any]:
    source = str(body.get("source", ""))
    collection = str(body.get("collection", ""))
    _logger.info(
        "RAG index triggered",
        extra={"extra_data": {"source": source, "collection": collection}},
    )
    return {
        "source": source,
        "collection": collection,
        "status": "queued",
        "message": "RAG indexing task has been queued",
    }


@router.post("/api/v1/rag/query")
async def rag_query(
    body: dict[str, object] = Body(...),
) -> list[dict[str, Any]]:
    query = str(body.get("query", ""))
    collections = body.get("collections", [])
    top_k = int(body.get("top_k", 5))
    collection_str = str(body.get("collection", "req_docs"))
    _logger.info(
        "RAG query received",
        extra={"extra_data": {"collection": collection_str, "top_k": top_k}},
    )
    return []


@router.get("/api/v1/rag")
async def list_rag_collections() -> list[dict[str, Any]]:
    return [
        {
            "id": "req_docs",
            "name": "需求文档",
            "type": "vector+fulltext",
            "document_count": 0,
        },
        {
            "id": "api_docs",
            "name": "API 文档",
            "type": "vector+fulltext",
            "document_count": 0,
        },
        {
            "id": "defect_history",
            "name": "历史缺陷",
            "type": "vector+fulltext+structured",
            "document_count": 0,
        },
        {
            "id": "test_reports",
            "name": "测试报告",
            "type": "vector+fulltext",
            "document_count": 0,
        },
        {
            "id": "locator_library",
            "name": "定位器库",
            "type": "vector+fulltext",
            "document_count": 0,
        },
        {
            "id": "failure_patterns",
            "name": "失败模式库",
            "type": "vector+structured",
            "document_count": 0,
        },
    ]


@router.delete("/api/v1/rag/{collection_id}", status_code=204)
async def delete_rag_collection(
    collection_id: str = PathParam(...),
) -> None:
    _logger.info(
        "RAG collection delete requested",
        extra={"extra_data": {"collection_id": collection_id}},
    )
    return None


# --- Defect endpoints ---


@router.get("/api/v1/defects")
async def list_defects(
    page: int = 1,
    page_size: int = 20,
    category: str | None = None,
    status: str | None = None,
) -> dict[str, Any]:
    return {"items": [], "total": 0, "page": page, "page_size": page_size}


@router.get("/api/v1/defects/{defect_id}")
async def get_defect(
    defect_id: str = PathParam(...),
) -> dict[str, Any]:
    raise HTTPException(
        status_code=404,
        detail={
            "code": "DEFECT_NOT_FOUND",
            "message": f"Defect '{defect_id}' not found",
        },
    )


@router.patch("/api/v1/defects/{defect_id}")
async def update_defect(
    defect_id: str = PathParam(...),
    body: dict[str, object] = Body(...),
) -> dict[str, Any]:
    raise HTTPException(
        status_code=404,
        detail={
            "code": "DEFECT_NOT_FOUND",
            "message": f"Defect '{defect_id}' not found",
        },
    )


# --- Dashboard endpoints ---


@router.get("/api/v1/dashboard/stats")
async def get_dashboard_stats(
    session_manager: SessionManager = Depends(_get_session_manager),
) -> dict[str, Any]:
    all_sessions = await session_manager.list_sessions()
    total_sessions = len(all_sessions)
    active_sessions = len(
        [s for s in all_sessions if s.get("status") not in ("completed", "failed", "cancelled")]
    )
    return {
        "total_sessions": total_sessions,
        "active_sessions": active_sessions,
        "total_tasks": 0,
        "pass_rate": 0.0,
        "defects_open": 0,
        "trends": [],
    }


@router.get("/api/v1/resources")
async def get_system_resources() -> dict[str, Any]:
    cpu_percent = 0.0
    memory = {"total": 0, "available": 0, "percent": 0.0}
    disk = {"total": 0, "used": 0, "free": 0, "percent": 0.0}
    try:
        import psutil
        cpu_percent = psutil.cpu_percent(interval=0.1)
        mem = psutil.virtual_memory()
        memory = {"total": mem.total, "available": mem.available, "percent": mem.percent}
        d = psutil.disk_usage("/")
        disk = {"total": d.total, "used": d.used, "free": d.free, "percent": d.percent}
    except ImportError:
        pass
    return {"cpu": cpu_percent, "memory": memory, "disk": disk}


# --- Report endpoint ---


@router.get("/api/v1/reports/{session_id}")
async def get_test_report(
    session_id: str = PathParam(...),
    session_manager: SessionManager = Depends(_get_session_manager),
) -> dict[str, Any]:
    session = await session_manager.get_session(session_id)
    return {
        "session": session,
        "plans": [],
        "summary": {
            "total_plans": 0,
            "total_tasks": 0,
            "passed": 0,
            "failed": 0,
            "flaky": 0,
            "skipped": 0,
        },
    }


# --- Quality Trends endpoints ---


_VALID_QUALITY_METRICS = frozenset({"pass_rate", "defect_density", "coverage", "flaky_rate"})


async def _get_quality_analyzer() -> QualityTrendsAnalyzer:
    from testagent.db.engine import get_session
    from testagent.db.repository import DefectRepository, SessionRepository

    async with get_session() as session:
        session_repo = SessionRepository(session)
        defect_repo = DefectRepository(session)
        return QualityTrendsAnalyzer(session_repo=session_repo, defect_repo=defect_repo)


@router.get("/api/v1/quality/trends")
async def get_quality_trends(
    metric: str = "pass_rate",
    days: int = 30,
) -> dict[str, Any]:
    if metric not in _VALID_QUALITY_METRICS:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "INVALID_METRIC",
                "message": f"Invalid metric '{metric}'. Valid metrics: {', '.join(sorted(_VALID_QUALITY_METRICS))}",
            },
        )
    try:
        analyzer = await _get_quality_analyzer()
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail={
                "code": "ANALYZER_INIT_FAILED",
                "message": f"Failed to initialize quality analyzer: {exc}",
            },
        ) from exc

    try:
        if metric == "pass_rate":
            data = await analyzer.get_pass_rate_trend(days=days)
        elif metric == "defect_density":
            data = await analyzer.get_defect_density_trend(days=days)
        elif metric == "coverage":
            data = await analyzer.get_coverage_trend(days=days)
        elif metric == "flaky_rate":
            data = await analyzer.get_flaky_rate_trend(days=days)
        else:
            data = []

        return {"metric": metric, "days": days, "trends": data}
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail={
                "code": "TREND_QUERY_FAILED",
                "message": f"Failed to query {metric} trend: {exc}",
            },
        ) from exc


@router.get("/api/v1/quality/summary")
async def get_quality_summary(
    session_manager: SessionManager = Depends(_get_session_manager),
) -> dict[str, Any]:
    try:
        analyzer = await _get_quality_analyzer()
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail={
                "code": "ANALYZER_INIT_FAILED",
                "message": f"Failed to initialize quality analyzer: {exc}",
            },
        ) from exc

    try:
        summary = await analyzer.get_summary()

        await _broadcast_quality_update(session_manager, summary)

        return summary
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail={
                "code": "SUMMARY_QUERY_FAILED",
                "message": f"Failed to get quality summary: {exc}",
            },
        ) from exc


async def _broadcast_quality_update(session_manager: SessionManager, summary: dict[str, Any]) -> None:
    try:
        active = await session_manager.get_active_sessions()
        for s in active:
            sid = s.get("id", "")
            if sid:
                await session_manager.publish_event(
                    session_id=sid,
                    event="quality.trend_update",
                    data={"summary": summary},
                )
    except Exception as exc:
        _logger.warning(
            "Failed to broadcast quality trend update",
            extra={"extra_data": {"error": str(exc)}},
        )


# --- Health endpoint ---


@router.get("/health")
async def health_check() -> dict[str, str]:
    return {"status": "healthy"}
