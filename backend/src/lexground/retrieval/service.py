from __future__ import annotations

import re
import time
import uuid

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from lexground.config import Settings
from lexground.db.models import text_search_config
from lexground.retrieval.embedder import Embedder
from lexground.retrieval.fusion import reciprocal_rank_fusion
from lexground.retrieval.types import RetrievalResult, RetrievedChunk

_LEXICAL_SQL = text(
    """
    SELECT c.id, c.citation, c.text, c.language, c.unit_type,
           d.title, d.source_id, d.source_url,
           ts_rank_cd(c.search_vector, query) AS score
    FROM chunks c
    JOIN documents d ON d.id = c.document_id
    CROSS JOIN to_tsquery(CAST(:config AS regconfig), :tsquery) AS query
    WHERE c.search_vector @@ query
      AND (CAST(:language AS text) IS NULL OR c.language = CAST(:language AS text))
    ORDER BY score DESC, c.citation
    LIMIT :limit
    """
)

_DENSE_SQL = text(
    """
    SELECT c.id, c.citation, c.text, c.language, c.unit_type,
           d.title, d.source_id, d.source_url,
           1 - (c.embedding <=> CAST(:embedding AS vector)) AS score
    FROM chunks c
    JOIN documents d ON d.id = c.document_id
    WHERE c.embedding IS NOT NULL
      AND (CAST(:language AS text) IS NULL OR c.language = CAST(:language AS text))
    ORDER BY c.embedding <=> CAST(:embedding AS vector), c.citation
    LIMIT :limit
    """
)


_TOKEN = re.compile(r"[^\W\d_]+|\d+", re.UNICODE)
MIN_TOKEN_LENGTH = 3


def build_tsquery(question: str) -> str:
    """Turn a natural-language question into an OR-of-terms tsquery."""
    tokens = {
        token.lower()
        for token in _TOKEN.findall(question)
        if len(token) >= MIN_TOKEN_LENGTH or token.isdigit()
    }
    return " | ".join(sorted(tokens))


def _to_chunk(row: object, *, lexical: bool) -> RetrievedChunk:
    mapping = row._mapping  # type: ignore[attr-defined]
    score = float(mapping["score"])
    return RetrievedChunk(
        chunk_id=mapping["id"],
        citation=mapping["citation"],
        text=mapping["text"],
        document_title=mapping["title"],
        source_id=mapping["source_id"],
        language=mapping["language"],
        source_url=mapping["source_url"],
        unit_type=mapping["unit_type"],
        lexical_score=score if lexical else 0.0,
        dense_score=0.0 if lexical else score,
    )


class HybridRetriever:
    def __init__(self, settings: Settings, embedder: Embedder) -> None:
        self._settings = settings
        self._embedder = embedder

    async def retrieve(
        self,
        session: AsyncSession,
        question: str,
        *,
        language: str | None = None,
        top_k: int | None = None,
    ) -> RetrievalResult:
        started = time.perf_counter()
        limit = self._settings.retrieval_candidates
        top_k = top_k or self._settings.retrieval_top_k

        tsquery = build_tsquery(question)
        lexical: list[RetrievedChunk] = []
        if tsquery:
            lexical_rows = await session.execute(
                _LEXICAL_SQL,
                {
                    "tsquery": tsquery,
                    "config": text_search_config(language or "en"),
                    "language": language,
                    "limit": limit,
                },
            )
            lexical = [_to_chunk(row, lexical=True) for row in lexical_rows]

        embedding = self._embedder.embed_query(question)
        dense_rows = await session.execute(
            _DENSE_SQL,
            {"embedding": str(embedding), "language": language, "limit": limit},
        )
        dense = [_to_chunk(row, lexical=False) for row in dense_rows]

        fused = reciprocal_rank_fusion(lexical, dense, k=self._settings.rrf_k)
        selected = fused[:top_k]

        return RetrievalResult(
            chunks=selected,
            lexical_candidates=len(lexical),
            dense_candidates=len(dense),
            latency_ms=int((time.perf_counter() - started) * 1000),
            weak_context=self._is_weak(lexical, dense),
        )

    def _is_weak(self, lexical: list[RetrievedChunk], dense: list[RetrievedChunk]) -> bool:
        """Answerability is judged on absolute match strength, never on fused rank."""
        best_lexical = max((chunk.lexical_score for chunk in lexical), default=0.0)
        best_dense = max((chunk.dense_score for chunk in dense), default=0.0)
        return (
            best_lexical < self._settings.min_lexical_score
            and best_dense < self._settings.min_dense_similarity
        )

    async def fetch_chunk_ids(self, session: AsyncSession) -> list[uuid.UUID]:
        rows = await session.execute(text("SELECT id FROM chunks"))
        return [row[0] for row in rows]
