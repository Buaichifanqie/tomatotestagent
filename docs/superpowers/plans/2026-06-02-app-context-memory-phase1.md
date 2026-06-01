# App Context Memory — Phase 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Persist AI-generated test cases to RAG and inject historical cases as context for future generation of the same App.

**Architecture:** Add a new RAG collection `app_test_cases` for per-app test case storage. After TC generation and user confirmation, serialize cases to text and write back via `RAGPipeline.write_back()`. Before TC generation, query the collection with the PRD text and prepend results to the generation prompt. Use `app_package` as the per-app namespace filter.

**Tech Stack:** Existing RAG pipeline (ChromaDB/Milvus + Meilisearch), existing `TestCaseGenerator`, `plan.py` orchestration.

**Spec:** `docs/superpowers/specs/2026-06-02-app-context-memory-design.md`

---

## File Structure

| Action | File | Responsibility |
|--------|------|---------------|
| Create | `testagent/rag/app_memory.py` | Serialization (TestCase list -> text) and retrieval formatting |
| Create | `tests/unit/rag/test_app_memory.py` | Tests for serialization and formatting |
| Modify | `testagent/rag/collections.py` | Add `app_test_cases` collection entry |
| Modify | `testagent/cli/plan.py` | Inject retrieval before TC gen, write-back after confirmation |
| Create | `tests/unit/plan/test_app_memory_integration.py` | Integration tests for the plan.py wiring |

---

### Task 1: Add `app_test_cases` RAG collection

**Files:**
- Modify: `testagent/rag/collections.py`
- Test: `tests/unit/rag/test_rag_pipeline.py` (existing, verify collection exists)

- [ ] **Step 1: Add collection entry**

In `testagent/rag/collections.py`, add to `RAG_COLLECTIONS`:

```python
RAG_COLLECTIONS: dict[str, dict[str, Any]] = {
    # ... existing entries ...
    "app_test_cases": {"description": "历史测试用例（Per-App 记忆）", "access": ["planner"]},
}
```

- [ ] **Step 2: Verify collection is registered**

Run: `python -c "from testagent.rag.collections import RAG_COLLECTIONS; print('app_test_cases' in RAG_COLLECTIONS)"`
Expected: `True`

- [ ] **Step 3: Commit**

```bash
git add testagent/rag/collections.py
git commit -m "feat(rag): add app_test_cases collection for App Context Memory"
```

---

### Task 2: Create serialization utility — TestCase list to text

**Files:**
- Create: `testagent/rag/app_memory.py`
- Create: `tests/unit/rag/test_app_memory.py`

- [ ] **Step 1: Write failing tests for serialization**

Create `tests/unit/rag/test_app_memory.py`:

```python
from __future__ import annotations

import json

from testagent.plan.models import TestCase, TestStep
from testagent.rag.app_memory import serialize_cases_for_storage, format_retrieved_cases_for_prompt


class TestSerializeCasesForStorage:
    """serialize_cases_for_storage converts TestCase list to searchable text."""

    def test_empty_list(self):
        result = serialize_cases_for_storage([])
        assert result == ""

    def test_single_case(self):
        cases = [
            TestCase(
                id="TC-SEARCH-001",
                title="正常搜索",
                priority="P0",
                is_core=True,
                steps=[
                    TestStep(step=1, action="launch", target="tv.danmaku.bili"),
                    TestStep(step=2, action="tap", target="搜索框"),
                    TestStep(step=3, action="type", target="搜索框", value="测试"),
                ],
            )
        ]
        result = serialize_cases_for_storage(cases)
        assert "TC-SEARCH-001" in result
        assert "正常搜索" in result
        assert "P0" in result
        assert "launch" in result
        assert "tv.danmaku.bili" in result
        assert "type" in result
        assert "测试" in result

    def test_multiple_cases(self):
        cases = [
            TestCase(id="TC-001", title="用例1", steps=[TestStep(step=1, action="tap", target="按钮")]),
            TestCase(id="TC-002", title="用例2", steps=[TestStep(step=1, action="type", target="输入框", value="文本")]),
        ]
        result = serialize_cases_for_storage(cases)
        assert "TC-001" in result
        assert "TC-002" in result
        # Cases are separated by a delimiter
        assert "---" in result or "\n\n" in result

    def test_output_is_valid_text(self):
        """Output should be plain text suitable for RAG chunking, not JSON."""
        cases = [
            TestCase(id="TC-001", title="测试", steps=[TestStep(step=1, action="tap", target="X")]),
        ]
        result = serialize_cases_for_storage(cases)
        # Should NOT be raw JSON
        with pytest.raises(json.JSONDecodeError):
            json.loads(result)


class TestFormatRetrievedCasesForPrompt:
    """format_retrieved_cases_for_prompt formats RAG results for prompt injection."""

    def test_empty_results(self):
        result = format_retrieved_cases_for_prompt([])
        assert result == ""

    def test_single_result(self):
        from testagent.rag.pipeline import RAGResult

        results = [
            RAGResult(
                doc_id="abc123",
                content="用例: TC-001 正常搜索\n步骤: 1. launch 2. tap 搜索框",
                score=0.92,
                metadata={"app_package": "tv.danmaku.bili", "plan_name": "test"},
            )
        ]
        result = format_retrieved_cases_for_prompt(results)
        assert "TC-001" in result
        assert "0.92" in result or "92%" in result

    def test_multiple_results_numbered(self):
        from testagent.rag.pipeline import RAGResult

        results = [
            RAGResult(doc_id="a", content="用例1", score=0.9, metadata={}),
            RAGResult(doc_id="b", content="用例2", score=0.8, metadata={}),
        ]
        result = format_retrieved_cases_for_prompt(results)
        assert "用例1" in result
        assert "用例2" in result
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/unit/rag/test_app_memory.py -v`
Expected: FAIL (module not found)

