from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import typer

from testagent.cli.plan import (
    format_tc_summary,
    parse_requirement,
    plan_command,
    present_tc_to_user,
    setup_output_dir,
)
from testagent.plan.models import ExecutionStatus, ExecutionVerdict, TestCase

# ── helpers ──────────────────────────────────────────────────────────────────


def _make_tc(
    tc_id: str = "TC-001",
    title: str = "Sample test",
    priority: str = "P1",
    is_core: bool = False,
) -> TestCase:
    return TestCase(
        id=tc_id,
        title=title,
        priority=priority,
        is_core=is_core,
    )


# ── parse_requirement ────────────────────────────────────────────────────────


class TestParseRequirement:
    def test_parse_requirement_file(self, tmp_path: Path) -> None:
        """Existing file path returns (path, True)."""
        file_path = tmp_path / "prd.md"
        file_path.write_text("# PRD Content", encoding="utf-8")
        result, is_file = parse_requirement(str(file_path))
        assert result == str(file_path)
        assert is_file is True

    def test_parse_requirement_text(self) -> None:
        """Raw text returns (text, False)."""
        text = "I want a login feature"
        result, is_file = parse_requirement(text)
        assert result == text
        assert is_file is False


# ── setup_output_dir ─────────────────────────────────────────────────────────


class TestSetupOutputDir:
    def test_setup_output_dir_creates_directory(self, tmp_path: Path) -> None:
        """Creates the correct directory structure."""
        base_dir = str(tmp_path)
        result = setup_output_dir("my-plan", base_dir=base_dir)
        path = Path(result)
        assert path.exists()
        assert path.is_dir()
        assert "my-plan" in path.name
        # Format: {base_dir}/{timestamp}-{safe_name}
        assert path.parent == tmp_path

    def test_setup_output_dir_timestamp_format(self, tmp_path: Path) -> None:
        """Directory name starts with a YYYY-MM-DD-HHMMSS timestamp."""
        base_dir = str(tmp_path)
        result = setup_output_dir("TestPlan", base_dir=base_dir)
        dirname = Path(result).name
        # e.g. 2026-05-24-033351-TestPlan
        parts = dirname.rsplit("-", 1)
        assert len(parts) == 2
        assert parts[1] == "TestPlan"

    def test_setup_output_dir_sanitizes_name(self, tmp_path: Path) -> None:
        """Spaces and special chars in plan name are sanitized."""
        base_dir = str(tmp_path)
        result = setup_output_dir("My Plan! @#$", base_dir=base_dir)
        dirname = Path(result).name
        assert "My-Plan" in dirname
        assert "!" not in dirname
        assert "@" not in dirname
        assert "#" not in dirname
        assert "$" not in dirname


# ── format_tc_summary ────────────────────────────────────────────────────────


class TestFormatTcSummary:
    def test_format_tc_summary_contains_ids(self) -> None:
        """Formatted string contains TC ids and titles."""
        tcs = [
            _make_tc("TC-001", "Login test"),
            _make_tc("TC-002", "Logout test"),
        ]
        result = format_tc_summary(tcs)
        assert "TC-001" in result
        assert "TC-002" in result
        assert "Login test" in result
        assert "Logout test" in result
        assert "2" in result  # total count

    def test_format_tc_summary_single(self) -> None:
        """Single TC shows correct info."""
        tc = _make_tc("TC-001", "Login test", priority="P0", is_core=True)
        result = format_tc_summary([tc])
        assert "TC-001" in result
        assert "Login test" in result
        assert "[P0]" in result
        assert "[CORE]" in result
        assert "1" in result

    def test_format_tc_summary_empty(self) -> None:
        """Empty list returns empty string."""
        result = format_tc_summary([])
        assert result == ""


# ── present_tc_to_user ───────────────────────────────────────────────────────


