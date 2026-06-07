"""Cross-source validation engine for business-semantic-level test verification.

Provides multi-source data fusion (UI + API + DB) with intelligent comparison
for automated test validation beyond simple element assertions.

Usage:
    from testagent.rule_engine import RuleEngine, SmartComparator, ContextManager

    engine = RuleEngine(appium_url="http://localhost:4723", session_id="abc")
    await engine.execute_setup(setup_configs)
    results = await engine.execute_assertions(assertion_configs)
"""

from testagent.rule_engine.context_manager import ContextManager
from testagent.rule_engine.data_source import (
    ApiDataSource,
    BaseDataSource,
    DatabaseDataSource,
    DataSourceFactory,
)
from testagent.rule_engine.engine import RuleEngine
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

__all__ = [
    "ContextManager",
    "BaseDataSource",
    "ApiDataSource",
    "DatabaseDataSource",
    "DataSourceFactory",
    "RuleEngine",
    "SmartComparator",
    "UIExtractor",
    "RuleYamlParser",
    "AssertionConfig",
    "AssertionResult",
    "AssertionStatus",
    "CompareResult",
    "DataSourceConfig",
]
