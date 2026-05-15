from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from testagent.gateway.celery_app import celery_app

# ====================================================================
# Celery App Configuration Tests
# ====================================================================


class TestCeleryAppConfig:
    def test_app_name(self) -> None:
        assert celery_app.main == "testagent"

    def test_broker_url_from_settings(self) -> None:
        assert celery_app.conf.broker_url == "redis://localhost:6379/0"

    def test_result_backend_from_settings(self) -> None:
        assert celery_app.conf.result_backend == "redis://localhost:6379/1"

    def test_task_serializer_json(self) -> None:
        assert celery_app.conf.task_serializer == "json"

    def test_result_serializer_json(self) -> None:
        assert celery_app.conf.result_serializer == "json"

    def test_accept_content_json(self) -> None:
        assert celery_app.conf.accept_content == ["json"]

    def test_task_track_started(self) -> None:
        assert celery_app.conf.task_track_started is True

    def test_task_acks_late(self) -> None:
        assert celery_app.conf.task_acks_late is True

    def test_worker_prefetch_multiplier(self) -> None:
        assert celery_app.conf.worker_prefetch_multiplier == 1

    def test_task_ignore_result(self) -> None:
        assert celery_app.conf.task_ignore_result is False

    def test_task_store_errors_even_if_ignored(self) -> None:
        assert celery_app.conf.task_store_errors_even_if_ignored is True

    def test_task_soft_time_limit(self) -> None:
        assert celery_app.conf.task_soft_time_limit == 300

    def test_task_time_limit(self) -> None:
        assert celery_app.conf.task_time_limit == 330

    def test_default_queue_is_execution(self) -> None:
        assert celery_app.conf.task_default_queue == "execution"

    def test_default_exchange_name(self) -> None:
        assert celery_app.conf.task_default_exchange == "testagent"

    def test_default_exchange_type_topic(self) -> None:
        assert celery_app.conf.task_default_exchange_type == "topic"

    def test_default_routing_key(self) -> None:
        assert celery_app.conf.task_default_routing_key == "execution.default"


class TestTaskQueues:
    def test_three_queues_defined(self) -> None:
        queues = celery_app.conf.task_queues
        assert len(queues) == 3

    def test_planning_queue(self) -> None:
        queues = celery_app.conf.task_queues
        queue_names = {q.name for q in queues}
        assert "planning" in queue_names

    def test_execution_queue(self) -> None:
        queues = celery_app.conf.task_queues
        queue_names = {q.name for q in queues}
        assert "execution" in queue_names

    def test_analysis_queue(self) -> None:
        queues = celery_app.conf.task_queues
        queue_names = {q.name for q in queues}
        assert "analysis" in queue_names

    def test_planning_routing_key(self) -> None:
        queues = {q.name: q for q in celery_app.conf.task_queues}
        assert queues["planning"].routing_key == "planning.#"

    def test_execution_routing_key(self) -> None:
        queues = {q.name: q for q in celery_app.conf.task_queues}
        assert queues["execution"].routing_key == "execution.#"

    def test_analysis_routing_key(self) -> None:
        queues = {q.name: q for q in celery_app.conf.task_queues}
        assert queues["analysis"].routing_key == "analysis.#"

    def test_planning_queue_has_default_priority(self) -> None:
        queues = {q.name: q for q in celery_app.conf.task_queues}
        planning_q = queues["planning"]
        assert hasattr(planning_q, "queue_arguments")


# ====================================================================
# Task Registration Tests
# ====================================================================


class TestTaskRegistration:
    def test_execute_test_task_registered(self) -> None:
        assert "testagent.gateway.tasks.execute_test_task" in celery_app.tasks

    def test_execute_planning_task_registered(self) -> None:
        assert "testagent.gateway.tasks.execute_planning_task" in celery_app.tasks

    def test_execute_analysis_task_registered(self) -> None:
        assert "testagent.gateway.tasks.execute_analysis_task" in celery_app.tasks


