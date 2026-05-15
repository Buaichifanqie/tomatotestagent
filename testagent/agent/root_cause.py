from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from testagent.common.errors import TestAgentError
from testagent.common.logging import get_logger

if TYPE_CHECKING:
    from testagent.llm.base import ILLMProvider
    from testagent.mcp_servers.git_server.server import GitMCPServer
    from testagent.models.result import TestResult
    from testagent.rag.pipeline import RAGPipeline

logger = get_logger(__name__)

ROOT_CAUSE_TYPES = (
    "code_change",
    "config_change",
    "env_issue",
    "data_issue",
    "unknown",
)

FAILURE_PATTERNS_COLLECTION = "failure_patterns"
DEFECT_HISTORY_COLLECTION = "defect_history"


class RootCauseAnalysisError(TestAgentError):
    pass


def _get_defect_id(defect: Any) -> str:
    if isinstance(defect, dict):
        raw_id: object = defect.get("id", "")
        return "" if raw_id is None else str(raw_id)
    raw_attr: object = getattr(defect, "id", "")
    return "" if raw_attr is None else str(raw_attr)


def _get_defect_field(defect: Any, field: str, default: Any = "") -> Any:
    if isinstance(defect, dict):
        return defect.get(field, default)
    return getattr(defect, field, default)


@dataclass
class RootCauseResult:
    defect_id: str
    root_cause_type: str
    confidence: float
    related_commits: list[dict[str, Any]] = field(default_factory=list)
    related_prs: list[dict[str, Any]] = field(default_factory=list)
    code_snippets: list[dict[str, Any]] = field(default_factory=list)
    suggestion: str = ""
    similar_historical_defects: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "defect_id": self.defect_id,
            "root_cause_type": self.root_cause_type,
            "confidence": self.confidence,
            "related_commits": self.related_commits,
            "related_prs": self.related_prs,
            "code_snippets": self.code_snippets,
            "suggestion": self.suggestion,
            "similar_historical_defects": self.similar_historical_defects,
        }


