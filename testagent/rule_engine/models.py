from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class AssertionStatus(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    ERROR = "ERROR"
    NEED_REVIEW = "NEED_REVIEW"


class CompareResult(BaseModel):
    """Result of a SmartComparator comparison."""
    matched: bool
    ui_value: Any = None
    expected_value: Any = None
    matcher_used: str = ""
    confidence: float = 1.0
    message: str = ""


class AssertionResult(BaseModel):
    """Result of a single assertion execution."""
    field: str
    assertion_type: str  # "cross_source", "ui_visible", etc.
    status: AssertionStatus
    compare_result: CompareResult | None = None
    error_message: str = ""
    source_values: dict[str, Any] = Field(default_factory=dict)


class DataSourceConfig(BaseModel):
    """Configuration for a data source from YAML."""
    name: str
    type: str  # "api", "database", "plugin"
    method: str = ""
    endpoint: str = ""
    headers: dict[str, str] = Field(default_factory=dict)
    body: dict[str, Any] = Field(default_factory=dict)
    connection: str = ""
    query: str = ""
    extract: dict[str, str] = Field(default_factory=dict)
    source_ref: str = ""  # Reference to a setup data source name


class AssertionConfig(BaseModel):
    """Configuration for an assertion from YAML."""
    type: str  # "cross_source", "ui_visible"
    field: str = ""
    target: str = ""
    expected: Any = None
    sources: dict[str, Any] = Field(default_factory=dict)
    compare_mode: str = "auto"  # "auto", "strict"
