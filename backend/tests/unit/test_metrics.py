import pytest

from lexground.evaluation.metrics import (
    aggregate,
    citation_key,
    citation_scores,
    dedupe,
    ndcg_at_k,
    normalise,
    percentile,
    precision_at_k,
    quote_is_verbatim,
    recall_at_k,
    reciprocal_rank,
)


class TestCitationKey:
    @pytest.mark.parametrize(
        ("citation", "expected"),
        [
            ("GDPR Art. 22(1)", "gdpr art. 22"),
            ("GDPR Art. 22", "gdpr art. 22"),
            ("GDPR Art. 22(10)", "gdpr art. 22"),
            ("ADSR Art. 4(2) [1/2]", "adsr art. 4"),
            ("GDPR Recital 71", "gdpr recital 71"),
        ],
    )
    def test_reduces_to_provision(self, citation: str, expected: str) -> None:
        assert citation_key(citation) == expected

    def test_paragraphs_of_same_article_collide(self) -> None:
        assert citation_key("ADSR Art. 4(1)") == citation_key("ADSR Art. 4(3)")

    def test_distinct_articles_do_not_collide(self) -> None:
        assert citation_key("ADSR Art. 4(1)") != citation_key("ADSR Art. 6(1)")


class TestDedupe:
    def test_keeps_best_ranked_occurrence(self) -> None:
        assert dedupe(["a", "b", "a", "c", "b"]) == ["a", "b", "c"]

    def test_empty(self) -> None:
        assert dedupe([]) == []


class TestRankedMetrics:
    def test_recall_counts_distinct_provisions(self) -> None:
        assert recall_at_k(["a", "b"], ["a", "b"], 5) == 1.0
        assert recall_at_k(["a"], ["a", "b"], 5) == 0.5

    def test_recall_window_is_applied_after_dedupe(self) -> None:
        assert recall_at_k(["a", "a", "a", "b"], ["a", "b"], 2) == 1.0

    def test_recall_with_no_relevant_items_is_vacuously_one(self) -> None:
        assert recall_at_k(["a"], [], 5) == 1.0

    def test_precision_at_k(self) -> None:
        assert precision_at_k(["a", "x", "y", "z"], ["a"], 4) == 0.25

    def test_reciprocal_rank_uses_first_hit(self) -> None:
        assert reciprocal_rank(["x", "a"], ["a"]) == 0.5
        assert reciprocal_rank(["a", "x"], ["a"]) == 1.0

    def test_reciprocal_rank_is_zero_when_absent(self) -> None:
        assert reciprocal_rank(["x", "y"], ["a"]) == 0.0

    def test_ndcg_perfect_ranking(self) -> None:
        assert ndcg_at_k(["a", "b"], ["a", "b"], 10) == pytest.approx(1.0)

    def test_ndcg_never_exceeds_one_with_repeated_provision(self) -> None:
        assert ndcg_at_k(["a", "a", "a"], ["a"], 10) <= 1.0

    def test_ndcg_penalises_late_hits(self) -> None:
        early = ndcg_at_k(["a", "x", "y"], ["a"], 10)
        late = ndcg_at_k(["x", "y", "a"], ["a"], 10)
        assert early > late


class TestCitationScores:
    def test_exact_match(self) -> None:
        assert citation_scores(["GDPR Art. 22"], ["GDPR Art. 22"]) == (1.0, 1.0)

    def test_paragraph_variation_still_matches(self) -> None:
        precision, recall = citation_scores(["GDPR Art. 22(2)"], ["GDPR Art. 22(1)"])
        assert (precision, recall) == (1.0, 1.0)

    def test_wrong_article_scores_zero(self) -> None:
        assert citation_scores(["GDPR Art. 21"], ["GDPR Art. 22"]) == (0.0, 0.0)

    def test_extra_citation_costs_precision_not_recall(self) -> None:
        precision, recall = citation_scores(["GDPR Art. 22", "GDPR Art. 5"], ["GDPR Art. 22"])
        assert precision == 0.5
        assert recall == 1.0

    def test_refusal_on_unanswerable_is_correct(self) -> None:
        assert citation_scores([], []) == (1.0, 1.0)

    def test_citing_anything_on_unanswerable_is_wrong(self) -> None:
        precision, _ = citation_scores(["GDPR Art. 22"], [])
        assert precision == 0.0


class TestQuoteIsVerbatim:
    SOURCE = "The controller shall notify the supervisory authority within 72 hours."

    def test_verbatim_span_passes(self) -> None:
        assert quote_is_verbatim("notify the supervisory authority", self.SOURCE)

    def test_survives_whitespace_and_case_differences(self) -> None:
        assert quote_is_verbatim("NOTIFY   the\nSupervisory Authority", self.SOURCE)

    def test_survives_smart_punctuation(self) -> None:
        assert quote_is_verbatim("within 72 hours", self.SOURCE.replace("72", "72"))

    def test_fabricated_quote_fails(self) -> None:
        assert not quote_is_verbatim("notify the data subject within 24 hours", self.SOURCE)

    def test_trivially_short_quote_fails(self) -> None:
        assert not quote_is_verbatim("the", self.SOURCE)


class TestNormalise:
    def test_folds_smart_quotes_and_dashes(self) -> None:
        assert normalise("the controller’s duty") == "the controller's duty"
        assert normalise("2016–2018") == "2016-2018"

    def test_collapses_whitespace(self) -> None:
        assert normalise("  a   b \n c ") == "a b c"


class TestAggregation:
    def test_percentile_picks_upper_value(self) -> None:
        assert percentile([1.0, 2.0, 3.0, 4.0], 0.5) == 2.0
        assert percentile([1.0, 2.0, 3.0, 4.0], 1.0) == 4.0

    def test_percentile_of_empty_is_zero(self) -> None:
        assert percentile([], 0.95) == 0.0

    def test_aggregate_means_each_metric_independently(self) -> None:
        summary = aggregate([{"recall_at_5": 1.0}, {"recall_at_5": 0.0, "mrr": 1.0}], [10.0, 20.0])
        assert summary["recall_at_5"] == 0.5
        assert summary["mrr"] == 1.0
        assert summary["latency_p95_ms"] == 20.0
