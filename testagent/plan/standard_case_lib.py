"""标准用例库（P0 核心用例持久化）。

将用户确认过的 P0/P1 核心用例保存为标准库，
后续生成时优先加载，LLM 只做补充生成。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from testagent.plan.models import TestCase


def _default_lib_path() -> Path:
    for c in [Path.cwd(), Path(__file__).parent.parent.parent]:
        if (c / "pyproject.toml").exists():
            return c / "standard_cases"
    return Path.cwd() / "standard_cases"


class StandardCaseLib:
    """P0/P1 核心用例标准库。

    每次生成测试用例前加载标准库，
    确保核心场景不因 LLM 概率波动而遗漏。
    """

    def __init__(self, lib_root: str = "") -> None:
        self._root = Path(lib_root) if lib_root else _default_lib_path()
        self._root.mkdir(parents=True, exist_ok=True)

    def _app_dir(self, app_name: str, module: str = "") -> Path:
        """Get directory for an app's standard cases."""
        parts = [self._root]
        if app_name:
            parts.append(app_name.lower().replace(" ", "_"))
        if module:
            parts.append(module.lower().replace(" ", "_").replace("-", "_"))
        d = Path(*parts)
        d.mkdir(parents=True, exist_ok=True)
        return d

    def save(self, tc: TestCase, app_name: str = "", module: str = "") -> Path:
        """Save a single test case to the standard library.

        Only P0/P1 cases should be saved as standard.
        """
        app = app_name or "unknown"
        tc_dir = self._app_dir(app, module)
        fp = tc_dir / f"{tc.id}.json"
        data = {
            "id": tc.id,
            "title": tc.title,
            "priority": tc.priority,
            "is_core": tc.is_core,
            "feature_id": tc.feature_id,
            "prerequisites": tc.prerequisites,
            "expected_outcome": tc.expected_outcome,
            "steps": [s.model_dump() for s in tc.steps],
        }
        fp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        return fp

    def load_all(self, app_name: str = "", module: str = "") -> list[TestCase]:
        """Load all standard cases for an app/module.

        Returns:
            List of TestCase objects.
        """
        cases: list[TestCase] = []
        search_root = self._app_dir(app_name, module) if app_name else self._root
        for f in sorted(search_root.rglob("*.json")):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                tc = TestCase(
                    id=data["id"],
                    title=data["title"],
                    priority=data.get("priority", "P1"),
                    is_core=data.get("is_core", False),
                    feature_id=data.get("feature_id", ""),
                    prerequisites=data.get("prerequisites", []),
                    expected_outcome=data.get("expected_outcome", ""),
                    steps=[TestStep(**s) for s in data.get("steps", [])],
                )
                cases.append(tc)
            except Exception:
                continue
        return cases

    def list_cases(self, app_name: str = "") -> list[dict[str, Any]]:
        """List all standard cases with metadata."""
        cases: list[dict[str, Any]] = []
        for f in sorted(self._root.rglob("*.json")):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                if app_name and app_name not in str(f):
                    continue
                cases.append({
                    "id": data.get("id", ""),
                    "title": data.get("title", ""),
                    "priority": data.get("priority", ""),
                    "is_core": data.get("is_core", False),
                    "feature_id": data.get("feature_id", ""),
                    "step_count": len(data.get("steps", [])),
                })
            except Exception:
                continue
        return cases

    def has_case(self, tc_id: str, app_name: str = "") -> bool:
        """Check if a case exists in the standard library."""
        search_root = self._app_dir(app_name) if app_name else self._root
        return any(search_root.rglob(f"{tc_id}.json"))

    def delete(self, tc_id: str, app_name: str = "") -> bool:
        """Delete a case from the standard library."""
        search_root = self._app_dir(app_name) if app_name else self._root
        for f in search_root.rglob(f"{tc_id}.json"):
            f.unlink()
            return True
        return False

    def format_as_prompt(self, app_name: str = "", module: str = "") -> str:
        """Format standard cases as a prompt section for LLM injection.

        Returns an empty string if no standard cases exist.
        """
        cases = self.load_all(app_name, module)
        if not cases:
            return ""

        lines = [
            "## 标准测试用例（必须完全包含，不得遗漏）",
            "",
            "以下是用例库中已有的 P0/P1 核心用例，你生成的用例列表",
            "必须完整包含以下所有用例（可以调整步骤细节，但不能删除或修改核心场景）：",
            "",
        ]
        for tc in cases:
            lines.append(f"- {tc.id}: {tc.title} [{tc.priority}]")
            for s in tc.steps:
                val = f' value="{s.value}"' if s.value else ""
                lines.append(f"  {s.step}. [{s.action}] → {s.target}{val}")
            lines.append("")

        lines.append("请在生成用例时，将以上用例完整包含在你的输出中。")
        lines.append("在此基础之上，再补充额外的边缘场景和异常场景用例。")
        lines.append("")
        return "\n".join(lines)
