# Cross-Source Validation Engine Design Spec

**Date:** 2026-06-07
**Status:** Draft
**Author:** AI Agent + User collaborative design

---

## 1. Problem Statement

Current test evaluation in vibe-ai-agent is **step-centric**: it only checks whether UI actions succeed or fail, using assertions like "is element visible" or "does text match". This creates three blind spots:

1. **Type A - Cross-data inconsistency**: UI shows `¥100.00` but the backend set `¥150.00`. Steps pass, but the feature is broken.
2. **Type C - Weak collection validation**: Search returns 10 results, only 1 matches the keyword. Current assertion says "keyword exists = PASS".
3. **Type D - Implicit app failures**: App crashes behind a system dialog, or ANR occurs silently. Agent can't distinguish "element not found" from "app is dead".

The goal is to add a **cross-source validation engine** that can fetch data from external systems (APIs, databases), extract values from the UI, and perform intelligent comparison.

---

## 2. Design Goals

| Goal | Description |
|------|-------------|
| **Multi-source data fusion** | Combine UI, API, and DB data into a unified context |
| **Intelligent comparison** | Auto-handle format differences (currency, datetime, enum mapping) |
| **Extensible rules** | Users can define custom validation logic via YAML |
| **Minimal disruption** | Integrate into existing `testagent app plan` flow without breaking it |
| **Progressive enhancement** | MVP uses deterministic methods (DOM + OCR + rule-based comparison); LLM fallback deferred to V1.1+ |

---

## 3. Architecture Overview

### 3.1 Three-Phase Execution Model

```
┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│   Phase A   │────▶│   Phase B   │────▶│   Phase C   │
│  Data Setup │     │  UI Steps   │     │  Assertions │
│  (API/DB)   │     │  (Appium)   │     │  (比对引擎)  │
└─────────────┘     └─────────────┘     └─────────────┘
       │                   │                   │
       ▼                   ▼                   ▼
  ┌─────────────────────────────────────────────────┐
  │              Context Manager (Flat Dict)         │
  │  ${product_id}, ${api_price}, ${ui_price}, ...  │
  └─────────────────────────────────────────────────┘
```

### 3.2 Context-Centric Integration

The new engine does **not** simply "insert between Phase 4 and Phase 5". Instead, it **transforms Phase 4 and Phase 5 internally**, with Context as the central nervous system:

- **Phase 4 (ExecutionEngine)** is extended: before executing UI steps, run `setup` data sources; during UI steps, extract values into Context.
- **Phase 5 (PerTCEvaluator)** is enhanced: after step-level evaluation, run `SmartComparator` to compare Context values from different sources.

### 3.3 Module Structure

```
testagent/
├── plan/                     # Existing: case generation, LLM interaction, execution
│   ├── execution_engine.py   # Extended: setup execution, UI value extraction
│   ├── evaluator.py          # Enhanced: cross-source comparison
│   └── ...
└── rule_engine/              # NEW: Cross-source validation engine
    ├── __init__.py
    ├── data_source.py        # BaseDataSource + api/database implementations
    ├── context_manager.py    # Flat context dict + ${variable} substitution
    ├── ui_extractor.py       # Three-layer UI extraction (DOM → OCR → VLM)
    ├── smart_comparator.py   # Three-layer comparison funnel
    ├── yaml_parser.py        # Parse assertion YAML into internal models
    └── engine.py             # RuleEngine orchestrator
```

---

## 4. YAML DSL Specification

### 4.1 Complete Example

