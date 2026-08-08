from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

EmbeddingBackend = Literal["fastembed", "hash"]
SynthesisBackend = Literal["claude", "extractive"]

ASYNC_DRIVER_PREFIX = "postgresql+asyncpg://"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="LEXGROUND_", env_file=".env", extra="ignore")

    database_url: str = Field(
        default=f"{ASYNC_DRIVER_PREFIX}lexground:lexground@localhost:5432/lexground"
    )
    db_pool_size: int = 10

    @field_validator("database_url")
    @classmethod
    def _require_async_driver(cls, value: str) -> str:
        """A sync DSN fails deep inside the first query with an opaque greenlet error."""
        if not value.startswith(ASYNC_DRIVER_PREFIX):
            raise ValueError(f"database_url must start with {ASYNC_DRIVER_PREFIX}")
        return value

    anthropic_api_key: str | None = None
    synthesis_model: str = "claude-opus-5"
    judge_model: str = "claude-opus-5"
    synthesis_effort: Literal["low", "medium", "high", "xhigh", "max"] = "medium"

    embedding_backend: EmbeddingBackend = "hash"
    embedding_model: str = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
    embedding_dimensions: int = 384

    retrieval_candidates: int = 40
    retrieval_top_k: int = 8
    rrf_k: int = 60

    min_lexical_score: float = 0.05
    min_dense_similarity: float = 0.15
    """Backstop floors for an index that returned nothing usable — not the abstention mechanism."""

    max_context_chunks: int = 8
    request_timeout_seconds: float = 120.0

    cors_origins: list[str] = ["http://localhost:3000"]

    @property
    def synthesis_backend(self) -> SynthesisBackend:
        return "claude" if self.anthropic_api_key else "extractive"


@lru_cache
def get_settings() -> Settings:
    return Settings()
