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
    citation_scores,
    keys,
    ndcg_at_k,
    normalise,
    quote_fidelity,
    recall_at_k,
    reciprocal_rank,
)
from lexground.pipeline import QueryService
from lexground.synthesis.providers import SynthesisError

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
        lines.append("")
        lines.append(f"  {'estimated cost (USD)':<24} {self.total_cost_usd:>10.4f}")
        lines.append("")
        if self.passed:
            lines.append("GATE PASSED")
        else:
            lines.append("GATE FAILED")
            lines.extend(f"  - {failure}" for failure in self.failures)
        return "\n".join(lines)


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
        scored: list[dict[str, float]] = []
        latencies: list[float] = []
        records: list[dict[str, object]] = []
        rows: list[EvalCase] = []
        failures_by_case: list[str] = []
        total_cost = 0.0

        for case in cases:
            try:
                outcome = await self._service.ask(
                    session, case.question, language=case.language, persist=False
                )
            except SynthesisError as error:
                failures_by_case.append(f"{case.id}: {error}")
                scored.append(dict.fromkeys(SCORED_METRICS, 0.0))
                records.append({"case_id": case.id, "question": case.question, "error": str(error)})
                continue
            latencies.append(float(outcome.latency_ms))
            total_cost += outcome.cost_usd

            retrieved = [chunk.citation for chunk in outcome.retrieval.chunks]
            answer = outcome.answer
            actual_citations = [citation.citation for citation in answer.citations]
            scores: dict[str, float] = {}
            rationale: str | None = None

            if case.answerable:
                found = keys(retrieved)
                relevant = keys(case.relevant_citations)
                scores["recall_at_5"] = recall_at_k(found, relevant, 5)
                scores["ndcg_at_10"] = ndcg_at_k(found, relevant, 10)
                scores["mrr"] = reciprocal_rank(found, relevant)

                precision, recall = citation_scores(actual_citations, case.expected_citations)
                scores["citation_precision"] = precision
                scores["citation_recall"] = recall
                scores["quote_fidelity"] = self._quote_fidelity(outcome)
                scores["refusal_accuracy"] = 0.0 if not answer.answerable else 1.0

                if self._judge is not None and answer.answerable:
                    verdict = await self._judge.evaluate(
                        case.question, answer, outcome.retrieval.chunks
                    )
                    scores["groundedness"] = 1.0 if verdict.grounded else 0.0
                    rationale = verdict.rationale
            else:
                scores["refusal_accuracy"] = 0.0 if answer.answerable else 1.0

            scored.append(scores)
            records.append(
                {
                    "case_id": case.id,
                    "question": case.question,
                    "answerable_expected": case.answerable,
                    "answerable_actual": answer.answerable,
                    "retrieved": retrieved,
                    "expected_citations": case.expected_citations,
                    "actual_citations": actual_citations,
                    "scores": scores,
                    "latency_ms": outcome.latency_ms,
                }
            )
            rows.append(
                EvalCase(
                    case_id=case.id,
                    question=case.question,
                    language=case.language,
                    expected_citations={"items": case.expected_citations},
                    actual_citations={"items": actual_citations},
                    scores=dict(scores),
                    answer=answer.answer or None,
                    judge_rationale=rationale,
                )
            )

        metrics = aggregate(scored, latencies)
        passed, failures = self._thresholds.evaluate(metrics)
        failures = failures_by_case + failures
        passed = passed and not failures_by_case

        if persist:
            run = EvalRun(
                git_sha=git_sha,
                index_version=index_version,
                case_count=len(cases),
                metrics=metrics,
                passed=passed,
                cases=rows,
            )
            session.add(run)
            await session.flush()

        return EvalReport(
            metrics=metrics,
            passed=passed,
            failures=failures,
            case_count=len(cases),
            per_case=records,
            total_cost_usd=total_cost,
        )

    @staticmethod
    def _quote_fidelity(outcome: object) -> float:
        """Fraction of claimed quotes that really appear in the chunk they cite."""
        answer = outcome.answer  # type: ignore[attr-defined]
        chunks = outcome.retrieval.chunks  # type: ignore[attr-defined]
        if not answer.citations:
            return 0.0
        by_citation = {normalise(chunk.citation): chunk.text for chunk in chunks}
        verified = sum(
            1
            for citation in answer.citations
            if quote_fidelity(
                citation.supporting_quote, by_citation.get(normalise(citation.citation), "")
            )
        )
        return verified / len(answer.citations)