```yaml
test_case:
  id: "TC-PRICE-001"
  title: "特价商品折扣价一致性验证"
  priority: "P1"

  # ── Phase A: Data Preparation ──────────────────────────────
  setup:
    - name: "create_product"
      type: "api"
      method: "POST"
      endpoint: "${API_BASE}/products"
      headers:
        Authorization: "Bearer ${token}"
      body:
        name: "测试商品_${random_id}"
        original_price: 200
        discount_price: 150
      extract:
        product_id: "$.data.id"
        product_name: "$.data.name"

    - name: "query_db"
      type: "database"
      connection: "test_db_mysql"
      query: "SELECT discount_price FROM products WHERE id = :product_id"
      extract:
        db_price: "$.rows[0].discount_price"

  # ── Phase B: UI Operations ─────────────────────────────────
  steps:
    - step: 1
      action: "tap"
      target: "搜索框"
    - step: 2
      action: "type"
      target: "搜索输入框"
      value: "${product_name}"
    - step: 3
      action: "tap"
      target: "搜索按钮"
    - step: 4
      action: "wait"
      target: "商品列表加载完成"

  # ── Phase C: Assertions ────────────────────────────────────
  assertions:
    # Simple UI assertion (existing capability)
    - type: "ui_visible"
      target: "商品卡片"
      expected: true

    # Cross-source comparison — Option A: reuse setup cache (fast, for static data)
    - type: "cross_source"
      field: "discount_price"
      sources:
        ui:
          semantic: "商品折扣价"    # Intent-driven, engine auto-extracts
        api:
          source_ref: "create_product"  # Reuse setup cache
          extract: "$.discount_price"
        # No transform needed — SmartComparator auto-handles ¥100.00 vs 150

    # Cross-source comparison — Option B: real-time fetch (for data that may change after UI ops)
    - type: "cross_source"
      field: "stock_count"
      sources:
        ui:
          semantic: "库存数量"
        api:
          type: "api"               # Real-time fetch, not cache
          method: "GET"
          endpoint: "${API_BASE}/products/${product_id}/stock"
          extract: "$.data.count"

    # Cross-source with explicit transform
    - type: "cross_source"
      field: "original_price"
      sources:
        ui:
          semantic: "商品原价"
        db:
          source_ref: "query_db"
          extract: "$.db_price"
      compare_mode: "strict"

    # Collection validation (Type C) — V1.1+, shown here for reference
    # - type: "collection_quality"
    #   field: "search_results"
    #   sources:
    #     ui:
    #       semantic: "搜索结果列表"
    #       extract: "all_texts"
    #   rules:
    #     keyword_match_rate:
    #       keyword: "${product_name}"
    #       min_rate: 0.8
```

### 4.2 Context Variable Substitution

All `${variable}` references are resolved against a **flat context dictionary**. Variables are registered from:

1. **Setup extract**: `extract.product_id` → `${product_id}`
2. **Built-in variables**: `${random_id}`, `${timestamp}`, `${API_BASE}`, `${token}`
3. **UI extraction**: Values extracted during Phase B → `${ui_price}`

References use simple `${name}` syntax, no deep paths like `${setup.data_sources.0.extract.product_id}`.

### 4.3 Semi-Automatic Flow (Phase 3.5)

In the existing `testagent app plan` flow:

```
Phase 0-3: PRD parsing → UI exploration → TC generation (unchanged)
    │
    ▼
Phase 3.5 (NEW): Data Source Alignment
    │   LLM generates assertion templates with placeholders
    │   System prompts user: "Please fill in API/DB config for price verification"
    │   User fills in endpoint, SQL, etc.
    ▼
Phase 4: ExecutionEngine (extended with setup + UI extraction)
    ▼
Phase 5: PerTCEvaluator (enhanced with SmartComparator)
    ▼
Phase 6: Overall evaluation + report (unchanged)
```

---

## 5. Module Designs

### 5.1 DataSource (`data_source.py`)

