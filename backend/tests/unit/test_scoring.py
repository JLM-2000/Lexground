import uuid

import pytest

from lexground.evaluation.golden import GoldenCase
from lexground.evaluation.harness import (
    score_case,
    score_quote_fidelity,
    score_retrieval,
)
from lexground.pipeline import QueryOutcome
from lexground.retrieval.types import RetrievalResult, RetrievedChunk
from lexground.synthesis.schema import Citation, GroundedAnswer

ARTICLE_4 = "The deployer shall complete the review within 30 days of the request."
ARTICLE_6 = "Records shall be retained for five years from the date of the decision."


def chunk(citation: str, text: str) -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=uuid.uuid5(uuid.NAMESPACE_DNS, citation),
        citation=citation,
        text=text,
        document_title="Act",
        celex_id="X",
        language="en",
        source_url="http://example.invalid",
        unit_type="article",
    )


def outcome(answer: GroundedAnswer, chunks: list[RetrievedChunk]) -> QueryOutcome:
    return QueryOutcome(
        trace_id=uuid.uuid4(),
        question="q",
        answer=answer,
        retrieval=RetrievalResult(chunks=chunks),
        latency_ms=10,
        input_tokens=0,
        output_tokens=0,
        cost_usd=0.0,
        model="test",
    )


def answered(*citations: Citation) -> GroundedAnswer:
    return GroundedAnswer(answerable=True, answer="text [1]", citations=list(citations))


def refused() -> GroundedAnswer:
    return GroundedAnswer(
        answerable=False, answer="", citations=[], refusal_reason="not in the corpus"
    )


class TestScoreRetrieval:
    def test_perfect_ranking_scores_one_across_the_board(self) -> None:
        case = GoldenCase(id="c", question="q", relevant_citations=["ADSR Art. 4"])
        scores = score_retrieval(case, ["ADSR Art. 4(1)", "ADSR Art. 9(2)"])
        assert scores == {"recall_at_5": 1.0, "ndcg_at_10": 1.0, "mrr": 1.0}

    def test_paragraph_chunks_resolve_to_their_provision(self) -> None:
        case = GoldenCase(id="c", question="q", relevant_citations=["ADSR Art. 4"])
        assert score_retrieval(case, ["ADSR Art. 4(3)"])["recall_at_5"] == 1.0

    def test_missing_the_provision_scores_zero(self) -> None:
        case = GoldenCase(id="c", question="q", relevant_citations=["ADSR Art. 4"])
        scores = score_retrieval(case, ["DRRR Art. 3(1)", "ADSR Art. 9(1)"])
        assert scores == {"recall_at_5": 0.0, "ndcg_at_10": 0.0, "mrr": 0.0}

    def test_ranking_the_provision_later_costs_mrr_but_not_recall(self) -> None:
        case = GoldenCase(id="c", question="q", relevant_citations=["ADSR Art. 4"])
        scores = score_retrieval(case, ["DRRR Art. 3(1)", "ADSR Art. 4(1)"])
        assert scores["recall_at_5"] == 1.0
        assert scores["mrr"] == 0.5

    def test_repeated_chunks_never_push_ndcg_above_one(self) -> None:
        case = GoldenCase(id="c", question="q", relevant_citations=["ADSR Art. 4"])
        ranking = ["ADSR Art. 4(1)", "ADSR Art. 4(2)", "ADSR Art. 4(3)"]
        assert score_retrieval(case, ranking)["ndcg_at_10"] <= 1.0


