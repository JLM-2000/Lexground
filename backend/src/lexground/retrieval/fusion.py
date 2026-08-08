from __future__ import annotations

import uuid

from lexground.retrieval.types import RetrievedChunk


def reciprocal_rank_fusion(
    lexical: list[RetrievedChunk],
    dense: list[RetrievedChunk],
    *,
    k: int = 60,
) -> list[RetrievedChunk]:
    """Fuse two ranked lists by reciprocal rank.

    RRF is used rather than a weighted score blend because BM25 ranks and cosine
    similarities are not on a comparable scale, and any fixed blend weight would
    need re-tuning every time either side changes. Rank position is stable.
    """
    merged: dict[uuid.UUID, RetrievedChunk] = {}

    for rank, chunk in enumerate(lexical, start=1):
        chunk.lexical_rank = rank
        merged[chunk.chunk_id] = chunk

    for rank, chunk in enumerate(dense, start=1):
        existing = merged.get(chunk.chunk_id)
        if existing is None:
            chunk.dense_rank = rank
            merged[chunk.chunk_id] = chunk
        else:
            existing.dense_rank = rank
            existing.dense_score = chunk.dense_score

    for chunk in merged.values():
        score = 0.0
        if chunk.lexical_rank is not None:
            score += 1.0 / (k + chunk.lexical_rank)
        if chunk.dense_rank is not None:
            score += 1.0 / (k + chunk.dense_rank)
        chunk.fused_score = score

    return sorted(
        merged.values(),
        key=lambda chunk: (-chunk.fused_score, chunk.citation),
    )