```python
class BaseDataSource(ABC):
    @abstractmethod
    async def fetch(self, context: dict) -> dict:
        """Fetch data. Context contains runtime variables."""
        pass

    @abstractmethod
    async def create(self, data: dict, context: dict) -> dict:
        """Create test data, return created state."""
        pass

    def cleanup(self, context: dict) -> None:
        """Optional: reverse cleanup after test (hook for future)."""
        pass

class ApiDataSource(BaseDataSource):
    """Built-in: REST API data source."""
    # Supports: GET/POST/PUT/DELETE
    # JSONPath extraction via $.data.field
    # Header injection (auth tokens, etc.)

class DatabaseDataSource(BaseDataSource):
    """Built-in: SQL database data source."""
    # Supports: MySQL, PostgreSQL, SQLite
    # Parameterized queries with :variable substitution
    # Result extraction via $.rows[0].field

class PluginDataSource(BaseDataSource):
    """User-defined: custom Python class."""
    # Loaded via: type: "plugin", class: "my_module.MyDataSource"
    # Params passed from YAML
```

### 5.2 ContextManager (`context_manager.py`)

```python
class ContextManager:
    """Flat dictionary with ${variable} substitution."""

    def __init__(self):
        self._store: dict[str, Any] = {}

    def register(self, key: str, value: Any) -> None:
        """Register a variable in the context."""
        pass

    def register_batch(self, data: dict[str, Any]) -> None:
        """Register multiple variables at once."""
        pass

    def resolve(self, template: str) -> str:
        """Replace ${var} placeholders in a string."""
        pass

    def resolve_dict(self, data: dict) -> dict:
        """Recursively resolve all ${var} in a dict."""
        pass

    def get(self, key: str, default: Any = None) -> Any:
        """Get a value from context."""
        pass
```

### 5.3 UIExtractor (`ui_extractor.py`)

Three-layer extraction funnel:

```python
class UIExtractor:
    """Extract values from UI using three-layer fallback."""

    async def extract(self, config: dict, context: dict) -> Any:
        """
        Layer 1: DOM semantic extraction
          - Find element by xpath/id/accessibility_id
          - Return innerText attribute

        Layer 2: Anchor + OCR
          - Use semantic text as anchor to find coordinates
          - Crop screenshot around anchor
          - Run OCR on cropped region

        Layer 3: VLM visual extraction (deferred to V1.1+)
          - Send full screenshot to vision model
          - Ask: "Extract the value of {semantic}"
        """
        pass

    async def extract_collection(self, config: dict, context: dict) -> list[str]:
        """Extract all text items from a list/collection element."""
        pass
```

### 5.4 SmartComparator (`smart_comparator.py`)

Three-layer comparison funnel:

```python
class SmartComparator:
    """Intelligent comparison with three-layer fallback."""

    def compare(self, ui_value: Any, expected_value: Any,
                config: dict = None) -> CompareResult:
        """
        Layer 1: Built-in Smart Matchers (80% of cases, zero config)
          - NumericMatcher: 100.0 == 100 == 100.00
          - CurrencyMatcher: ¥100.00 == 100
          - DatetimeMatcher: various formats → timestamp comparison
          - FuzzyStringMatcher: case-insensitive, trim whitespace

        Layer 2: Explicit Transforms (19% of cases)
          - strip_currency: Remove ¥/$ symbols
          - divide_by_100: Cents to yuan
          - map: {"1": "待发货", "2": "已发货"}

        Layer 3: LLM Semantic (1% of cases, deferred to V1.1+)
          - Rich text comparison
          - Icon/emoji semantic matching
        """
        pass

class CompareResult:
    matched: bool
    ui_value: Any
    expected_value: Any
    matcher_used: str  # Which matcher/transform was used
    confidence: float
    message: str
```

Built-in matchers:

| Matcher | Input | Output | Logic |
|---------|-------|--------|-------|
| NumericMatcher | `"100.0"`, `100` | Match | Parse both as float, compare |
| CurrencyMatcher | `"¥100.00"`, `150` | No match | Strip `¥$`, parse as float |
| DatetimeMatcher | `"2026-06-07"`, `1749273600` | Match | Normalize to timestamp |
| FuzzyStringMatcher | `"Hello"`, `"hello "` | Match | Lowercase + strip |

### 5.5 RuleEngine (`engine.py`)