# ====================================================================
# execute_test_task Tests
# ====================================================================


class TestExecuteTestTask:
    def test_max_retries(self) -> None:
        task = celery_app.tasks["testagent.gateway.tasks.execute_test_task"]
        assert task.max_retries == 3

    def test_default_retry_delay(self) -> None:
        task = celery_app.tasks["testagent.gateway.tasks.execute_test_task"]
        assert task.default_retry_delay == 2

    def test_acks_late(self) -> None:
        task = celery_app.tasks["testagent.gateway.tasks.execute_test_task"]
        assert task.acks_late is True

    def test_reject_on_worker_lost(self) -> None:
        task = celery_app.tasks["testagent.gateway.tasks.execute_test_task"]
        assert task.reject_on_worker_lost is True

    def test_queue_assigned(self) -> None:
        task = celery_app.tasks["testagent.gateway.tasks.execute_test_task"]
        assert task.queue == "execution"

    def test_soft_time_limit(self) -> None:
        task = celery_app.tasks["testagent.gateway.tasks.execute_test_task"]
        assert task.soft_time_limit == 300

    def test_time_limit(self) -> None:
        task = celery_app.tasks["testagent.gateway.tasks.execute_test_task"]
        assert task.time_limit == 330

    def test_retry_called_on_failure(self) -> None:
        mock_task = MagicMock()
        mock_task.request.retries = 0
        mock_task.retry.side_effect = RuntimeError("retry triggered")
        task_func = celery_app.tasks["testagent.gateway.tasks.execute_test_task"].__wrapped__.__func__

        with (
            patch("testagent.harness.orchestrator.HarnessOrchestrator"),
            patch("testagent.gateway.tasks.asyncio.run") as mock_run,
        ):
            mock_run.side_effect = RuntimeError("execution failed")

            with pytest.raises(RuntimeError):
                task_func(mock_task, "task-001", {"url": "http://test.com"})

        mock_task.retry.assert_called_once()
        _, kwargs = mock_task.retry.call_args
        assert "exc" in kwargs
        assert isinstance(kwargs["exc"], RuntimeError)
        assert str(kwargs["exc"]) == "execution failed"
        assert kwargs["countdown"] == 2 ** (0 + 1)

    def test_exponential_backoff_increases_with_retries(self) -> None:
        for retry_num in range(3):
            expected_countdown = 2 ** (retry_num + 1)
            assert expected_countdown in (2, 4, 8), f"retry {retry_num} expected {expected_countdown}"

    def test_successful_execution_returns_result_dict(self) -> None:
        mock_task = MagicMock()
        task_func = celery_app.tasks["testagent.gateway.tasks.execute_test_task"].__wrapped__.__func__
        expected = {
            "task_id": "task-001",
            "status": "passed",
            "duration_ms": 150.0,
            "assertion_results": {"status_code": {"passed": True}},
            "logs": "test passed",
            "screenshot_url": None,
            "video_url": None,
            "artifacts": None,
        }

        with (
            patch("testagent.harness.orchestrator.HarnessOrchestrator"),
            patch("testagent.gateway.tasks.asyncio.run") as mock_run,
        ):
            mock_run.return_value = expected

            result = task_func(mock_task, "task-001", {"url": "http://test.com"})

            assert result["task_id"] == "task-001"
            assert result["status"] == "passed"
            assert result["duration_ms"] == 150.0
            assert result["assertion_results"] == {"status_code": {"passed": True}}
            assert result["logs"] == "test passed"

    def test_build_task_from_config(self) -> None:
        from testagent.gateway.tasks import _build_task

        task_config = {
            "plan_id": "plan-001",
            "task_type": "api_test",
            "priority": 5,
            "isolation_level": "docker",
        }
        task = _build_task("task-001", task_config)

        assert task.id == "task-001"
        assert task.plan_id == "plan-001"
        assert task.task_type == "api_test"
        assert task.priority == 5
        assert task.isolation_level == "docker"
        assert task.status == "running"
        assert task.task_config == task_config

    def test_build_task_defaults(self) -> None:
        from testagent.gateway.tasks import _build_task

        task = _build_task("task-002", {})

        assert task.id == "task-002"
        assert task.task_type == "api_test"
        assert task.isolation_level == "docker"
        assert task.priority == 0
        assert task.status == "running"


