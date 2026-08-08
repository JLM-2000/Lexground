from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any, ClassVar

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    CheckConstraint,
    Computed,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

EMBEDDING_DIMENSIONS = 384

TEXT_SEARCH_CONFIG = {
    "en": "english",
    "es": "spanish",
    "fr": "french",
    "de": "german",
}
DEFAULT_TEXT_SEARCH_CONFIG = "english"

# to_tsvector(regconfig, text) is immutable and so is a CASE over a stored column,
# which is what lets the stemmed vector be a generated column instead of a trigger.
_SEARCH_VECTOR_EXPRESSION = """
to_tsvector(
    CASE language
        WHEN 'es' THEN 'spanish'::regconfig
        WHEN 'fr' THEN 'french'::regconfig
        WHEN 'de' THEN 'german'::regconfig
        ELSE 'english'::regconfig
    END,
    text
)
"""


def text_search_config(language: str) -> str:
    """The stemmer to parse a query with. It must match the one used to build the
    stored vector, or stemmed terms will not line up."""
    return TEXT_SEARCH_CONFIG.get(language, DEFAULT_TEXT_SEARCH_CONFIG)


def _utcnow() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    type_annotation_map: ClassVar[dict[Any, Any]] = {dict[str, Any]: JSONB}


class Document(Base):
    """A legal act. One row per (celex_id, language, version)."""

    __tablename__ = "documents"
    __table_args__ = (
        UniqueConstraint("celex_id", "language", "version", name="uq_document_identity"),
        CheckConstraint("version IN ('original', 'consolidated')", name="ck_document_version"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    celex_id: Mapped[str] = mapped_column(String(32), index=True)
    title: Mapped[str] = mapped_column(Text)
    short_title: Mapped[str] = mapped_column(String(128))
    language: Mapped[str] = mapped_column(String(2), index=True)
    version: Mapped[str] = mapped_column(String(16), default="original")
    jurisdiction: Mapped[str] = mapped_column(String(8), default="EU")
    source_url: Mapped[str] = mapped_column(Text)
    adopted_on: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ingested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    chunks: Mapped[list[Chunk]] = relationship(
        back_populates="document", cascade="all, delete-orphan"
    )


class Chunk(Base):
    """A structure-aware span of a legal act, addressable by its own citation."""

    __tablename__ = "chunks"
    __table_args__ = (
        UniqueConstraint("document_id", "ordinal", name="uq_chunk_ordinal"),
        Index(
            "ix_chunks_search_vector",
            "search_vector",
            postgresql_using="gin",
        ),
        Index(
            "ix_chunks_embedding_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_with={"m": 16, "ef_construction": 64},
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    document_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), index=True
    )
    ordinal: Mapped[int] = mapped_column(Integer)

    unit_type: Mapped[str] = mapped_column(String(16))
    """article | recital | annex | preamble"""
    unit_number: Mapped[str | None] = mapped_column(String(16))
    paragraph: Mapped[str | None] = mapped_column(String(16))
    heading: Mapped[str | None] = mapped_column(Text)
    citation: Mapped[str] = mapped_column(Text)
    """Human-readable pin cite, e.g. "GDPR Art. 22(1)"."""

    text: Mapped[str] = mapped_column(Text)
    token_estimate: Mapped[int] = mapped_column(Integer)
    language: Mapped[str] = mapped_column(String(2))

    search_vector: Mapped[str] = mapped_column(
        TSVECTOR,
        Computed(_SEARCH_VECTOR_EXPRESSION, persisted=True),
    )
    embedding: Mapped[list[float] | None] = mapped_column(Vector(EMBEDDING_DIMENSIONS))

    document: Mapped[Document] = relationship(back_populates="chunks")


class QueryTrace(Base):
    """One answered query, retained so the retrieval inspector can replay it."""

    __tablename__ = "query_traces"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    question: Mapped[str] = mapped_column(Text)
    language: Mapped[str | None] = mapped_column(String(2))
    """The language filter that was applied, or NULL when the query searched all of them."""
    answered: Mapped[bool] = mapped_column(default=False)
    refusal_reason: Mapped[str | None] = mapped_column(Text)
    answer: Mapped[str | None] = mapped_column(Text)
    citations: Mapped[dict[str, Any]] = mapped_column(default=dict)
    retrieved: Mapped[dict[str, Any]] = mapped_column(default=dict)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, server_default=func.now(), index=True
    )


class EvalRun(Base):
    """An evaluation pass over the golden set. The CI gate reads these rows."""

    __tablename__ = "eval_runs"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    git_sha: Mapped[str | None] = mapped_column(String(40))
    index_version: Mapped[str] = mapped_column(String(64))
    case_count: Mapped[int] = mapped_column(Integer, default=0)
    metrics: Mapped[dict[str, Any]] = mapped_column(default=dict)
    passed: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, server_default=func.now(), index=True
    )

    cases: Mapped[list[EvalCase]] = relationship(back_populates="run", cascade="all, delete-orphan")


class EvalCase(Base):
    __tablename__ = "eval_cases"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("eval_runs.id", ondelete="CASCADE"), index=True
    )
    case_id: Mapped[str] = mapped_column(String(64))
    question: Mapped[str] = mapped_column(Text)
    language: Mapped[str] = mapped_column(String(2))
    expected_citations: Mapped[dict[str, Any]] = mapped_column(default=dict)
    actual_citations: Mapped[dict[str, Any]] = mapped_column(default=dict)
    scores: Mapped[dict[str, Any]] = mapped_column(default=dict)
    answer: Mapped[str | None] = mapped_column(Text)
    judge_rationale: Mapped[str | None] = mapped_column(Text)

    run: Mapped[EvalRun] = relationship(back_populates="cases")
