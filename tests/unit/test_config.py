from __future__ import annotations

import os
from unittest.mock import patch

from testagent.config.defaults import (
    DEFECT_CATEGORIES,
    DEFECT_SEVERITIES,
    ISOLATION_LEVELS,
    MESSAGE_TYPES,
    RRF_K,
    SESSION_STATUSES,
    TASK_STATUSES,
)
from testagent.config.settings import TestAgentSettings, get_settings, reset_settings


class TestTestAgentSettingsDefaults:
    def test_app_defaults(self) -> None:
        s = TestAgentSettings()
        assert s.app_name == "TestAgent"
        assert s.app_version == "0.1.0"
        assert s.debug is False

    def test_database_defaults(self) -> None:
        s = TestAgentSettings()
        assert s.database_url == "sqlite+aiosqlite:///./testagent.db"
        assert s.database_echo is False
        assert s.database_backend == "sqlite"
        assert s.postgres_host == "localhost"
        assert s.postgres_port == 5432
        assert s.postgres_db == "testagent"
        assert s.postgres_user == "testagent"
        assert s.postgres_password.get_secret_value() == ""
        assert s.postgres_pool_size == 10
        assert s.postgres_max_overflow == 20
        assert s.postgres_pool_recycle == 3600

    def test_redis_defaults(self) -> None:
        s = TestAgentSettings()
        assert s.redis_url == "redis://localhost:6379/0"

    def test_celery_defaults(self) -> None:
        s = TestAgentSettings()
        assert s.celery_broker_url == "redis://localhost:6379/0"
        assert s.celery_result_backend == "redis://localhost:6379/1"

    def test_llm_defaults(self) -> None:
        s = TestAgentSettings()
        assert s.llm_provider == "openai"
        assert s.openai_api_key.get_secret_value() == ""
        assert s.openai_model == "gpt-4o"
        assert s.local_model_url == "http://localhost:11434"

    def test_rag_defaults(self) -> None:
        s = TestAgentSettings()
        assert s.chroma_persist_dir == "./chroma_data"
        assert s.meilisearch_url == "http://localhost:7700"
        assert s.meilisearch_api_key.get_secret_value() == "testagent-dev-master-key"
        assert s.embedding_mode == "local"
        assert s.embedding_model == "BAAI/bge-large-zh-v1.5"
        assert s.openai_embedding_model == "text-embedding-3-small"

    def test_agent_defaults(self) -> None:
        s = TestAgentSettings()
        assert s.agent_max_rounds == 50
        assert s.agent_token_threshold == 100000

    def test_harness_defaults(self) -> None:
        s = TestAgentSettings()
        assert s.default_isolation_level == "docker"
        assert s.docker_timeout_api == 60
        assert s.docker_timeout_web == 120

    def test_security_defaults(self) -> None:
        s = TestAgentSettings()
        assert s.data_retention_days == 90


