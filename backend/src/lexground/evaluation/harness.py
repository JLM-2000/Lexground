from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from lexground.db.models import EvalCase, EvalRun
from lexground.evaluation.golden import GoldenCase
from lexground.evaluation.judge import GroundednessJudge
from lexground.evaluation.metrics import (
    aggregate,
    citation_keys,
    citation_scores,
    ndcg_at_k,
    normalise,
    quote_is_verbatim,
    recall_at_k,
    reciprocal_rank,
)
from lexground.pipeline import QueryOutcome, QueryService
from lexground.retrieval.types import RetrievedChunk
from lexground.synthesis.providers import SynthesisError
from lexground.synthesis.schema import GroundedAnswer

SCORED_METRICS = (
    "recall_at_5",
    "ndcg_at_10",
    "mrr",
    "citation_precision",
    "citation_recall",
    "quote_fidelity",
    "refusal_accuracy",
)


class Thresholds(BaseModel):
    """Gate thresholds. Floors, except latency_p95_ms which is a ceiling."""

    recall_at_5: float = 0.85
    ndcg_at_10: float = 0.70
    mrr: float = 0.75
    citation_precision: float = 0.90
    citation_recall: float = 0.90
    quote_fidelity: float = 0.95
    refusal_accuracy: float = 0.90
    groundedness: float = 0.90
    latency_p95_ms: float = 15000.0

    @classmethod
    def load(cls, path: Path | None) -> Thresholds:
        if path is None or not path.exists():
            return cls()
        return cls.model_validate(json.loads(path.read_text(encoding="utf-8")))

    def evaluate(self, metrics: dict[str, float]) -> tuple[bool, list[str]]:
        failures: list[str] = []
        for name, floor in self.model_dump().items():
            if name not in metrics:
                continue
            value = metrics[name]
            if name == "latency_p95_ms":
                if value > floor:
                    failures.append(f"{name}: {value:.1f} > ceiling {floor:.1f}")
            elif value < floor:
                failures.append(f"{name}: {value:.4f} < floor {floor:.4f}")
        return not failures, failures


def score_retrieval(case: GoldenCase, retrieved: list[str]) -> dict[str, float]:
    found = citation_keys(retrieved)
    relevant = citation_keys(case.relevant_citations)
    return {
        "recall_at_5": recall_at_k(found, relevant, 5),
        "ndcg_at_10": ndcg_at_k(found, relevant, 10),
        "mrr": reciprocal_rank(found, relevant),
    }


def score_quote_fidelity(answer: GroundedAnswer, chunks: list[RetrievedChunk]) -> float:
    """Fraction of claimed quotes that really appear in the chunk they cite."""
    if not answer.citations:
        return 0.0
    source_by_citation = {normalise(chunk.citation): chunk.text for chunk in chunks}
    verified = sum(
        quote_is_verbatim(
            citation.supporting_quote,
            source_by_citation.get(normalise(citation.citation), ""),
        )
        for citation in answer.citations
    )
    return verified / len(answer.citations)


def score_case(case: GoldenCase, outcome: QueryOutcome) -> dict[str, float]:
    """Grade one answered question. Pure, so the scoring rules are testable alone."""
    answer = outcome.answer
    if not case.answerable:
        return {"refusal_accuracy": 0.0 if answer.answerable else 1.0}

    claimed = [citation.citation for citation in answer.citations]
    precision, recall = citation_scores(claimed, case.expected_citations)
    return {
        **score_retrieval(case, [chunk.citation for chunk in outcome.retrieval.chunks]),
        "citation_precision": precision,
        "citation_recall": recall,
        "quote_fidelity": score_quote_fidelity(answer, outcome.retrieval.chunks),
        "refusal_accuracy": 1.0 if answer.answerable else 0.0,
    }