- [ ] **Step 3: Implement serialization utility**

Create `testagent/rag/app_memory.py`:

```python
"""App Context Memory — serialization and retrieval formatting.

Converts TestCase lists to searchable text for RAG storage,
and formats RAG retrieval results for prompt injection.
"""
from __future__ import annotations

from testagent.plan.models import TestCase
from testagent.rag.pipeline import RAGResult


def serialize_cases_for_storage(cases: list[TestCase]) -> str:
    """Serialize a list of TestCase objects into searchable plain text.

    Output format is human-readable text (not JSON) optimized for RAG chunking
    and semantic retrieval. Each case becomes a structured block.
    """
    if not cases:
        return ""

    blocks: list[str] = []
    for tc in cases:
        lines: list[str] = []
        lines.append(f"用例: {tc.id} {tc.title}")
        lines.append(f"优先级: {tc.priority}")
        if tc.is_core:
            lines.append("核心用例: 是")
        if tc.requirement_ids:
            lines.append(f"关联需求: {', '.join(tc.requirement_ids)}")
        if tc.steps:
            step_lines: list[str] = []
            for s in tc.steps:
                parts = [f"{s.step}. [{s.action}]"]
                if s.target:
                    parts.append(f"target={s.target}")
                if s.value:
                    parts.append(f"value={s.value}")
                step_lines.append(" ".join(parts))
            lines.append("步骤:\n" + "\n".join(step_lines))
        blocks.append("\n".join(lines))

    return "\n\n---\n\n".join(blocks)


def format_retrieved_cases_for_prompt(results: list[RAGResult]) -> str:
    """Format RAG retrieval results into a prompt-ready context section.

    Returns a formatted string suitable for prepending to the TC generation prompt.
    Returns empty string if no results.
    """
    if not results:
        return ""

    lines: list[str] = ["以下是该 App 的历史测试用例（仅供参考，避免重复）：", ""]
    for i, r in enumerate(results, 1):
        score_pct = f"{r.score * 100:.0f}%"
        lines.append(f"--- 历史用例 {i}（相似度: {score_pct}）---")
        lines.append(r.content)
        lines.append("")

    return "\n".join(lines)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/unit/rag/test_app_memory.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add testagent/rag/app_memory.py tests/unit/rag/test_app_memory.py
git commit -m "feat(memory): add TestCase serialization and retrieval formatting"
```

---

### Task 3: Wire retrieval into plan.py — read path

**Files:**
- Modify: `testagent/cli/plan.py` (lines ~598-609, the `enhanced_prd` construction)
- Create: `tests/unit/plan/test_app_memory_integration.py`

- [ ] **Step 1: Write failing test for retrieval injection**

Create `tests/unit/plan/test_app_memory_integration.py`:

```python
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from testagent.rag.app_memory import format_retrieved_cases_for_prompt
from testagent.rag.pipeline import RAGResult


class TestRetrievalInjection:
    """Test that historical cases are injected into the generation prompt."""

    def test_format_empty_results_returns_empty(self):
        result = format_retrieved_cases_for_prompt([])
        assert result == ""

    def test_format_with_results_contains_context(self):
        results = [
            RAGResult(
                doc_id="abc",
                content="用例: TC-001 正常搜索\n步骤: 1. launch 2. tap 搜索框",
                score=0.9,
                metadata={"app_package": "tv.danmaku.bili"},
            )
        ]
        formatted = format_retrieved_cases_for_prompt(results)
        assert "历史测试用例" in formatted
        assert "TC-001" in formatted
        assert "90%" in formatted

    def test_enhanced_prd_includes_history_when_available(self):
        """Simulate the enhanced_prd construction logic from plan.py."""
        prd_text = "测试哔哩哔哩搜索功能"
        app_package = "tv.danmaku.bili"

        # Simulate RAG returning historical cases
        mock_results = [
            RAGResult(
                doc_id="x",
                content="用例: TC-SEARCH-001 搜索视频",
                score=0.85,
                metadata={},
            )
        ]
        history_context = format_retrieved_cases_for_prompt(mock_results)

        # Build enhanced_prd the same way plan.py will
        enhanced_prd = prd_text
        if history_context:
            enhanced_prd = history_context + "\n\n" + enhanced_prd

        assert "TC-SEARCH-001" in enhanced_prd
        assert "测试哔哩哔哩搜索功能" in enhanced_prd
        # History comes BEFORE the user's requirement
        assert enhanced_prd.index("TC-SEARCH-001") < enhanced_prd.index("测试哔哩哔哩搜索功能")

    def test_enhanced_prd_unchanged_when_no_history(self):
        """When RAG returns nothing, enhanced_prd should be unchanged."""
        prd_text = "测试哔哩哔哩搜索功能"
        history_context = format_retrieved_cases_for_prompt([])

        enhanced_prd = prd_text
        if history_context:
            enhanced_prd = history_context + "\n\n" + enhanced_prd

        assert enhanced_prd == prd_text
```

- [ ] **Step 2: Run tests to verify they pass**

Run: `python -m pytest tests/unit/plan/test_app_memory_integration.py -v`
Expected: PASS (pure logic tests, no external deps)

- [ ] **Step 3: Add retrieval call to plan.py**

In `testagent/cli/plan.py`, add the import at the top:

```python
from testagent.rag.app_memory import format_retrieved_cases_for_prompt, serialize_cases_for_storage
```

In `_plan_command_async`, after the `enhanced_prd` construction (line ~606) and before `ts_gen = TestCaseGenerator(...)`, add the retrieval logic:

```python
    # ── Phase 2.5: Retrieve historical cases from App Context Memory ──────
    history_context = ""
    if app_package:
        try:
            from testagent.rag.factories import create_pipeline

            rag_pipeline = create_pipeline(settings)
            rag_results = await rag_pipeline.query(
                query_text=enhanced_prd,
                collection="app_test_cases",
                top_k=5,
                filters={"app_package": app_package},
            )
            if rag_results:
                history_context = format_retrieved_cases_for_prompt(rag_results)
                typer.echo(f"  Found {len(rag_results)} historical case(s) from App Context Memory.")
        except Exception as exc:
            typer.echo(f"  [App Context Memory retrieval skipped: {exc}]")

    # Inject history context before the user's requirement
    if history_context:
        enhanced_prd = history_context + "\n\n" + enhanced_prd
```

The final flow becomes:
```
enhanced_prd = prd_text + app_info
history_context = RAG query(enhanced_prd, app_test_cases, filter=app_package)
enhanced_prd = history_context + "\n\n" + enhanced_prd  # prepend history
ts_gen.generate(enhanced_prd)
```

- [ ] **Step 4: Run existing plan tests to verify no regressions**

Run: `python -m pytest tests/unit/plan/test_plan_cli.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add testagent/cli/plan.py tests/unit/plan/test_app_memory_integration.py
git commit -m "feat(plan): retrieve historical cases from App Context Memory before TC generation"
```

---

### Task 4: Wire write-back into plan.py — write path

**Files:**
- Modify: `testagent/cli/plan.py` (after user confirmation, before execution)

- [ ] **Step 1: Add write-back after user confirms test cases**

In `_plan_command_async`, after the user confirmation check (line ~638, after `present_tc_to_user`) and before execution starts, add the write-back logic:

```python
    # ── Persist confirmed cases to App Context Memory ──────────────────
    if app_package:
        try:
            from testagent.rag.factories import create_pipeline

            rag_pipeline = create_pipeline(settings)
            cases_text = serialize_cases_for_storage(test_cases)
            if cases_text:
                await rag_pipeline.write_back(
                    content=cases_text,
                    collection="app_test_cases",
                    metadata={
                        "app_package": app_package,
                        "plan_name": name,
                        "case_count": len(test_cases),
                    },
                )
                typer.echo(f"  Saved {len(test_cases)} case(s) to App Context Memory.")
        except Exception as exc:
            typer.echo(f"  [App Context Memory write-back skipped: {exc}]")
```