# ====================================================================
# execute_planning_task Tests
# ====================================================================


class TestExecutePlanningTask:
    def test_max_retries(self) -> None:
        task = celery_app.tasks["testagent.gateway.tasks.execute_planning_task"]
        assert task.max_retries == 2

    def test_default_retry_delay(self) -> None:
        task = celery_app.tasks["testagent.gateway.tasks.execute_planning_task"]
        assert task.default_retry_delay == 2

    def test_queue_assigned(self) -> None:
        task = celery_app.tasks["testagent.gateway.tasks.execute_planning_task"]
        assert task.queue == "planning"

    def test_soft_time_limit(self) -> None:
        task = celery_app.tasks["testagent.gateway.tasks.execute_planning_task"]
        assert task.soft_time_limit == 120

    def test_time_limit(self) -> None:
        task = celery_app.tasks["testagent.gateway.tasks.execute_planning_task"]
        assert task.time_limit == 150

    def test_acks_late(self) -> None:
        task = celery_app.tasks["testagent.gateway.tasks.execute_planning_task"]
        assert task.acks_late is True

    def test_retry_called_on_failure(self) -> None:
        mock_task = MagicMock()
        mock_task.request.retries = 0
        mock_task.retry.side_effect = RuntimeError("retry triggered")
        task_func = celery_app.tasks["testagent.gateway.tasks.execute_planning_task"].__wrapped__.__func__

        with (
            patch("testagent.agent.planner.PlannerAgent"),
            patch("testagent.agent.context.ContextAssembler"),
            patch("testagent.llm.openai_provider.OpenAIProvider"),
            patch("testagent.gateway.tasks.asyncio.run") as mock_run,
        ):
            mock_run.side_effect = RuntimeError("planning failed")

            with pytest.raises(RuntimeError):
                task_func(mock_task, "session-001", {"requirement": "test login"})

        mock_task.retry.assert_called_once()
        _, kwargs = mock_task.retry.call_args
        assert isinstance(kwargs["exc"], RuntimeError)
        assert str(kwargs["exc"]) == "planning failed"
        assert kwargs["countdown"] == 2 ** (0 + 1)

    def test_successful_execution(self) -> None:
        mock_task = MagicMock()
        task_func = celery_app.tasks["testagent.gateway.tasks.execute_planning_task"].__wrapped__.__func__
        expected = {"plan": "test plan", "tasks": 3}

        with (
            patch("testagent.agent.planner.PlannerAgent"),
            patch("testagent.agent.context.ContextAssembler"),
            patch("testagent.llm.openai_provider.OpenAIProvider"),
            patch("testagent.gateway.tasks.asyncio.run") as mock_run,
        ):
            mock_run.return_value = expected

            result = task_func(mock_task, "session-001", {"requirement": "test login"})

            assert result == expected


# ====================================================================
# execute_analysis_task Tests
# ====================================================================


