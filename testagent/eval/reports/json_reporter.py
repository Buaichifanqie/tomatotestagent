"""JSON report generator.

Serialises SuiteResult to a JSON-serialisable dict and saves it.
"""

from __future__ import annotations

import json
from pathlib import Path

from ..models import SuiteResult


class JsonReporter:
    """Generates JSON reports from evaluation results."""

    @staticmethod
    def generate(result: SuiteResult) -> dict:
        """Return a JSON-serialisable dict of the evaluation result."""
        return result.to_dict()

    @staticmethod
    def save(result: SuiteResult, output_dir: Path) -> str:
        """Write the JSON report to *output_dir* / summary.json."""
        output_dir.mkdir(parents=True, exist_ok=True)
        path = output_dir / "summary.json"
        path.write_text(
            json.dumps(result.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return str(path)
