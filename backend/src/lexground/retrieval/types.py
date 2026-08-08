from __future__ import annotations

import uuid
from dataclasses import dataclass, field


@dataclass(slots=True)
class RetrievedChunk:
    chunk_id: uuid.UUID
    citation: str
    text: str
    document_title: str
    source_id: str
    language: str
    source_url: str
    unit_type: str

    lexical_rank: int | None = None
    dense_rank: int | None = None
    lexical_score: float = 0.0
    dense_score: float = 0.0
    fused_score: float = 0.0

    def provenance(self) -> dict[str, object]:
        """Everything the retrieval inspector needs to explain why this chunk surfaced."""
        return {
            "chunk_id": str(self.chunk_id),
            "citation": self.citation,
            "source_id": self.source_id,
            "document_title": self.document_title,
            "source_url": self.source_url,
            "language": self.language,
            "unit_type": self.unit_type,
            "lexical_rank": self.lexical_rank,
            "dense_rank": self.dense_rank,
            "lexical_score": round(self.lexical_score, 6),
            "dense_score": round(self.dense_score, 6),
            "fused_score": round(self.fused_score, 6),
        }


@dataclass(slots=True)
class RetrievalResult:
    chunks: list[RetrievedChunk]
    lexical_candidates: int = 0
    dense_candidates: int = 0
    latency_ms: int = 0
    weak_context: bool = field(default=False)
    """True when the best fused score fell below the answerability floor."""