class TestEnvOverride:
    def test_env_override_app_name(self) -> None:
        with patch.dict(os.environ, {"TESTAGENT_APP_NAME": "CustomAgent"}):
            s = TestAgentSettings()
            assert s.app_name == "CustomAgent"

    def test_env_override_debug(self) -> None:
        with patch.dict(os.environ, {"TESTAGENT_DEBUG": "true"}):
            s = TestAgentSettings()
            assert s.debug is True

    def test_env_override_database_url(self) -> None:
        with patch.dict(os.environ, {"TESTAGENT_DATABASE_URL": "sqlite+aiosqlite:///./custom.db"}):
            s = TestAgentSettings()
            assert s.database_url == "sqlite+aiosqlite:///./custom.db"

    def test_env_override_redis_url(self) -> None:
        with patch.dict(os.environ, {"TESTAGENT_REDIS_URL": "redis://redis-host:6379/2"}):
            s = TestAgentSettings()
            assert s.redis_url == "redis://redis-host:6379/2"

    def test_env_override_celery(self) -> None:
        with patch.dict(
            os.environ,
            {
                "TESTAGENT_CELERY_BROKER_URL": "redis://broker:6379/0",
                "TESTAGENT_CELERY_RESULT_BACKEND": "redis://backend:6379/1",
            },
        ):
            s = TestAgentSettings()
            assert s.celery_broker_url == "redis://broker:6379/0"
            assert s.celery_result_backend == "redis://backend:6379/1"

    def test_env_override_openai_api_key(self) -> None:
        with patch.dict(os.environ, {"TESTAGENT_OPENAI_API_KEY": "sk-test-key-12345"}):
            s = TestAgentSettings()
            assert s.openai_api_key.get_secret_value() == "sk-test-key-12345"

    def test_env_override_llm_provider(self) -> None:
        with patch.dict(os.environ, {"TESTAGENT_LLM_PROVIDER": "local"}):
            s = TestAgentSettings()
            assert s.llm_provider == "local"

    def test_env_override_agent_max_rounds(self) -> None:
        with patch.dict(os.environ, {"TESTAGENT_AGENT_MAX_ROUNDS": "20"}):
            s = TestAgentSettings()
            assert s.agent_max_rounds == 20

    def test_env_override_isolation_level(self) -> None:
        with patch.dict(os.environ, {"TESTAGENT_DEFAULT_ISOLATION_LEVEL": "microvm"}):
            s = TestAgentSettings()
            assert s.default_isolation_level == "microvm"

    def test_env_prefix_case_insensitive(self) -> None:
        with patch.dict(os.environ, {"testagent_app_name": "LowerCaseAgent"}):
            s = TestAgentSettings()
            assert s.app_name == "LowerCaseAgent"

    def test_env_override_embedding_mode(self) -> None:
        with patch.dict(os.environ, {"TESTAGENT_EMBEDDING_MODE": "api"}):
            s = TestAgentSettings()
            assert s.embedding_mode == "api"

    def test_env_override_meilisearch_api_key(self) -> None:
        with patch.dict(os.environ, {"TESTAGENT_MEILISEARCH_API_KEY": "secret-key-abc"}):
            s = TestAgentSettings()
            assert s.meilisearch_api_key.get_secret_value() == "secret-key-abc"


class TestMaskSecrets:
    def test_secrets_are_masked(self) -> None:
        with patch.dict(
            os.environ,
            {
                "TESTAGENT_OPENAI_API_KEY": "sk-super-secret-key",
                "TESTAGENT_MEILISEARCH_API_KEY": "meili-secret",
            },
        ):
            s = TestAgentSettings()
            masked = s.mask_secrets()
            assert masked["openai_api_key"] == "***"
            assert masked["meilisearch_api_key"] == "***"

    def test_non_secrets_are_visible(self) -> None:
        s = TestAgentSettings()
        masked = s.mask_secrets()
        assert masked["app_name"] == "TestAgent"
        assert masked["app_version"] == "0.1.0"
        assert masked["debug"] == "False"
        assert masked["database_url"] == "sqlite+aiosqlite:///./testagent.db"
        assert masked["redis_url"] == "redis://localhost:6379/0"
        assert masked["llm_provider"] == "openai"
        assert masked["agent_max_rounds"] == "50"
        assert masked["default_isolation_level"] == "docker"

    def test_mask_secrets_covers_all_fields(self) -> None:
        s = TestAgentSettings()
        masked = s.mask_secrets()
        assert set(masked.keys()) == set(TestAgentSettings.model_fields.keys())

    def test_mask_secrets_default_empty_keys(self) -> None:
        s = TestAgentSettings()
        masked = s.mask_secrets()
        assert masked["openai_api_key"] == "***"
        assert masked["meilisearch_api_key"] == "***"


class TestGetSettings:
    def setup_method(self) -> None:
        reset_settings()

    def teardown_method(self) -> None:
        reset_settings()

    def test_get_settings_returns_singleton(self) -> None:
        s1 = get_settings()
        s2 = get_settings()
        assert s1 is s2

    def test_reset_settings_clears_singleton(self) -> None:
        s1 = get_settings()
        reset_settings()
        s2 = get_settings()
        assert s1 is not s2


