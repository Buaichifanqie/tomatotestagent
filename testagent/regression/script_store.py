"""Script persistence — centralized ``./scripts/`` directory."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from testagent.regression.types import RegressionScript, ScriptStatus


def _default_scripts_root() -> Path:
    for c in [Path.cwd(), Path(__file__).parent.parent.parent]:
        if (c / "pyproject.toml").exists():
            return c / "scripts"
    return Path.cwd() / "scripts"


class ScriptStore:
    """Centralized regression script storage (``./scripts/``)."""

    def __init__(self, scripts_root: str = "") -> None:
        self._root = Path(scripts_root) if scripts_root else _default_scripts_root()
        self._root.mkdir(parents=True, exist_ok=True)
        self._healing_log = self._root / "healing_log.jsonl"

    def _module_dir(self, app_name: str = "", module: str = "") -> Path:
        """Get directory for an app + module, keyed by stable app name."""
        parts = [self._root]
        if app_name:
            parts.append(app_name.lower().replace(" ", "_"))
        if module:
            parts.append(module.lower().replace(" ", "_").replace("-", "_"))
        d = Path(*parts)
        d.mkdir(parents=True, exist_ok=True)
        return d

    def save(self, script: RegressionScript, module: str = "") -> Path:
        app = script.app_name or script.app_package or "unknown"
        mod = module or "default"
        target_dir = self._module_dir(app, mod)
        (target_dir / "assets").mkdir(parents=True, exist_ok=True)
        fp = target_dir / f"{script.tc_id}.json"
        fp.write_text(script.to_file_content(), encoding="utf-8")
        return fp

    def load(self, tc_id: str, app_package: str = "") -> RegressionScript | None:
        for f in self._root.rglob(f"{tc_id}.json"):
            if not f.is_file():
                continue
            if app_package:
                try:
                    data = json.loads(f.read_text(encoding="utf-8"))
                    if data.get("app_package") != app_package and data.get("app_name") not in (app_package, ""):
                        continue
                except Exception:
                    continue
            try:
                return RegressionScript.from_file_content(f.read_text(encoding="utf-8"))
            except Exception:
                continue
        return None

    def delete(self, tc_id: str) -> bool:
        deleted = False
        for f in self._root.rglob(f"{tc_id}.json"):
            if f.is_file():
                f.unlink()
                deleted = True
        return deleted

    def list_scripts(self, app_name: str = "") -> list[dict[str, Any]]:
        scripts: list[dict[str, Any]] = []
        for f in sorted(self._root.rglob("*.json")):
            if f.name in ("manifest.json", "healing_log.jsonl"):
                continue
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                if app_name and data.get("app_name", "") != app_name:
                    continue
                scripts.append({
                    "tc_id": data.get("tc_id", ""),
                    "tc_title": data.get("tc_title", ""),
                    "app_name": data.get("app_name", data.get("app_package", "")),
                    "app_package": data.get("app_package", ""),
                    "app_version": data.get("app_version", ""),
                    "platform": data.get("platform", "android"),
                    "status": data.get("status", "active"),
                    "step_count": len(data.get("steps", [])),
                    "generated_at": data.get("generated_at", ""),
                    "run_count": data.get("run_count", 0),
                    "heal_count": data.get("heal_count", 0),
                    "module": str(f.parent.relative_to(self._root)),
                })
            except Exception:
                continue
        return scripts

    def has_script(self, tc_id: str) -> bool:
        return any(self._root.rglob(f"{tc_id}.json"))

    def mark_unstable(self, tc_id: str) -> bool:
        script = self.load(tc_id)
        if not script:
            return False
        script.status = ScriptStatus.UNSTABLE
        self.save(script)
        return True

    def mark_deprecated(self, tc_id: str) -> bool:
        script = self.load(tc_id)
        if not script:
            return False
        script.status = ScriptStatus.DEPRECATED
        self.save(script)
        return True

    def find_by_title(self, title: str, app_name: str = "", min_similarity: float = 0.4) -> RegressionScript | None:
        best_score = 0.0
        best_script: RegressionScript | None = None
        for f in self._root.rglob("*.json"):
            if f.name in ("manifest.json", "healing_log.jsonl"):
                continue
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                if app_name and data.get("app_name", "") != app_name:
                    continue
            except Exception:
                continue
            st = data.get("tc_title", "")
            score = _title_similarity(title, st)
            if score > best_score:
                best_score = score
                try:
                    best_script = RegressionScript.from_file_content(f.read_text(encoding="utf-8"))
                except Exception:
                    continue
        if best_score >= min_similarity:
            return best_script
        return None

    @staticmethod
    def find_across_reports(title: str, app_name: str = "", min_similarity: float = 0.4, reports_root: str = "reports") -> RegressionScript | None:
        centralized = ScriptStore()
        found = centralized.find_by_title(title=title, app_name=app_name, min_similarity=min_similarity)
        if found:
            return found
        root = Path(reports_root)
        if not root.exists():
            return None
        best_score = 0.0
        best_script: RegressionScript | None = None
        for d in sorted(root.rglob("scripts")):
            if not d.is_dir():
                continue
            store = ScriptStore(scripts_root=str(d.parent))
            f = store.find_by_title(title=title, app_name=app_name, min_similarity=min_similarity)
            if f:
                s = _title_similarity(title, f.tc_title)
                if s > best_score:
                    best_score = s
                    best_script = f
        return best_script

    def append_healing_log(self, record_dict: dict[str, Any]) -> None:
        line = json.dumps(record_dict, ensure_ascii=False)
        with open(self._healing_log, "a", encoding="utf-8") as f:
            f.write(line + "\n")

    def get_healing_logs(self, tc_id: str = "") -> list[dict[str, Any]]:
        if not self._healing_log.exists():
            return []
        logs: list[dict[str, Any]] = []
        for line in self._healing_log.read_text(encoding="utf-8").strip().split("\n"):
            if not line.strip():
                continue
            try:
                r = json.loads(line)
                if tc_id and r.get("tc_id") != tc_id:
                    continue
                logs.append(r)
            except json.JSONDecodeError:
                continue
        logs.reverse()
        return logs


def _title_similarity(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    import re
    a = re.sub(r"[^\w]", "", a.lower())
    b = re.sub(r"[^\w]", "", b.lower())
    if a == b:
        return 1.0

    def tok(s: str) -> set[str]:
        c = set(s)
        w = set(re.findall(r"[a-z0-9_]+", s))
        bg = set(s[i:i+2] for i in range(len(s)-1))
        return c | w | bg

    ta, tb = tok(a), tok(b)
    if not ta or not tb:
        return 0.0
    token_score = len(ta & tb) / len(ta | tb)
    ml = max(len(a), len(b))
    ed = _edit_distance(a, b)
    edit_score = 1.0 - (ed / ml) if ml else 1.0
    return 0.6 * token_score + 0.4 * edit_score


def _edit_distance(s1: str, s2: str) -> int:
    m, n = len(s1), len(s2)
    if m > n:
        s1, s2 = s2, s1
        m, n = n, m
    prev = list(range(m + 1))
    for j, c2 in enumerate(s2):
        cur = [j + 1]
        for i, c1 in enumerate(s1):
            cost = 0 if c1 == c2 else 1
            cur.append(min(cur[-1] + 1, prev[i + 1] + 1, prev[i] + cost))
        prev = cur
    return prev[m]