```python
class RuleEngine:
    """Orchestrates data fetching, UI extraction, and comparison."""

    def __init__(self, appium_url: str, session_id: str):
        self._context = ContextManager()
        self._data_source_factory = DataSourceFactory()
        self._ui_extractor = UIExtractor(appium_url, session_id)
        self._comparator = SmartComparator()

    async def execute_setup(self, setup_configs: list[dict]) -> None:
        """Phase A: Execute all data sources, register results in context."""
        pass

    async def execute_assertions(self, assertion_configs: list[dict]) -> list[AssertionResult]:
        """Phase C: Execute all assertions, return results."""
        pass
```

---

## 6. Error Handling

| Scenario | Handling Strategy |
|----------|-------------------|
| API timeout | Retry 1x with 5s timeout, then mark assertion as `ERROR` (not FAIL) |
| API returns non-200 | Mark assertion as `ERROR`, log response body |
| DB connection failure | Mark assertion as `ERROR`, skip DB-dependent assertions |
| OCR returns empty | Fall back to VLM (V1.1+), or mark as `NEED_REVIEW` |
| Variable not in context | Mark assertion as `ERROR` with clear message: `${product_id} not found` |
| JSONPath extraction fails | Mark assertion as `ERROR`, log the raw response |

---

## 7. MVP Scope

### In Scope (MVP)

- [ ] `BaseDataSource` interface + `ApiDataSource` + `DatabaseDataSource`
- [ ] `ContextManager` with flat dict + `${variable}` substitution
- [ ] `UIExtractor` with DOM extraction + OCR (PaddleOCR or cloud API)
- [ ] `SmartComparator` with 4 built-in matchers + explicit transforms
- [ ] `RuleEngine` orchestrator
- [ ] YAML parser for `setup` and `assertions` sections
- [ ] Integration with `ExecutionEngine` (setup execution + UI extraction)
- [ ] Integration with `PerTCEvaluator` (cross-source comparison)
- [ ] Semi-automatic Phase 3.5 (placeholder YAML generation + user prompt)
- [ ] `cleanup()` hook in BaseDataSource (no-op default)

### Out of Scope (V1.1+)

- [ ] VLM visual extraction fallback (Layer 3 of UIExtractor)
- [ ] LLM semantic comparison fallback (Layer 3 of SmartComparator)
- [ ] `PluginDataSource` custom class loading
- [ ] Collection quality assertions (keyword match rate)
- [ ] Automatic data cleanup
- [ ] Swagger/OpenAPI auto-discovery

---

## 8. Integration Points

### 8.1 With ExecutionEngine

```python
# In ExecutionEngine._execute_single():
# Before steps:
if tc.setup:
    await self._rule_engine.execute_setup(tc.setup)

# During steps (after each step):
if step.action == "extract_ui":
    value = await self._rule_engine.extract_ui(step.config)
    context.register(step.target, value)

# After steps:
if tc.assertions:
    results = await self._rule_engine.execute_assertions(tc.assertions)
    # Store results in tc.execution
```

### 8.2 With PerTCEvaluator

```python
# In PerTCEvaluator._fallback_evaluate():
# After existing logic:
if tc.execution.cross_source_results:
    for result in tc.execution.cross_source_results:
        if not result.matched:
            return EvaluationOutput(
                verdict=ExecutionVerdict.FAIL,
                confidence=result.confidence,
                reason=f"Cross-source mismatch: {result.message}",
            )
```

---

## 9. Evolution Roadmap

| Version | Capability | Human Effort |
|---------|-----------|--------------|
| **MVP** | DOM + OCR extraction, rule-based comparison | User fills API/DB config in Phase 3.5 |
| **V1.0** | + Swagger integration, LLM auto-generates API config | User reviews and confirms |
| **V1.1** | + VLM extraction fallback, LLM semantic comparison | Minimal |
| **V2.0** | + DB schema discovery, full auto end-to-end | Zero |