@dataclass(slots=True)
class EvalReport:
    metrics: dict[str, float]
    passed: bool
    failures: list[str]
    case_count: int
    per_case: list[dict[str, object]] = field(default_factory=list)
    total_cost_usd: float = 0.0

    def render(self) -> str:
        lines = [f"Evaluated {self.case_count} cases", ""]
        for name, value in sorted(self.metrics.items()):
            lines.append(f"  {name:<24} {value:>10.4f}")
        lines += ["", f"  {'estimated cost (USD)':<24} {self.total_cost_usd:>10.4f}", ""]
        if self.passed:
            lines.append("GATE PASSED")
        else:
            lines.append("GATE FAILED")
            lines.extend(f"  - {failure}" for failure in self.failures)
        return "\n".join(lines)


@dataclass(slots=True)
class _Results:
    scored: list[dict[str, float]] = field(default_factory=list)
    latencies: list[float] = field(default_factory=list)
    records: list[dict[str, object]] = field(default_factory=list)
    rows: list[EvalCase] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    cost_usd: float = 0.0


class EvaluationHarness:
    def __init__(
        self,
        query_service: QueryService,
        thresholds: Thresholds,
        judge: GroundednessJudge | None = None,
    ) -> None:
        self._service = query_service
        self._thresholds = thresholds
        self._judge = judge

    async def run(
        self,
        session: AsyncSession,
        cases: list[GoldenCase],
        *,
        index_version: str,
        git_sha: str | None = None,
        persist: bool = True,
    ) -> EvalReport:
        results = _Results()
        for case in cases:
            await self._run_case(session, case, results)

        metrics = aggregate(results.scored, results.latencies)
        passed, failures = self._thresholds.evaluate(metrics)
        passed = passed and not results.errors

        if persist:
            session.add(
                EvalRun(
                    git_sha=git_sha,
                    index_version=index_version,
                    case_count=len(cases),
                    metrics=metrics,
                    passed=passed,
                    cases=results.rows,
                )
            )
            await session.flush()

        return EvalReport(
            metrics=metrics,
            passed=passed,
            failures=results.errors + failures,
            case_count=len(cases),
            per_case=results.records,
            total_cost_usd=results.cost_usd,
        )

    async def _run_case(self, session: AsyncSession, case: GoldenCase, results: _Results) -> None:
        try:
            outcome = await self._service.ask(
                session, case.question, language=case.language, persist=False
            )
        except SynthesisError as error:
            results.errors.append(f"{case.id}: {error}")
            results.scored.append(dict.fromkeys(SCORED_METRICS, 0.0))
            results.records.append(
                {"case_id": case.id, "question": case.question, "error": str(error)}
            )
            return

        scores = score_case(case, outcome)
        rationale = await self._judge_groundedness(case, outcome, scores)
        claimed = [citation.citation for citation in outcome.answer.citations]

        results.latencies.append(float(outcome.latency_ms))
        results.cost_usd += outcome.cost_usd
        results.scored.append(scores)
        results.records.append(
            {
                "case_id": case.id,
                "question": case.question,
                "answerable_expected": case.answerable,
                "answerable_actual": outcome.answer.answerable,
                "retrieved": [chunk.citation for chunk in outcome.retrieval.chunks],
                "expected_citations": case.expected_citations,
                "actual_citations": claimed,
                "scores": scores,
                "latency_ms": outcome.latency_ms,
            }
        )
        results.rows.append(
            EvalCase(
                case_id=case.id,
                question=case.question,
                language=case.language,
                expected_citations={"items": case.expected_citations},
                actual_citations={"items": claimed},
                scores=dict(scores),
                answer=outcome.answer.answer or None,
                judge_rationale=rationale,
            )
        )

    async def _judge_groundedness(
        self, case: GoldenCase, outcome: QueryOutcome, scores: dict[str, float]
    ) -> str | None:
        if self._judge is None or not case.answerable or not outcome.answer.answerable:
            return None
        verdict = await self._judge.evaluate(
            case.question, outcome.answer, outcome.retrieval.chunks
        )
        scores["groundedness"] = 1.0 if verdict.grounded else 0.0
        return verdict.rationale