The write-back happens AFTER user confirmation (so cancelled/edited cases are not persisted) and BEFORE execution (so the cases are available for retrieval even if execution fails).

- [ ] **Step 2: Run existing plan tests to verify no regressions**

Run: `python -m pytest tests/unit/plan/test_plan_cli.py -v`
Expected: PASS

- [ ] **Step 3: Commit**

```bash
git add testagent/cli/plan.py
git commit -m "feat(plan): persist confirmed cases to App Context Memory"
```

---

### Task 5: Add `--app-id` CLI parameter

**Files:**
- Modify: `testagent/cli/app_cmd.py`
- Modify: `testagent/cli/plan.py` (add `app_id` parameter to `plan_command` and `_plan_command_async`)

- [ ] **Step 1: Add `--app-id` option to CLI**

In `testagent/cli/app_cmd.py`, add to the `plan` function signature:

```python
@app_typer.command()
def plan(
    requirement: str = typer.Argument(..., help="产品需求文档路径 或 自然语言需求描述"),
    name: str = typer.Option("", "--name", "-n", help="自定义计划名称"),
    app_package: str = typer.Option("", "--app-package", "-p", help="App package name"),
    app_activity: str = typer.Option("", "--app-activity", "-a", help="App launch activity"),
    app_id: str = typer.Option("", "--app-id", help="App 标识（如 com.bilibili.app），默认使用 app-package"),
    auto_yes: bool = typer.Option(False, "--auto-yes", "-y", help="跳过确认步骤，直接执行"),
) -> None:
```

Pass it through to `_plan_command`:

```python
    _plan_command(
        requirement,
        name=name,
        app_package=app_package,
        app_activity=app_activity,
        app_id=app_id,
        auto_yes=auto_yes,
    )
```

- [ ] **Step 2: Update plan_command signatures**

In `testagent/cli/plan.py`, update both `plan_command` and `_plan_command_async` to accept `app_id: str = ""`:

```python
def plan_command(
    requirement: str,
    name: str = "",
    app_package: str = "",
    app_activity: str = "",
    app_id: str = "",
    auto_yes: bool = False,
) -> str | None:
    return asyncio.run(_plan_command_async(
        requirement, name=name,
        app_package=app_package, app_activity=app_activity,
        app_id=app_id, auto_yes=auto_yes,
    ))
```

In `_plan_command_async`, derive the effective app identifier:

```python
async def _plan_command_async(
    requirement: str,
    name: str = "",
    app_package: str = "",
    app_activity: str = "",
    app_id: str = "",
    auto_yes: bool = False,
) -> str | None:
```

After auto-detection of `app_package`, derive the memory app_id:

```python
    # ── Derive app identifier for App Context Memory ────────────────────
    memory_app_id = app_id or app_package  # explicit --app-id takes priority
```

Use `memory_app_id` (not `app_package`) as the RAG filter in Tasks 3 and 4.

- [ ] **Step 3: Run existing tests**

Run: `python -m pytest tests/unit/plan/test_plan_cli.py -v`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add testagent/cli/app_cmd.py testagent/cli/plan.py
git commit -m "feat(cli): add --app-id parameter for App Context Memory namespace"
```

---

### Task 6: End-to-end verification

- [ ] **Step 1: Run all unit tests**

Run: `python -m pytest tests/unit/ -v --tb=short`
Expected: All PASS

- [ ] **Step 2: Run the serialization tests**

Run: `python -m pytest tests/unit/rag/test_app_memory.py -v`
Expected: All PASS

- [ ] **Step 3: Run the integration tests**

Run: `python -m pytest tests/unit/plan/test_app_memory_integration.py -v`
Expected: All PASS

- [ ] **Step 4: Manual smoke test (requires RAG infra)**

```bash
# First run: generate cases for Bilibili
testagent app plan "测试哔哩哔哩搜索功能" -p tv.danmaku.bili -y

# Second run: same app, same requirement — should see "Found N historical case(s)"
testagent app plan "测试哔哩哔哩搜索功能" -p tv.danmaku.bili -y
```

Expected output on second run:
```
  Found 5 historical case(s) from App Context Memory.
Generated N test case(s).
```

- [ ] **Step 5: Final commit (if any fixups needed)**

```bash
git add -A
git commit -m "fix: address review feedback for App Context Memory Phase 1"
```
