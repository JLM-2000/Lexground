from __future__ import annotations

import time
import uuid
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from lexground.config import Settings
from lexground.db.models import QueryTrace
from lexground.observability.metrics import (
    ANSWER_COST,
    QUERY_LATENCY,
    QUERY_TOTAL,
    RETRIEVAL_LATENCY,
)
from lexground.retrieval.service import HybridRetriever
from lexground.retrieval.types import RetrievalResult
from lexground.synthesis.answerer import Answerer
from lexground.synthesis.schema import GroundedAnswer


@dataclass(slots=True)
class QueryOutcome:
    trace_id: uuid.UUID
    question: str
    answer: GroundedAnswer
    retrieval: RetrievalResult
    latency_ms: int
    input_tokens: int
    output_tokens: int
    cost_usd: float
    model: str


class QueryService:
    """The one path a question takes."""

    def __init__(self, settings: Settings, retriever: HybridRetriever, answerer: Answerer) -> None:
        self._settings = settings
        self._retriever = retriever
        self._answerer = answerer

    async def ask(
        self,
        session: AsyncSession,
        question: str,
        *,
        language: str | None = None,
        persist: bool = True,
    ) -> QueryOutcome:
        started = time.perf_counter()

        retrieval = await self._retriever.retrieve(session, question, language=language)
        RETRIEVAL_LATENCY.observe(retrieval.latency_ms / 1000)

        outcome = await self._answerer.answer(question, retrieval)
        latency_ms = int((time.perf_counter() - started) * 1000)

        QUERY_LATENCY.observe(latency_ms / 1000)
        QUERY_TOTAL.labels(
            answered=str(outcome.answer.answerable).lower(),
            language=language or "auto",
        ).inc()
        ANSWER_COST.inc(outcome.cost_usd)

        trace = QueryTrace(
            question=question,
            language=language,
            answered=outcome.answer.answerable,
            refusal_reason=outcome.answer.refusal_reason or None,
            answer=outcome.answer.answer or None,
            citations={"items": [citation.model_dump() for citation in outcome.answer.citations]},
            retrieved={"chunks": [chunk.provenance() for chunk in retrieval.chunks]},
            latency_ms=latency_ms,
            input_tokens=outcome.input_tokens,
            output_tokens=outcome.output_tokens,
            cost_usd=outcome.cost_usd,
        )
        if persist:
            session.add(trace)
            await session.flush()

        return QueryOutcome(
            trace_id=trace.id,
            question=question,
            answer=outcome.answer,
            retrieval=retrieval,
            latency_ms=latency_ms,
            input_tokens=outcome.input_tokens,
            output_tokens=outcome.output_tokens,
            cost_usd=outcome.cost_usd,
            model=outcome.model,
        )
