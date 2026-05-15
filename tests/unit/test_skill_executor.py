from unittest.mock import Mock

import pytest

from testagent.models.skill import SkillDefinition
from testagent.skills.executor import SkillExecutor, SkillResult, SkillStepResult


def _make_skill(
    name: str = "test_skill",
    version: str = "1.0.0",
    description: str = "A test skill",
    required_mcp_servers: list[str] | None = None,
    body: str | None = None,
) -> SkillDefinition:
    return SkillDefinition(
        name=name,
        version=version,
        description=description,
        trigger_pattern=r"test.*skill",
        required_mcp_servers=required_mcp_servers or [],
        required_rag_collections=[],
        body=body,
    )


class TestSkillResult:
    def test_default_values(self) -> None:
        result = SkillResult(skill_name="test", skill_version="1.0")
        assert result.skill_name == "test"
        assert result.skill_version == "1.0"
        assert result.status == "pending"
        assert result.step_results == []
        assert result.error is None
        assert result.duration_ms == 0.0


class TestSkillStepResult:
    def test_default_values(self) -> None:
        result = SkillStepResult(step_index=0, step_name="test")
        assert result.step_index == 0
        assert result.step_name == "test"
        assert result.status == "pending"
        assert result.output is None
        assert result.error is None
        assert result.duration_ms == 0.0

    def test_with_output(self) -> None:
        result = SkillStepResult(
            step_index=1,
            step_name="verify",
            status="passed",
            output={"status_code": 200},
            duration_ms=150.0,
        )
        assert result.status == "passed"
        assert result.output == {"status_code": 200}
        assert result.duration_ms == 150.0


class TestSkillExecutorValidatePrerequisites:
    @pytest.mark.asyncio
    async def test_no_registry_skips_validation(self) -> None:
        executor = SkillExecutor(mcp_registry=None)
        skill = _make_skill(required_mcp_servers=["api_server"])
        result = await executor._validate_prerequisites(skill)
        assert result["valid"] is True

    @pytest.mark.asyncio
    async def test_empty_required_servers_passes(self) -> None:
        mock_registry = Mock(spec=[])
        executor = SkillExecutor(mcp_registry=mock_registry)
        skill = _make_skill(required_mcp_servers=[])
        result = await executor._validate_prerequisites(skill)
        assert result["valid"] is True

    @pytest.mark.asyncio
    async def test_all_servers_registered_passes(self) -> None:
        mock_registry = Mock()
        mock_registry.is_registered = Mock(return_value=True)
        executor = SkillExecutor(mcp_registry=mock_registry)
        skill = _make_skill(required_mcp_servers=["api_server", "db_server"])
        result = await executor._validate_prerequisites(skill)
        assert result["valid"] is True

    @pytest.mark.asyncio
    async def test_missing_server_fails_validation(self) -> None:
        mock_registry = Mock()
        mock_registry.is_registered = Mock(return_value=False)
        executor = SkillExecutor(mcp_registry=mock_registry)
        skill = _make_skill(required_mcp_servers=["missing_server"])
        result = await executor._validate_prerequisites(skill)
        assert result["valid"] is False
        assert "missing_server" in result["error"]


class TestSkillExecutorParseSteps:
    def test_empty_body_returns_empty_list(self) -> None:
        executor = SkillExecutor()
        skill = _make_skill(body=None)
        steps = executor._parse_steps(skill)
        assert steps == []

    def test_body_with_no_headings_returns_single_step(self) -> None:
        executor = SkillExecutor()
        skill = _make_skill(body="Just some plain text content without headings.")
        steps = executor._parse_steps(skill)
        assert len(steps) == 1
        assert steps[0]["name"] == "body"
        assert steps[0]["content"] == "Just some plain text content without headings."

    def test_parses_headings_as_steps(self) -> None:
        executor = SkillExecutor()
        body = "## Step One\n\nContent for step one.\n\n## Step Two\n\nContent for step two."
        skill = _make_skill(body=body)
        steps = executor._parse_steps(skill)
        assert len(steps) == 2
        assert steps[0]["name"] == "Step One"
        assert steps[0]["content"] == "Content for step one."
        assert steps[1]["name"] == "Step Two"
        assert steps[1]["content"] == "Content for step two."

    def test_h1_and_h2_are_both_parsed(self) -> None:
        executor = SkillExecutor()
        body = "# Overview\n\nIntro text.\n\n## Detail\n\nDetail text."
        skill = _make_skill(body=body)
        steps = executor._parse_steps(skill)
        assert len(steps) == 2
        assert steps[0]["name"] == "Overview"
        assert steps[1]["name"] == "Detail"

    def test_step_indexes_are_sequential(self) -> None:
        executor = SkillExecutor()
        body = "# A\n\n## B\n\n## C\n\n# D"
        skill = _make_skill(body=body)
        steps = executor._parse_steps(skill)
        for i, step in enumerate(steps):
            assert step["index"] == i


class TestSkillExecutorExecute:
    @pytest.mark.asyncio
    async def test_execute_simple_body_passes(self) -> None:
        executor = SkillExecutor()
        skill = _make_skill(body="## Test Step\n\nDo something.")
        result = await executor.execute(skill)
        assert result.status == "passed"
        assert result.skill_name == "test_skill"
        assert result.skill_version == "1.0.0"
        assert len(result.step_results) == 1
        assert result.step_results[0].status == "passed"
        assert result.step_results[0].step_name == "Test Step"

    @pytest.mark.asyncio
    async def test_execute_fails_on_missing_mcp_server(self) -> None:
        mock_registry = Mock()
        mock_registry.is_registered = Mock(return_value=False)
        executor = SkillExecutor(mcp_registry=mock_registry)
        skill = _make_skill(
            required_mcp_servers=["missing_server"],
            body="## Step\n\nContent.",
        )
        result = await executor.execute(skill)
        assert result.status == "error"
        assert result.error is not None
        assert "missing_server" in result.error
        assert len(result.step_results) == 0

    @pytest.mark.asyncio
    async def test_execute_multiple_steps_all_pass(self) -> None:
        executor = SkillExecutor()
        body = "## Init\n\nInitialize.\n\n## Run\n\nRun tests.\n\n## Cleanup\n\nClean up."
        skill = _make_skill(body=body)
        result = await executor.execute(skill)
        assert result.status == "passed"
        assert len(result.step_results) == 3
        for step_result in result.step_results:
            assert step_result.status == "passed"

    @pytest.mark.asyncio
    async def test_execute_empty_body_results_in_passed(self) -> None:
        executor = SkillExecutor()
        skill = _make_skill(body=None)
        result = await executor.execute(skill)
        assert result.status == "passed"
        assert len(result.step_results) == 0

    @pytest.mark.asyncio
    async def test_execute_duration_is_positive(self) -> None:
        executor = SkillExecutor()
        skill = _make_skill(body="## Step\n\nContent.")
        result = await executor.execute(skill)
        assert result.duration_ms > 0
        for step_result in result.step_results:
            assert step_result.duration_ms >= 0
