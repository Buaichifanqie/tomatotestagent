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
    name: str = Body(..., embed=True),
    trigger_type: str = Body("manual", embed=True),
    input_context: dict[str, object] | None = Body(None, embed=True),
    session_manager: SessionManager = Depends(_get_session_manager),
) -> dict[str, Any]:
    session = await session_manager.create_session(
        name=name,
        trigger_type=trigger_type,
        input_context=input_context,
    )
    return {"data": session}


@router.get("/api/v1/sessions/{session_id}")
async def get_session(
    session_id: str = PathParam(..., description="Test session ID"),
    session_manager: SessionManager = Depends(_get_session_manager),
) -> dict[str, Any]:
    session = await session_manager.get_session(session_id)
    return {"data": session}


@router.get("/api/v1/sessions/{session_id}/plans")
async def get_session_plans(
    session_id: str = PathParam(...),
    session_manager: SessionManager = Depends(_get_session_manager),
) -> dict[str, Any]:
    await session_manager.get_session(session_id)
    return {"data": []}


@router.get("/api/v1/sessions/{session_id}/results")
async def get_session_results(
    session_id: str = PathParam(...),
    session_manager: SessionManager = Depends(_get_session_manager),
) -> dict[str, Any]:
    await session_manager.get_session(session_id)
    return {"data": []}


@router.post("/api/v1/sessions/{session_id}/cancel")
async def cancel_session(
    session_id: str = PathParam(...),
    session_manager: SessionManager = Depends(_get_session_manager),
) -> dict[str, Any]:
    session = await session_manager.cancel_session(session_id)
    return {"data": session}


# --- Skill endpoints ---


@router.get("/api/v1/skills")
async def list_skills() -> dict[str, Any]:
    skills: list[dict[str, Any]] = []
    for filepath in _list_skill_files():
        parsed = _parse_skill_file(filepath)
        if parsed is not None:
            skills.append(parsed)
    return {"data": skills}


@router.get("/api/v1/skills/{skill_name}")
async def get_skill_detail(
    skill_name: str = PathParam(...),
) -> dict[str, Any]:
    for filepath in _list_skill_files():
        if filepath.stem == skill_name or filepath.stem.lower() == skill_name.lower():
            parsed = _parse_skill_file(filepath)
            if parsed is not None:
                return {"data": parsed}

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
) -> dict[str, Any]:
    try:
        servers = await registry.list_servers()
        return {
            "data": [
                {
                    "name": s.name,
                    "status": s.status,
                    "tools_count": len(s.tools),
                    "resources_count": len(s.resources),
                }
                for s in servers
            ]
        }
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
    server_name: str = Body(..., embed=True),
    command: str = Body(..., embed=True),
    args: dict[str, object] | None = Body(None, embed=True),
    env: dict[str, object] | None = Body(None, embed=True),
    registry: MCPRegistry = Depends(_get_mcp_registry),
) -> dict[str, Any]:
    try:
        from testagent.models.mcp_config import MCPConfig

        config = MCPConfig(
            server_name=server_name,
            command=command,
            args=args,
            env=env,
        )
        info = await registry.register(config)
        return {
            "data": {
                "name": info.name,
                "status": info.status,
                "tools_count": len(info.tools),
            }
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
            "data": {
                "name": info.name,
                "status": info.status,
                "healthy": info.status == "healthy",
            }
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
    source: str = Body(..., embed=True),
    collection: str = Body(..., embed=True),
) -> dict[str, Any]:
    _logger.info(
        "RAG index triggered",
        extra={"extra_data": {"source": source, "collection": collection}},
    )
    return {
        "data": {
            "source": source,
            "collection": collection,
            "status": "queued",
            "message": "RAG indexing task has been queued",
        }
    }


@router.post("/api/v1/rag/query")
async def rag_query(
    query: str = Body(..., embed=True),
    collection: str = Body("req_docs", embed=True),
    top_k: int = Body(5, embed=True),
) -> dict[str, Any]:
    _logger.info(
        "RAG query received",
        extra={"extra_data": {"collection": collection, "top_k": top_k}},
    )
    return {
        "data": {
            "query": query,
            "collection": collection,
            "results": [],
            "total": 0,
        }
    }


# --- Report endpoint ---


@router.get("/api/v1/reports/{session_id}")
async def get_test_report(
    session_id: str = PathParam(...),
    session_manager: SessionManager = Depends(_get_session_manager),
) -> dict[str, Any]:
    session = await session_manager.get_session(session_id)
    return {
        "data": {
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

        return {"data": {"metric": metric, "days": days, "trends": data}}
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

        return {"data": summary}
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