class TestDefaults:
    def test_session_statuses_complete(self) -> None:
        assert SESSION_STATUSES == ("pending", "planning", "executing", "analyzing", "completed", "failed")

    def test_session_statuses_count(self) -> None:
        assert len(SESSION_STATUSES) == 6

    def test_task_statuses_complete(self) -> None:
        assert TASK_STATUSES == ("queued", "running", "passed", "failed", "flaky", "skipped", "retrying")

    def test_task_statuses_count(self) -> None:
        assert len(TASK_STATUSES) == 7

    def test_defect_categories_complete(self) -> None:
        assert DEFECT_CATEGORIES == ("bug", "flaky", "environment", "configuration")

    def test_defect_categories_count(self) -> None:
        assert len(DEFECT_CATEGORIES) == 4

    def test_defect_severities_complete(self) -> None:
        assert DEFECT_SEVERITIES == ("critical", "major", "minor", "trivial")

    def test_defect_severities_count(self) -> None:
        assert len(DEFECT_SEVERITIES) == 4

    def test_isolation_levels_complete(self) -> None:
        assert ISOLATION_LEVELS == ("docker", "microvm", "local")

    def test_isolation_levels_count(self) -> None:
        assert len(ISOLATION_LEVELS) == 3

    def test_message_types_complete(self) -> None:
        assert MESSAGE_TYPES == ("task_assignment", "result_report", "query", "notification", "ack", "error")

    def test_message_types_count(self) -> None:
        assert len(MESSAGE_TYPES) == 6

    def test_rrf_k_value(self) -> None:
        assert RRF_K == 60

    _ALL_TUPLE_CONSTS = (
        SESSION_STATUSES,
        TASK_STATUSES,
        DEFECT_CATEGORIES,
        DEFECT_SEVERITIES,
        ISOLATION_LEVELS,
        MESSAGE_TYPES,
    )

    def test_all_defaults_are_tuples(self) -> None:
        for const in self._ALL_TUPLE_CONSTS:
            assert isinstance(const, tuple)

    def test_all_defaults_elements_are_str(self) -> None:
        for const in self._ALL_TUPLE_CONSTS:
            for item in const:
                assert isinstance(item, str)

    def test_no_duplicates_in_status_tuples(self) -> None:
        for const in self._ALL_TUPLE_CONSTS:
            assert len(const) == len(set(const))


class TestGetDatabaseUrl:
    def test_sqlite_backend_returns_sqlite_url(self) -> None:
        s = TestAgentSettings()
        assert s.get_database_url() == "sqlite+aiosqlite:///./testagent.db"

    def test_postgresql_backend_returns_postgresql_url(self) -> None:
        with patch.dict(
            os.environ,
            {
                "TESTAGENT_DATABASE_BACKEND": "postgresql",
                "TESTAGENT_POSTGRES_PASSWORD": "s3cret",
                "TESTAGENT_POSTGRES_HOST": "db.example.com",
                "TESTAGENT_POSTGRES_PORT": "5433",
                "TESTAGENT_POSTGRES_DB": "mydb",
                "TESTAGENT_POSTGRES_USER": "admin",
            },
        ):
            s = TestAgentSettings()
            assert s.get_database_url() == "postgresql+asyncpg://admin:s3cret@db.example.com:5433/mydb"

    def test_postgresql_backend_with_empty_password(self) -> None:
        with patch.dict(
            os.environ,
            {
                "TESTAGENT_DATABASE_BACKEND": "postgresql",
                "TESTAGENT_POSTGRES_PASSWORD": "",
            },
        ):
            s = TestAgentSettings()
            assert s.get_database_url() == "postgresql+asyncpg://testagent:@localhost:5432/testagent"

    def test_env_override_database_backend_to_postgresql(self) -> None:
        with patch.dict(os.environ, {"TESTAGENT_DATABASE_BACKEND": "postgresql"}):
            s = TestAgentSettings()
            assert s.database_backend == "postgresql"
            url = s.get_database_url()
            assert url.startswith("postgresql+asyncpg://")

    def test_sqlite_backend_explicit_env(self) -> None:
        with patch.dict(os.environ, {"TESTAGENT_DATABASE_BACKEND": "sqlite"}):
            s = TestAgentSettings()
            assert s.get_database_url() == "sqlite+aiosqlite:///./testagent.db"


class TestPostgresPasswordMasking:
    def test_postgres_password_masked_in_mask_secrets(self) -> None:
        with patch.dict(os.environ, {"TESTAGENT_POSTGRES_PASSWORD": "super-secret-pg-pass"}):
            s = TestAgentSettings()
            masked = s.mask_secrets()
            assert masked["postgres_password"] == "***"

    def test_postgres_password_default_masked(self) -> None:
        s = TestAgentSettings()
        masked = s.mask_secrets()
        assert masked["postgres_password"] == "***"

    def test_postgres_password_not_leaked_in_url(self) -> None:
        with patch.dict(
            os.environ,
            {
                "TESTAGENT_DATABASE_BACKEND": "postgresql",
                "TESTAGENT_POSTGRES_PASSWORD": "leak-check-pass",
            },
        ):
            s = TestAgentSettings()
            masked = s.mask_secrets()
            for value in masked.values():
                assert "leak-check-pass" not in value
