"""Delta report generator for failed case replay.

Generates JSON + HTML reports comparing original failures vs replay results.
"""
from __future__ import annotations

import json
import os
from datetime import UTC, datetime


class DeltaReportGenerator:
    """Generates delta reports in JSON and HTML formats."""

    def generate(
        self,
        app_id: str,
        summary: dict,
        records: list[dict],
        output_dir: str = "reports/delta",
    ) -> tuple[str, str]:
        """Generate both JSON and HTML reports. Returns (json_path, html_path)."""
        json_path = self.generate_json(app_id, summary, records, output_dir)
        html_path = self.generate_html(app_id, summary, records, output_dir)
        return json_path, html_path

    def generate_json(
        self,
        app_id: str,
        summary: dict,
        records: list[dict],
        output_dir: str = "reports/delta",
    ) -> str:
        """Generate JSON delta report."""
        os.makedirs(output_dir, exist_ok=True)
        timestamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
        filename = f"{app_id}_{timestamp}.json"
        path = os.path.join(output_dir, filename)

        report = {
            "app_id": app_id,
            "timestamp": datetime.now(UTC).isoformat(),
            "summary": {
                "total_replayed": summary["total_replayed"],
                "fixed": summary["fixed"],
                "still_failed": summary["still_failed"],
                "blocked": summary.get("blocked", 0),
                "skipped": summary.get("skipped", 0),
            },
            "details": {
                "fixed": [
                    self._record_to_detail(r)
                    for r in records
                    if r.get("test_case_id") in summary["details"]["fixed"]
                ],
                "still_failed": [
                    self._record_to_detail(r)
                    for r in records
                    if r.get("test_case_id") in summary["details"]["still_failed"]
                ],
                "blocked": [
                    self._record_to_detail(r)
                    for r in records
                    if r.get("test_case_id") in summary["details"].get("blocked", [])
                ],
                "skipped": [
                    self._record_to_detail(r)
                    for r in records
                    if r.get("test_case_id") in summary["details"].get("skipped", [])
                ],
            },
        }

        with open(path, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
        return path

    def generate_html(
        self,
        app_id: str,
        summary: dict,
        records: list[dict],
        output_dir: str = "reports/delta",
    ) -> str:
        """Generate HTML delta report."""
        os.makedirs(output_dir, exist_ok=True)
        timestamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
        filename = f"{app_id}_{timestamp}.html"
        path = os.path.join(output_dir, filename)

        records_by_id = {r["test_case_id"]: r for r in records}
        details = summary["details"]

        def _category_rows(category_ids: list[str], status_label: str, color: str) -> str:
            if not category_ids:
                return f'<tr><td colspan="6" style="color:#999">No {status_label} cases</td></tr>'
            rows = []
            for tc_id in category_ids:
                r = records_by_id.get(tc_id, {})
                orig_err = r.get("original_error_message", "") or ""
                replay_err = r.get("last_replay_error_message", "") or ""
                replay_count = r.get("replay_count", 0)
                rows.append(
                    f'<tr style="border-left: 4px solid {color}">'
                    f'<td>{tc_id}</td>'
                    f'<td>{r.get("test_case_name", "")}</td>'
                    f'<td title="{orig_err}">{orig_err[:80]}</td>'
                    f'<td title="{replay_err}">{replay_err[:80]}</td>'
                    f'<td>{status_label}</td>'
                    f'<td>{replay_count}</td>'
                    f'</tr>'
                )
            return "\n".join(rows)

        fixed_rows = _category_rows(details["fixed"], "FIXED", "#4caf50")
        failed_rows = _category_rows(details["still_failed"], "STILL FAILED", "#f44336")
        blocked_rows = _category_rows(details.get("blocked", []), "BLOCKED", "#ff9800")
        skipped_rows = _category_rows(details.get("skipped", []), "SKIPPED", "#9e9e9e")

        html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>Delta Report — {app_id}</title>
<style>
body {{ font-family: -apple-system, sans-serif; margin: 2rem; background: #f5f5f5; }}
h1 {{ color: #333; }}
.cards {{ display: flex; gap: 1rem; margin: 1.5rem 0; }}
.card {{ padding: 1rem 1.5rem; border-radius: 8px; color: white; min-width: 120px; text-align: center; }}
.card .num {{ font-size: 2rem; font-weight: bold; }}
.card .label {{ font-size: 0.85rem; opacity: 0.9; }}
.card-total {{ background: #2196f3; }}
.card-fixed {{ background: #4caf50; }}
.card-failed {{ background: #f44336; }}
.card-blocked {{ background: #ff9800; }}
.card-skipped {{ background: #9e9e9e; }}
table {{ width: 100%; border-collapse: collapse; background: white; border-radius: 8px; overflow: hidden; box-shadow: 0 1px 3px rgba(0,0,0,0.1); }}
th {{ background: #f0f0f0; text-align: left; padding: 0.75rem; font-size: 0.85rem; }}
td {{ padding: 0.75rem; border-bottom: 1px solid #eee; font-size: 0.9rem; }}
h2 {{ margin-top: 2rem; color: #555; }}
</style>
</head>
<body>
<h1>Failed Case Replay — Delta Report</h1>
<p><strong>App:</strong> {app_id} &nbsp; <strong>Time:</strong> {datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S UTC")}</p>

<div class="cards">
  <div class="card card-total"><div class="num">{summary["total_replayed"]}</div><div class="label">Total Replayed</div></div>
  <div class="card card-fixed"><div class="num">{summary["fixed"]}</div><div class="label">Fixed</div></div>
  <div class="card card-failed"><div class="num">{summary["still_failed"]}</div><div class="label">Still Failed</div></div>
  <div class="card card-blocked"><div class="num">{summary.get("blocked", 0)}</div><div class="label">Blocked</div></div>
  <div class="card card-skipped"><div class="num">{summary.get("skipped", 0)}</div><div class="label">Skipped</div></div>
</div>

<h2>Fixed</h2>
<table><thead><tr><th>Case ID</th><th>Name</th><th>Original Error</th><th>Replay Error</th><th>Status</th><th>Replay Count</th></tr></thead>
<tbody>{fixed_rows}</tbody></table>

<h2>Still Failed</h2>
<table><thead><tr><th>Case ID</th><th>Name</th><th>Original Error</th><th>Replay Error</th><th>Status</th><th>Replay Count</th></tr></thead>
<tbody>{failed_rows}</tbody></table>

<h2>Blocked</h2>
<table><thead><tr><th>Case ID</th><th>Name</th><th>Original Error</th><th>Replay Error</th><th>Status</th><th>Replay Count</th></tr></thead>
<tbody>{blocked_rows}</tbody></table>

<h2>Skipped</h2>
<table><thead><tr><th>Case ID</th><th>Name</th><th>Original Error</th><th>Replay Error</th><th>Status</th><th>Replay Count</th></tr></thead>
<tbody>{skipped_rows}</tbody></table>
</body>
</html>"""

        with open(path, "w", encoding="utf-8") as f:
            f.write(html)
        return path

    @staticmethod
    def _record_to_detail(record: dict) -> dict:
        return {
            "test_case_id": record.get("test_case_id"),
            "test_case_name": record.get("test_case_name"),
            "original_error_message": record.get("original_error_message"),
            "last_replay_error_message": record.get("last_replay_error_message"),
            "last_replay_status": record.get("last_replay_status"),
            "replay_count": record.get("replay_count", 0),
        }
