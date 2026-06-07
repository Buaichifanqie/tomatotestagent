from __future__ import annotations

from typing import Any

from testagent.rule_engine.models import AssertionConfig, DataSourceConfig


class RuleYamlParser:
    """Parse rule engine YAML configs into internal models."""

    def parse_setup(self, setup_list: list[dict[str, Any]]) -> list[DataSourceConfig]:
        """Parse setup data source configs from YAML."""
        result = []
        for item in setup_list or []:
            result.append(DataSourceConfig(
                name=item.get("name", ""),
                type=item.get("type", ""),
                method=item.get("method", ""),
                endpoint=item.get("endpoint", ""),
                headers=item.get("headers", {}),
                body=item.get("body", {}),
                connection=item.get("connection", ""),
                query=item.get("query", ""),
                extract=item.get("extract", {}),
                source_ref=item.get("source_ref", ""),
            ))
        return result

    def parse_assertions(self, assertion_list: list[dict[str, Any]]) -> list[AssertionConfig]:
        """Parse assertion configs from YAML."""
        result = []
        for item in assertion_list or []:
            result.append(AssertionConfig(
                type=item.get("type", ""),
                field=item.get("field", ""),
                target=item.get("target", ""),
                expected=item.get("expected"),
                sources=item.get("sources", {}),
                compare_mode=item.get("compare_mode", "auto"),
            ))
        return result
