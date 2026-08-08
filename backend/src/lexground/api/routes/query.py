from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select

from lexground.api.deps import QueryServiceDep, SessionDep
from lexground.db.models import QueryTrace
from lexground.synthesis.schema import Citation

router = APIRouter(prefix="/api", tags=["query"])


class QueryRequest(BaseModel):
    question: str = Field(min_length=3, max_length=2000)
    language: str | None = Field(default=None, pattern="^[a-z]{2}$")


class RetrievedChunkView(BaseModel):
    chunk_id: uuid.UUID
    citation: str
    document_title: str
    source_url: str
    text: str
    lexical_rank: int | None
    dense_rank: int | None
    fused_score: float


class QueryResponse(BaseModel):
    trace_id: uuid.UUID
    question: str
    answerable: bool
    answer: str
    refusal_reason: str
    citations: list[Citation]
    retrieved: list[RetrievedChunkView]
    latency_ms: int
    cost_usd: float
    model: str


@router.post("/query", response_model=QueryResponse)
async def ask(
    payload: QueryRequest, session: SessionDep, service: QueryServiceDep
) -> QueryResponse:
    outcome = await service.ask(session, payload.question, language=payload.language)
    return QueryResponse(
        trace_id=outcome.trace_id,
        question=outcome.question,
        answerable=outcome.answer.answerable,
        answer=outcome.answer.answer,
        refusal_reason=outcome.answer.refusal_reason,
        citations=outcome.answer.citations,
        retrieved=[
            RetrievedChunkView(
                chunk_id=chunk.chunk_id,
                citation=chunk.citation,
                document_title=chunk.document_title,
                source_url=chunk.source_url,
                text=chunk.text,
                lexical_rank=chunk.lexical_rank,
                dense_rank=chunk.dense_rank,
                fused_score=round(chunk.fused_score, 6),
            )
            for chunk in outcome.retrieval.chunks
        ],
        latency_ms=outcome.latency_ms,
        cost_usd=round(outcome.cost_usd, 6),
        model=outcome.model,
    )


@router.get("/traces/{trace_id}")
async def get_trace(trace_id: uuid.UUID, session: SessionDep) -> dict[str, object]:
    trace = await session.scalar(select(QueryTrace).where(QueryTrace.id == trace_id))
    if trace is None:
        raise HTTPException(status_code=404, detail="trace not found")
    return {
        "id": str(trace.id),
        "question": trace.question,
        "answered": trace.answered,
        "answer": trace.answer,
        "refusal_reason": trace.refusal_reason,
        "citations": trace.citations.get("items", []),
        "retrieved": trace.retrieved.get("chunks", []),
        "latency_ms": trace.latency_ms,
        "cost_usd": trace.cost_usd,
        "created_at": trace.created_at.isoformat(),
    }


@router.get("/traces")
async def list_traces(session: SessionDep, limit: int = 25) -> list[dict[str, object]]:
    rows = await session.scalars(
        select(QueryTrace).order_by(QueryTrace.created_at.desc()).limit(min(limit, 100))
    )
    return [
        {
            "id": str(trace.id),
            "question": trace.question,
            "answered": trace.answered,
            "latency_ms": trace.latency_ms,
            "created_at": trace.created_at.isoformat(),
        }
        for trace in rows
    ]