class TestScoreQuoteFidelity:
    def test_verbatim_quote_passes(self) -> None:
        answer = answered(
            Citation(marker=1, citation="ADSR Art. 4(2)", supporting_quote="within 30 days")
        )
        assert score_quote_fidelity(answer, [chunk("ADSR Art. 4(2)", ARTICLE_4)]) == 1.0

    def test_quote_from_a_different_article_fails(self) -> None:
        answer = answered(
            Citation(
                marker=1, citation="ADSR Art. 4(2)", supporting_quote="retained for five years"
            )
        )
        assert score_quote_fidelity(answer, [chunk("ADSR Art. 4(2)", ARTICLE_4)]) == 0.0

    def test_citing_a_chunk_that_was_not_retrieved_fails(self) -> None:
        answer = answered(
            Citation(marker=1, citation="ADSR Art. 99", supporting_quote="within 30 days")
        )
        assert score_quote_fidelity(answer, [chunk("ADSR Art. 4(2)", ARTICLE_4)]) == 0.0

    def test_one_good_quote_of_two_scores_half(self) -> None:
        answer = answered(
            Citation(marker=1, citation="ADSR Art. 4(2)", supporting_quote="within 30 days"),
            Citation(marker=2, citation="ADSR Art. 6(2)", supporting_quote="invented text here"),
        )
        chunks = [chunk("ADSR Art. 4(2)", ARTICLE_4), chunk("ADSR Art. 6(2)", ARTICLE_6)]
        assert score_quote_fidelity(answer, chunks) == 0.5

    def test_an_answer_with_no_citations_scores_zero(self) -> None:
        answer = answered()
        assert score_quote_fidelity(answer, [chunk("ADSR Art. 4(2)", ARTICLE_4)]) == 0.0


class TestScoreCase:
    def test_a_correct_answer_scores_one_everywhere(self) -> None:
        case = GoldenCase(id="c", question="q", relevant_citations=["ADSR Art. 4"])
        answer = answered(
            Citation(marker=1, citation="ADSR Art. 4(2)", supporting_quote="within 30 days")
        )
        scores = score_case(case, outcome(answer, [chunk("ADSR Art. 4(2)", ARTICLE_4)]))
        assert all(value == 1.0 for value in scores.values()), scores

    def test_refusing_an_answerable_question_is_penalised(self) -> None:
        case = GoldenCase(id="c", question="q", relevant_citations=["ADSR Art. 4"])
        scores = score_case(case, outcome(refused(), [chunk("ADSR Art. 4(2)", ARTICLE_4)]))
        assert scores["refusal_accuracy"] == 0.0
        assert scores["recall_at_5"] == 1.0

    def test_an_extra_citation_costs_precision_but_not_recall(self) -> None:
        case = GoldenCase(id="c", question="q", relevant_citations=["ADSR Art. 4"])
        answer = answered(
            Citation(marker=1, citation="ADSR Art. 4(2)", supporting_quote="within 30 days"),
            Citation(
                marker=2, citation="ADSR Art. 6(2)", supporting_quote="retained for five years"
            ),
        )
        chunks = [chunk("ADSR Art. 4(2)", ARTICLE_4), chunk("ADSR Art. 6(2)", ARTICLE_6)]
        scores = score_case(case, outcome(answer, chunks))
        assert scores["citation_precision"] == 0.5
        assert scores["citation_recall"] == 1.0

    def test_unanswerable_case_is_graded_only_on_refusal(self) -> None:
        case = GoldenCase(id="c", question="q", answerable=False)
        scores = score_case(case, outcome(refused(), []))
        assert scores == {"refusal_accuracy": 1.0}

    def test_answering_an_unanswerable_question_scores_zero(self) -> None:
        case = GoldenCase(id="c", question="q", answerable=False)
        answer = answered(
            Citation(marker=1, citation="ADSR Art. 4(2)", supporting_quote="within 30 days")
        )
        scores = score_case(case, outcome(answer, [chunk("ADSR Art. 4(2)", ARTICLE_4)]))
        assert scores == {"refusal_accuracy": 0.0}

    @pytest.mark.parametrize("metric", ["recall_at_5", "citation_precision", "quote_fidelity"])
    def test_every_score_stays_within_zero_and_one(self, metric: str) -> None:
        case = GoldenCase(id="c", question="q", relevant_citations=["ADSR Art. 4"])
        answer = answered(
            Citation(marker=1, citation="ADSR Art. 4(1)", supporting_quote="within 30 days"),
            Citation(marker=2, citation="ADSR Art. 4(2)", supporting_quote="within 30 days"),
        )
        chunks = [chunk("ADSR Art. 4(1)", ARTICLE_4), chunk("ADSR Art. 4(2)", ARTICLE_4)]
        assert 0.0 <= score_case(case, outcome(answer, chunks))[metric] <= 1.0