class RootCauseAnalyzer:
    def __init__(
        self,
        git_server: GitMCPServer,
        llm: ILLMProvider,
        rag: RAGPipeline,
        repo_path: str = "",
    ) -> None:
        self._git_server = git_server
        self._llm = llm
        self._rag = rag
        self._repo_path = repo_path

    async def analyze(self, defect: Any, test_result: TestResult) -> RootCauseResult:
        error_info = self._extract_error_info(test_result)
        file_path, line_number = self._extract_file_location(error_info, test_result)

        related_commits: list[dict[str, Any]] = []
        code_snippets: list[dict[str, Any]] = []

        if self._repo_path and file_path:
            related_commits = await self._get_related_commits(file_path, line_number)
            code_snippets = await self._get_code_snippets(file_path, line_number)

        similar_defects = await self._query_similar_defects(error_info)

        root_cause = await self._llm_analyze(
            error_info=error_info,
            related_commits=related_commits,
            code_snippets=code_snippets,
            similar_defects=similar_defects,
        )

        result = RootCauseResult(
            defect_id=_get_defect_id(defect),
            root_cause_type=root_cause.get("root_cause_type", "unknown"),
            confidence=float(root_cause.get("confidence", 0.0)),
            related_commits=related_commits,
            related_prs=[],
            code_snippets=code_snippets,
            suggestion=root_cause.get("suggestion", ""),
            similar_historical_defects=similar_defects,
        )

        await self._write_back_to_rag(
            defect=defect,
            test_result=test_result,
            result=result,
        )

        return result

    def _extract_error_info(self, test_result: TestResult) -> str:
        parts: list[str] = []

        if test_result.logs:
            log_lines = test_result.logs.strip().split("\n")
            tail_lines = log_lines[-min(len(log_lines), 200):]
            parts.append("=== Test Logs (tail) ===")
            parts.extend(tail_lines)

        if test_result.assertion_results:
            parts.append("=== Assertion Results ===")
            parts.append(json.dumps(test_result.assertion_results, ensure_ascii=False, default=str, indent=2))

        if test_result.artifacts:
            error_keywords = ("error", "exception", "traceback", "fail")
            error_fields = {
                k: v
                for k, v in test_result.artifacts.items()
                if any(kw in k.lower() for kw in error_keywords)
            }
            if error_fields:
                parts.append("=== Error Artifacts ===")
                parts.append(json.dumps(error_fields, ensure_ascii=False, default=str, indent=2))

        return "\n".join(parts) if parts else "No error information available"

    def _extract_file_location(self, error_info: str, test_result: TestResult) -> tuple[str | None, int | None]:
        file_path: str | None = None
        line_number: int | None = None

        if test_result.artifacts:
            source_file = test_result.artifacts.get("source_file")
            if isinstance(source_file, str) and source_file.strip():
                file_path = source_file.strip()
            source_line = test_result.artifacts.get("source_line")
            if isinstance(source_line, (int, str)):
                try:
                    line_number = int(source_line)
                except (ValueError, TypeError):
                    line_number = None

        if file_path:
            return file_path, line_number

        import re
        file_patterns = re.findall(
            r'(?:File|file)["\s]*:?["\s]*([a-zA-Z0-9_/\\\-]+\.(?:py|js|ts|java|go|rs|kt|swift))',
            error_info,
        )
        if file_patterns:
            file_path = file_patterns[0]
            line_match = re.search(r'line\s+(\d+)', error_info)
            if line_match:
                line_number = int(line_match.group(1))

        return file_path, line_number

    async def _get_related_commits(self, file_path: str, line_number: int | None) -> list[dict[str, Any]]:
        if not self._repo_path:
            return []

        commits: list[dict[str, Any]] = []

        if line_number is not None:
            blame_result = await self._git_server.call_tool("git_blame", {
                "repo_path": self._repo_path,
                "file_path": file_path,
                "start_line": line_number,
                "end_line": line_number + 5,
            })
            if isinstance(blame_result, str):
                try:
                    blame_data = json.loads(blame_result)
                except json.JSONDecodeError:
                    blame_data = {"output": blame_result}

            if isinstance(blame_data, dict) and blame_data.get("output"):
                commits.append({
                    "file": file_path,
                    "line": line_number,
                    "blame_info": str(blame_data["output"]),
                })

        log_result = await self._git_server.call_tool("git_log", {
            "repo_path": self._repo_path,
            "file_path": file_path,
            "max_count": 5,
        })
        if isinstance(log_result, str):
            try:
                log_data = json.loads(log_result)
            except json.JSONDecodeError:
                log_data = {"output": log_result}

        if isinstance(log_data, dict) and log_data.get("output"):
            commits.append({
                "file": file_path,
                "recent_commits": str(log_data["output"]),
            })

        return commits

    async def _get_code_snippets(self, file_path: str, line_number: int | None) -> list[dict[str, Any]]:
        if not self._repo_path:
            return []

        diff_result = await self._git_server.call_tool("git_diff", {
            "repo_path": self._repo_path,
            "path": file_path,
            "commit_a": "HEAD~1",
        })
        if isinstance(diff_result, str):
            try:
                diff_data = json.loads(diff_result)
            except json.JSONDecodeError:
                diff_data = {"output": diff_result}
        elif isinstance(diff_result, dict):
            diff_data = diff_result
        else:
            return []

        if isinstance(diff_data, dict) and diff_data.get("output"):
            return [{"file": file_path, "diff": str(diff_data["output"])}]

        return []

    async def _query_similar_defects(self, error_info: str) -> list[dict[str, Any]]:
        try:
            results = await self._rag.query(
                query_text=error_info[:1000],
                collection=DEFECT_HISTORY_COLLECTION,
                top_k=3,
            )
            return [
                {
                    "doc_id": r.doc_id,
                    "content": r.content[:500],
                    "score": r.score,
                    "metadata": r.metadata,
                }
                for r in results
            ]
        except Exception as exc:
            logger.warning(
                "Failed to query similar defects from RAG",
                extra={"extra_data": {"error": str(exc)}},
            )
            return []

    async def _llm_analyze(
        self,
        error_info: str,
        related_commits: list[dict[str, Any]],
        code_snippets: list[dict[str, Any]],
        similar_defects: list[dict[str, Any]],
    ) -> dict[str, Any]:
        system_prompt = """You are a root cause analysis expert for automated testing.
Analyze the provided error information, code changes, and historical defect patterns.
Determine the root cause type and provide a fix suggestion.

Root cause types:
- code_change: A recent code change introduced the failure
- config_change: A configuration or environment change caused the failure
- env_issue: The test environment itself is broken or misconfigured
- data_issue: Test data or test fixtures are incorrect or missing
- unknown: Cannot determine the root cause

Respond with a JSON object containing:
{
  "root_cause_type": "code_change|config_change|env_issue|data_issue|unknown",
  "confidence": 0.0-1.0,
  "reasoning": "brief explanation of the analysis",
  "suggestion": "actionable fix suggestion"
}"""

        user_parts: list[str] = ["=== Error Information ===\n" + (error_info[:3000] if error_info else "N/A")]

        if related_commits:
            user_parts.append("=== Related Commits ===")
            user_parts.append(json.dumps(related_commits, ensure_ascii=False, default=str, indent=2))

        if code_snippets:
            user_parts.append("=== Code Changes ===")
            user_parts.append(json.dumps(code_snippets, ensure_ascii=False, default=str, indent=2))

        if similar_defects:
            user_parts.append("=== Similar Historical Defects ===")
            user_parts.append(json.dumps(similar_defects, ensure_ascii=False, default=str, indent=2))

        user_prompt = "\n\n".join(user_parts)

        try:
            response = await self._llm.chat(
                system=system_prompt,
                messages=[{"role": "user", "content": user_prompt}],
                max_tokens=1024,
                temperature=0.3,
            )

            for block in response.content:
                if isinstance(block, dict) and block.get("type") == "text":
                    text = str(block.get("text", ""))
                    try:
                        parsed = json.loads(text)
                        if isinstance(parsed, dict):
                            return parsed
                    except (json.JSONDecodeError, ValueError):
                        import re
                        json_match = re.search(r'\{[^{}]*\}', text, re.DOTALL)
                        if json_match:
                            try:
                                parsed = json.loads(json_match.group())
                                if isinstance(parsed, dict):
                                    return parsed
                            except (json.JSONDecodeError, ValueError):
                                pass
                        return {
                            "root_cause_type": "unknown",
                            "confidence": 0.0,
                            "reasoning": text[:500],
                            "suggestion": "Review the error details and code changes manually.",
                        }

        except Exception as exc:
            logger.warning(
                "LLM analysis failed, falling back to default",
                extra={"extra_data": {"error": str(exc)}},
            )

        return {
            "root_cause_type": "unknown",
            "confidence": 0.0,
            "reasoning": "LLM analysis unavailable",
            "suggestion": "Review the error details and code changes manually.",
        }

    async def _write_back_to_rag(
        self,
        defect: Any,
        test_result: TestResult,
        result: RootCauseResult,
    ) -> None:
        defect_id = _get_defect_id(defect)
        try:
            write_back_content = json.dumps(
                {
                    "defect_id": defect_id,
                    "defect_title": _get_defect_field(defect, "title", ""),
                    "defect_category": _get_defect_field(defect, "category", "unknown"),
                    "defect_severity": _get_defect_field(defect, "severity", "minor"),
                    "root_cause_type": result.root_cause_type,
                    "confidence": result.confidence,
                    "suggestion": result.suggestion,
                    "error_summary": (test_result.logs or "")[:500],
                },
                ensure_ascii=False,
                default=str,
            )

            metadata: dict[str, Any] = {
                "defect_id": defect_id,
                "defect_category": _get_defect_field(defect, "category", "unknown"),
                "defect_severity": _get_defect_field(defect, "severity", "minor"),
                "root_cause_type": result.root_cause_type,
                "confidence": result.confidence,
                "analyzed_at": datetime.now(UTC).isoformat(),
            }

            await self._rag.write_back(
                content=write_back_content,
                collection=FAILURE_PATTERNS_COLLECTION,
                metadata=metadata,
            )

            logger.info(
                "Root cause analysis written back to RAG failure_patterns",
                extra={
                    "extra_data": {
                        "defect_id": defect_id,
                        "root_cause_type": result.root_cause_type,
                        "collection": FAILURE_PATTERNS_COLLECTION,
                    }
                },
            )
        except Exception as exc:
            logger.warning(
                "Failed to write back root cause analysis to RAG",
                extra={"extra_data": {"defect_id": defect_id, "error": str(exc)}},
            )
