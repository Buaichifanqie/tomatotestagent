from __future__ import annotations

from typing import Any

from testagent.common.logging import get_logger
from testagent.rule_engine.context_manager import ContextManager
from testagent.rule_engine.data_source import ApiDataSource, DataSourceFactory
from testagent.rule_engine.models import (
    AssertionConfig,
    AssertionResult,
    AssertionStatus,
    CompareResult,
    DataSourceConfig,
)
from testagent.rule_engine.smart_comparator import SmartComparator
from testagent.rule_engine.ui_extractor import UIExtractor
from testagent.rule_engine.yaml_parser import RuleYamlParser

logger = get_logger(__name__)


class RuleEngine:
    """Orchestrates data fetching, UI extraction, and comparison.

    Manages the three-phase execution:
    Phase A: execute_setup() — fetch data from APIs/DBs, register in context
    Phase B: UI operations happen in ExecutionEngine (not here)
    Phase C: execute_assertions() — extract UI values, compare with external data
    """

    def __init__(self, appium_url: str, session_id: str) -> None:
        self.context = ContextManager()
        self._ui_extractor = UIExtractor(appium_url, session_id)
        self._comparator = SmartComparator()
        self._parser = RuleYamlParser()
        self._setup_cache: dict[str, dict[str, Any]] = {}

    async def execute_setup(self, setup_configs: list[dict[str, Any]]) -> None:
        """Phase A: Execute all data sources and register results in context.

        Args:
            setup_configs: List of data source config dicts from YAML.
        """
        configs = self._parser.parse_setup(setup_configs)

        for config in configs:
            # Resolve ${var} in config
            resolved_config = self.context.resolve_dict(config.model_dump())

            source = DataSourceFactory.create(resolved_config)

            if config.type in ("api", "database"):
                result = await source.fetch(self.context)
            else:
                logger.warning(f"Unknown data source type: {config.type}")
                continue

            if "error" in result:
                logger.warning(f"Setup data source '{config.name}' failed: {result['error']}")
                continue

            # Register extracted values in context
            self.context.register_batch(result)
            # Also cache for source_ref lookups
            self._setup_cache[config.name] = result
            logger.info(f"Setup '{config.name}' completed: {list(result.keys())}")

    async def execute_assertions(
        self, assertion_configs: list[dict[str, Any]]
    ) -> list[AssertionResult]:
        """Phase C: Execute all assertions and return results.

        Args:
            assertion_configs: List of assertion config dicts from YAML.

        Returns:
            List of AssertionResult objects.
        """
        configs = self._parser.parse_assertions(assertion_configs)
        results = []

        for config in configs:
            if config.type == "cross_source":
                result = await self._execute_cross_source(config)
            elif config.type == "ui_visible":
                result = self._execute_ui_visible(config)
            else:
                result = AssertionResult(
                    field=config.field or config.target,
                    assertion_type=config.type,
                    status=AssertionStatus.ERROR,
                    error_message=f"Unknown assertion type: {config.type}",
                )
            results.append(result)

        return results

    async def _execute_cross_source(self, config: AssertionConfig) -> AssertionResult:
        """Execute a cross-source comparison assertion."""
        field = config.field
        sources = config.sources

        # Extract UI value
        ui_config = sources.get("ui", {})
        ui_value = await self._ui_extractor.extract(ui_config, self.context)

        if ui_value is None:
            return AssertionResult(
                field=field,
                assertion_type="cross_source",
                status=AssertionStatus.ERROR,
                error_message=f"UI extraction failed for '{ui_config.get('semantic', field)}'",
                source_values={"ui": None},
            )

        # Extract expected value from API/DB source
        expected_value = None
        source_name = ""

        for source_key in ("api", "database", "db"):
            if source_key in sources:
                source_config = sources[source_key]
                source_name = source_key
                expected_value = await self._extract_expected_value(source_config)
                break

        if expected_value is None:
            return AssertionResult(
                field=field,
                assertion_type="cross_source",
                status=AssertionStatus.ERROR,
                error_message=f"Failed to extract expected value from {source_name} source",
                source_values={"ui": ui_value},
            )

        # Compare
        compare_config = ui_config if ui_config.get("transform") else {}
        compare_mode = config.compare_mode

        compare_result = self._comparator.compare(
            ui_value=ui_value,
            expected_value=expected_value,
            transform=compare_config.get("transform"),
            compare_mode=compare_mode,
        )

        status = AssertionStatus.PASS if compare_result.matched else AssertionStatus.FAIL

        return AssertionResult(
            field=field,
            assertion_type="cross_source",
            status=status,
            compare_result=compare_result,
            source_values={"ui": ui_value, source_name: expected_value},
        )

    async def _extract_expected_value(self, source_config: dict[str, Any]) -> Any:
        """Extract expected value from a source config (cache or real-time)."""
        source_ref = source_config.get("source_ref", "")
        extract_path = source_config.get("extract", "")

        # Try cache first (source_ref)
        if source_ref and source_ref in self._setup_cache:
            cached = self._setup_cache[source_ref]
            if extract_path:
                return ApiDataSource.resolve_json_path(cached, extract_path)
            return cached

        # Real-time fetch
        if source_config.get("type") in ("api", "database"):
            resolved = self.context.resolve_dict(source_config)

            # Normalize extract: DataSourceFactory expects a dict, but
            # assertion configs may provide a single JSONPath string.
            normalized_key = "__expected__"
            if isinstance(resolved.get("extract"), str):
                resolved["extract"] = {normalized_key: resolved["extract"]}

            source = DataSourceFactory.create(resolved)
            result = await source.fetch(self.context)

            if "error" not in result:
                if normalized_key in result:
                    return result[normalized_key]
                for key, value in result.items():
                    return value
            return None

        return None

    def _execute_ui_visible(self, config: AssertionConfig) -> AssertionResult:
        """Execute a simple UI visibility assertion (placeholder for MVP)."""
        # This is a placeholder -- actual UI visibility is checked by ExecutionEngine
        return AssertionResult(
            field=config.target,
            assertion_type="ui_visible",
            status=AssertionStatus.PASS,
            error_message="UI visibility check delegated to ExecutionEngine",
        )