class TestPresentTcToUser:
    def test_present_tc_to_user_auto_yes(self) -> None:
        """auto_yes=True returns True regardless of TC list."""
        tcs = [_make_tc("TC-001", "Test")]
        assert present_tc_to_user(tcs, auto_yes=True) is True

    def test_present_tc_to_user_auto_yes_empty(self) -> None:
        """auto_yes=True with empty list returns True."""
        assert present_tc_to_user([], auto_yes=True) is True

    def test_present_tc_to_user_empty(self) -> None:
        """Empty TC list with auto_yes=False returns False."""
        assert present_tc_to_user([], auto_yes=False) is False

    @patch("testagent.cli.plan.typer.prompt")
    @patch("testagent.cli.plan.typer.echo")
    def test_present_tc_to_user_confirmed(
        self, mock_echo: MagicMock, mock_prompt: MagicMock
    ) -> None:
        """With TCs and auto_yes=False, prompts user and returns their choice."""
        mock_prompt.return_value = "y"
        tcs = [_make_tc("TC-001", "Login test")]
        result = present_tc_to_user(tcs, auto_yes=False)
        assert result is True
        mock_prompt.assert_called_once()

    @patch("testagent.cli.plan.typer.prompt")
    @patch("testagent.cli.plan.typer.echo")
    def test_present_tc_to_user_rejected(
        self, mock_echo: MagicMock, mock_prompt: MagicMock
    ) -> None:
        """User rejects the prompt."""
        mock_prompt.return_value = "n"
        tcs = [_make_tc("TC-001", "Login test")]
        result = present_tc_to_user(tcs, auto_yes=False)
        assert result is False


# ── plan_command orchestration ───────────────────────────────────────────────