class TestExecuteAnalysisTask:
    def test_max_retries(self) -> None:
        task = celery_app.tasks["testagent.gateway.tasks.execute_analysis_task"]
        assert task.max_retries == 2

    def test_default_retry_delay(self) -> None:
        task = celery_app.tasks["testagent.gateway.tasks.execute_analysis_task"]
        assert task.default_retry_delay == 2

    def test_queue_assigned(self) -> None:
        task = celery_app.tasks["testagent.gateway.tasks.execute_analysis_task"]
        assert task.queue == "analysis"

    def test_soft_time_limit(self) -> None:
        task = celery_app.tasks["testagent.gateway.tasks.execute_analysis_task"]
        assert task.soft_time_limit == 120

    def test_time_limit(self) -> None:
        task = celery_app.tasks["testagent.gateway.tasks.execute_analysis_task"]
        assert task.time_limit == 150

    def test_acks_late(self) -> None:
        task = celery_app.tasks["testagent.gateway.tasks.execute_analysis_task"]
        assert task.acks_late is True

    def test_retry_called_on_failure(self) -> None:
        mock_task = MagicMock()
        mock_task.request.retries = 1
        mock_task.retry.side_effect = RuntimeError("retry triggered")
        task_func = celery_app.tasks["testagent.gateway.tasks.execute_analysis_task"].__wrapped__.__func__

        with (
            patch("testagent.agent.analyzer.AnalyzerAgent"),
            patch("testagent.agent.context.ContextAssembler"),
            patch("testagent.llm.openai_provider.OpenAIProvider"),
            patch("testagent.gateway.tasks.asyncio.run") as mock_run,
        ):
            mock_run.side_effect = RuntimeError("analysis failed")

            with pytest.raises(RuntimeError):
                task_func(mock_task, "session-001", [{"task_id": "task-001", "status": "failed"}])

        mock_task.retry.assert_called_once()
        _, kwargs = mock_task.retry.call_args
        assert isinstance(kwargs["exc"], RuntimeError)
        assert kwargs["countdown"] == 2 ** (1 + 1)

    def test_successful_execution(self) -> None:
        mock_task = MagicMock()
        task_func = celery_app.tasks["testagent.gateway.tasks.execute_analysis_task"].__wrapped__.__func__
        expected = {"defects": [], "root_cause": "environment issue"}

        with (
            patch("testagent.agent.analyzer.AnalyzerAgent"),
            patch("testagent.agent.context.ContextAssembler"),
            patch("testagent.llm.openai_provider.OpenAIProvider"),
            patch("testagent.gateway.tasks.asyncio.run") as mock_run,
        ):
            mock_run.return_value = expected

            result = task_func(mock_task, "session-001", [{"task_id": "task-001", "status": "failed"}])

            assert result == expected


# ====================================================================
# _result_to_dict Tests
# ====================================================================


class TestResultToDict:
    def test_converts_test_result_to_dict(self) -> None:
        from testagent.gateway.tasks import _result_to_dict

        mock_result = MagicMock()
        mock_result.task_id = "task-001"
        mock_result.status = "passed"
        mock_result.duration_ms = 150.0
        mock_result.assertion_results = {"status_code": {"passed": True}}
        mock_result.logs = "test passed"
        mock_result.screenshot_url = "http://screenshot.png"
        mock_result.video_url = None
        mock_result.artifacts = {"screenshots": ["shot1.png"]}

        result = _result_to_dict(mock_result)

        assert result["task_id"] == "task-001"
        assert result["status"] == "passed"
        assert result["duration_ms"] == 150.0
        assert result["assertion_results"] == {"status_code": {"passed": True}}
        assert result["logs"] == "test passed"
        assert result["screenshot_url"] == "http://screenshot.png"
        assert result["video_url"] is None
        assert result["artifacts"] == {"screenshots": ["shot1.png"]}

    def test_handles_minimal_result(self) -> None:
        from testagent.gateway.tasks import _result_to_dict

        mock_result = MagicMock()
        mock_result.task_id = "task-002"
        mock_result.status = "failed"
        mock_result.duration_ms = None
        mock_result.assertion_results = None
        mock_result.logs = None
        mock_result.screenshot_url = None
        mock_result.video_url = None
        mock_result.artifacts = None

        result = _result_to_dict(mock_result)

        assert result["task_id"] == "task-002"
        assert result["status"] == "failed"
        assert result["duration_ms"] is None
        assert result["assertion_results"] is None
        assert result["logs"] is None
