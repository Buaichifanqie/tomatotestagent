from __future__ import annotations

import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from typing import Any


def generate_junit_xml(tasks: list[dict[str, Any]], suite_name: str = "testagent") -> str:
    testsuite = ET.Element(
        "testsuite",
        name=suite_name,
        tests=str(len(tasks)),
        failures=str(sum(1 for t in tasks if t.get("status") == "failed")),
        errors=str(sum(1 for t in tasks if t.get("status") == "error")),
        skipped=str(sum(1 for t in tasks if t.get("status") in ("skipped", "flaky"))),
        timestamp=datetime.now(UTC).isoformat(),
    )

    for task in tasks:
        name = task.get("name", "unnamed")
        status = task.get("status", "unknown")
        duration = task.get("duration", "0")
        error_msg = task.get("error", None)

        testcase = ET.SubElement(
            testsuite,
            "testcase",
            classname=suite_name,
            name=name,
            time=str(duration),
        )

        if status == "failed":
            failure = ET.SubElement(testcase, "failure", message="Test failed")
            failure.text = error_msg or "No error details"
        elif status == "error":
            error_elem = ET.SubElement(testcase, "error", message="Test error")
            error_elem.text = error_msg or "No error details"
        elif status == "skipped":
            ET.SubElement(testcase, "skipped", message="Test skipped")
        elif status == "flaky":
            ET.SubElement(testcase, "skipped", message="Test flaky (retried)")

    return ET.tostring(testsuite, encoding="unicode", xml_declaration=True)