class TestPlanCommand:
    @patch("testagent.cli.plan.ReportGenerator")
    @patch("testagent.cli.plan.OverallEvaluator")
    @patch("testagent.cli.plan.PerTCEvaluator")
    @patch("testagent.cli.plan.ExecutionEngine")
    @patch("testagent.cli.plan.TestCaseGenerator")
    @patch("testagent.cli.plan.PrdParser")
    @patch("testagent.cli.plan.SessionManager")
    @patch("testagent.cli.plan.typer.echo")
    def test_plan_command_raw_text_flow(
        self,
        mock_echo: MagicMock,
        mock_sm_cls: MagicMock,
        mock_prd_parser: MagicMock,
        mock_tc_gen_cls: MagicMock,
        mock_engine_cls: MagicMock,
        mock_evaluator_cls: MagicMock,
        mock_overall_cls: MagicMock,
        mock_report_cls: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Full orchestration with raw text input."""
        base_dir = tmp_path / "reports"
        report_path = str(base_dir / "20250101-120000-myplan" / "plan-report.md")

        # Mock SessionManager to prevent real Appium calls
        mock_sm = MagicMock()
        mock_sm_cls.return_value = mock_sm
        mock_sm.create_session.return_value = None  # No session → skip exploration

        # Mock TestCaseGenerator
        mock_generator = MagicMock()
        mock_tc_gen_cls.return_value = mock_generator
        mock_generator.generate = AsyncMock(return_value=[
            _make_tc("TC-001", "Login test"),
            _make_tc("TC-002", "Logout test"),
        ])

        # Mock ExecutionEngine
        mock_engine = MagicMock()
        mock_engine._interrupted = False
        mock_engine_cls.return_value = mock_engine
        executed_tcs = [
            _make_tc("TC-001", "Login test"),
            _make_tc("TC-002", "Logout test"),
        ]
        mock_engine.execute_all = AsyncMock(return_value=executed_tcs)

        # Mock PerTCEvaluator
        mock_evaluator = MagicMock()
        mock_evaluator_cls.return_value = mock_evaluator
        eval_result = MagicMock()
        eval_result.verdict = ExecutionVerdict.PASS
        eval_result.confidence = 0.95
        eval_result.reason = "All steps passed"
        mock_evaluator.evaluate.return_value = eval_result

        # Mock OverallEvaluator
        mock_overall = MagicMock()
        mock_overall_cls.return_value = mock_overall
        overall_result = MagicMock(
            verdict=ExecutionVerdict.PASS,
            total_count=2,
            passed_count=2,
            core_total=0,
            core_passed=0,
            need_review_count=0,
            blocked_count=0,
            summary="All passed",
            review_recommendations=[],
        )
        mock_overall.evaluate.return_value = overall_result

        # Mock ReportGenerator
        mock_report = MagicMock()
        mock_report_cls.return_value = mock_report
        mock_report.generate.return_value = report_path

        with (
            patch("testagent.cli.plan.datetime") as mock_dt,
            patch("testagent.db.engine.init_db", new_callable=AsyncMock),
            patch("testagent.config.settings.get_settings") as mock_settings,
            patch("testagent.llm.local_provider.LLMProviderFactory.create") as mock_llm_factory,
        ):
            mock_dt.now.return_value.strftime.return_value = "20250101-120000"
            mock_settings.return_value = MagicMock()
            mock_llm_factory.return_value = MagicMock()
            result = plan_command(
                requirement="I want a login feature",
                name="myplan",
                app_package="com.example.app",
                app_activity=".MainActivity",
                auto_yes=True,
            )

        # Verify orchestration phases
        # Phase 0: Input parsed
        # (raw text, so PrdParser should NOT be called)
        mock_prd_parser.assert_not_called()

        # Phase 2: TestCaseGenerator invoked
        mock_tc_gen_cls.assert_called_once()
        mock_generator.generate.assert_called_once()

        # Phase 3: auto_yes skips prompt → execution proceeds

        # Phase 4: ExecutionEngine invoked
        mock_engine_cls.assert_called_once()
        mock_engine.execute_all.assert_called_once()

        # Phase 5: Per-TC evaluation on each TC
        assert mock_evaluator.evaluate.call_count == 2

        # Phase 6: Overall evaluation + report
        mock_overall.evaluate.assert_called_once()
        mock_report_cls.assert_called_once()
        mock_report.generate.assert_called_once()

        # Returns (report_path, overall, executed_tcs) tuple
        assert result[0] == report_path

    @patch("testagent.cli.plan.TestCaseGenerator")
    @patch("testagent.cli.plan.typer.echo")
    def test_plan_command_no_test_cases(
        self,
        mock_echo: MagicMock,
        mock_tc_gen_cls: MagicMock,
    ) -> None:
        """No generated TCs aborts early."""
        mock_generator = MagicMock()
        mock_tc_gen_cls.return_value = mock_generator
        mock_generator.generate = AsyncMock(return_value=[])

        with (
            patch("testagent.db.engine.init_db", new_callable=AsyncMock),
            patch("testagent.config.settings.get_settings") as mock_settings,
            patch("testagent.llm.local_provider.LLMProviderFactory.create") as mock_llm_factory,
        ):
            mock_settings.return_value = MagicMock()
            mock_llm_factory.return_value = MagicMock()
            result = plan_command(
                requirement="some text",
                name="test",
                auto_yes=True,
            )

        assert result[0] is None

    @patch("testagent.cli.plan.TestCaseGenerator")
    @patch("testagent.cli.plan.setup_output_dir")
    @patch("testagent.cli.plan.present_tc_to_user")
    @patch("testagent.cli.plan.typer.echo")
    def test_plan_command_user_cancels(
        self,
        mock_echo: MagicMock,
        mock_present: MagicMock,
        mock_setup_dir: MagicMock,
        mock_tc_gen_cls: MagicMock,
    ) -> None:
        """User cancellation aborts before execution."""
        mock_generator = MagicMock()
        mock_tc_gen_cls.return_value = mock_generator
        mock_generator.generate = AsyncMock(return_value=[_make_tc("TC-001", "Test")])

        mock_setup_dir.return_value = "/tmp/reports/x"
        mock_present.return_value = False

        with (
            patch("testagent.db.engine.init_db", new_callable=AsyncMock),
            patch("testagent.config.settings.get_settings") as mock_settings,
            patch("testagent.llm.local_provider.LLMProviderFactory.create") as mock_llm_factory,
        ):
            mock_settings.return_value = MagicMock()
            mock_llm_factory.return_value = MagicMock()
            result = plan_command(
                requirement="some text",
                name="test",
                auto_yes=False,
            )

        assert result[0] is None

    @patch("testagent.cli.plan.ReportGenerator")
    @patch("testagent.cli.plan.OverallEvaluator")
    @patch("testagent.cli.plan.PerTCEvaluator")
    @patch("testagent.cli.plan.ExecutionEngine")
    @patch("testagent.cli.plan.TestCaseGenerator")
    @patch("testagent.cli.plan.PrdParser")
    @patch("testagent.cli.plan._detect_app_package")
    @patch("testagent.cli.plan.typer.echo")
    def test_plan_command_with_prd_file(
        self,
        mock_echo: MagicMock,
        mock_detect: MagicMock,
        mock_prd_parser_cls: MagicMock,
        mock_tc_gen_cls: MagicMock,
        mock_engine_cls: MagicMock,
        mock_evaluator_cls: MagicMock,
        mock_overall_cls: MagicMock,
        mock_report_cls: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Orchestration with PRD file input."""
        mock_detect.return_value = None  # no auto-detect
        file_path = tmp_path / "requirements.md"
        file_path.write_text("# PRD", encoding="utf-8")
        report_path = str(tmp_path / "reports" / "x" / "plan-report.md")

        # Mock PrdParser
        mock_parser = MagicMock()
        mock_prd_parser_cls.return_value = mock_parser
        prd_doc = MagicMock()
        prd_doc.formatted_text = "Parsed PRD content"
        mock_parser.parse.return_value = prd_doc

        # Mock TestCaseGenerator
        mock_generator = MagicMock()
        mock_tc_gen_cls.return_value = mock_generator
        mock_generator.generate = AsyncMock(return_value=[_make_tc("TC-001", "Test")])

        # Mock execution engine
        mock_engine = MagicMock()
        mock_engine._interrupted = False
        mock_engine_cls.return_value = mock_engine
        mock_engine.execute_all = AsyncMock(return_value=[_make_tc("TC-001", "Test")])

        # Mock evaluators
        mock_evaluator = MagicMock()
        mock_evaluator_cls.return_value = mock_evaluator
        eval_result = MagicMock(verdict=ExecutionVerdict.PASS, confidence=0.9, reason="OK")
        mock_evaluator.evaluate.return_value = eval_result

        mock_overall = MagicMock()
        mock_overall_cls.return_value = mock_overall
        mock_overall.evaluate.return_value = MagicMock(
            verdict=ExecutionVerdict.PASS,
            total_count=1, passed_count=1,
            core_total=0, core_passed=0,
            need_review_count=0, blocked_count=0,
            summary="OK", review_recommendations=[],
        )

        mock_report = MagicMock()
        mock_report_cls.return_value = mock_report
        mock_report.generate.return_value = report_path

        with (
            patch("testagent.cli.plan.datetime") as mock_dt,
            patch("testagent.db.engine.init_db", new_callable=AsyncMock),
            patch("testagent.config.settings.get_settings") as mock_settings,
            patch("testagent.llm.local_provider.LLMProviderFactory.create") as mock_llm_factory,
        ):
            mock_dt.now.return_value.strftime.return_value = "20250101-120000"
            mock_settings.return_value = MagicMock()
            mock_llm_factory.return_value = MagicMock()
            result = plan_command(
                requirement=str(file_path),
                name="req-plan",
                app_package="",
                app_activity="",
                auto_yes=True,
            )

        # Verify PrdParser was called for file input
        mock_prd_parser_cls.assert_called_once()
        mock_parser.parse.assert_called_once_with(str(file_path))

        # Verify rest of pipeline
        mock_generator.generate.assert_called_once()
        assert mock_generator.generate.call_args[0][0] == "Parsed PRD content"
        mock_engine.execute_all.assert_called_once()
        mock_evaluator.evaluate.assert_called_once()
        mock_overall.evaluate.assert_called_once()
        mock_report.generate.assert_called_once()

        assert result[0] == report_path


# ── main.py registration ────────────────────────────────────────────────────


class TestMainPlanRegistration:
    def test_main_registers_app_plan_command(self) -> None:
        """Verify main.py has `testagent app plan` registered."""
        from typer.testing import CliRunner

        from testagent.cli.main import app

        runner = CliRunner()
        result = runner.invoke(app, ["app", "plan", "--help"])
        assert result.exit_code == 0
        assert "产品需求文档路径" in result.stdout
        assert "--auto-yes" in result.stdout or "-y" in result.stdout
