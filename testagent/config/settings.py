from __future__ import annotations

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

_SECRET_FIELDS = frozenset(
    {
        "openai_api_key",
        "meilisearch_api_key",
        "postgres_password",
    }
)


class TestAgentSettings(BaseSettings):
    __test__ = False

    app_name: str = "TestAgent"
    app_version: str = "0.1.0"
    debug: bool = False

    database_url: str = "sqlite+aiosqlite:///./testagent.db"
    database_echo: bool = False
    database_backend: str = "sqlite"

    postgres_host: str = "localhost"
    postgres_port: int = 5432
    postgres_db: str = "testagent"
    postgres_user: str = "testagent"
    postgres_password: SecretStr = SecretStr("")
    postgres_pool_size: int = 10
    postgres_max_overflow: int = 20
    postgres_pool_recycle: int = 3600

    redis_url: str = "redis://localhost:6379/0"

    celery_broker_url: str = "redis://localhost:6379/0"
    celery_result_backend: str = "redis://localhost:6379/1"

    llm_provider: str = "openai"
    openai_api_key: SecretStr = SecretStr("")
    openai_model: str = "gpt-4o"
    local_model_url: str = "http://localhost:11434"

    chroma_persist_dir: str = "./chroma_data"
    vector_store_backend: str = "chromadb"
    milvus_host: str = "localhost"
    milvus_port: int = 19530
    milvus_collection_prefix: str = "testagent_"
    meilisearch_url: str = "http://localhost:7700"
    meilisearch_api_key: SecretStr = SecretStr("testagent-dev-master-key")
    embedding_mode: str = "local"
    embedding_model: str = "BAAI/bge-large-zh-v1.5"
    openai_embedding_model: str = "text-embedding-3-small"

    reranker_enabled: bool = False
    reranker_model: str = "BAAI/bge-reranker-large"

    agent_max_rounds: int = 50
    agent_token_threshold: int = 100000

    default_isolation_level: str = "docker"
    docker_timeout_api: int = 60
    docker_timeout_web: int = 120

    repo_path: str = ""
    data_retention_days: int = 90

    def get_database_url(self) -> str:
        if self.database_backend == "postgresql":
            password = self.postgres_password.get_secret_value()
            return f"postgresql+asyncpg://{self.postgres_user}:{password}@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        return self.database_url

    model_config = SettingsConfigDict(
        env_prefix="TESTAGENT_",
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    def mask_secrets(self) -> dict[str, str]:
        return {
            field: "***" if field in _SECRET_FIELDS else str(getattr(self, field))
            for field in self.__class__.model_fields
        }


_settings: TestAgentSettings | None = None


def get_settings() -> TestAgentSettings:
    global _settings
    if _settings is None:
        _settings = TestAgentSettings()
    return _settings


def reset_settings() -> None:
    global _settings
    _settings = None
