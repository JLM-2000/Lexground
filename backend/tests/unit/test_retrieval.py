import uuid

import pytest

from lexground.retrieval.embedder import HashEmbedder
from lexground.retrieval.fusion import reciprocal_rank_fusion
from lexground.retrieval.service import build_tsquery
from lexground.retrieval.types import RetrievedChunk


def chunk(citation: str, *, lexical: float = 0.0, dense: float = 0.0) -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=uuid.uuid5(uuid.NAMESPACE_DNS, citation),
        citation=citation,
        text=f"body of {citation}",
        document_title="Act",
        source_id="X",
        language="en",
        source_url="http://example.invalid",
        unit_type="article",
        lexical_score=lexical,
        dense_score=dense,
    )


class TestTsQuery:
    def test_terms_are_ored_not_anded(self) -> None:
        assert " | " in build_tsquery("how long must records be kept")

    def test_short_stopwords_are_dropped(self) -> None:
        assert "to" not in build_tsquery("how to do it").split(" | ")

    def test_digits_survive_even_when_short(self) -> None:
        assert "72" in build_tsquery("within 72 hours")

    def test_punctuation_is_stripped(self) -> None:
        assert "review" in build_tsquery("human review?")
        assert "?" not in build_tsquery("human review?")

    def test_accented_terms_are_preserved(self) -> None:
        assert "revisión" in build_tsquery("¿revisión humana?")

    def test_terms_are_deduplicated_and_ordered(self) -> None:
        assert build_tsquery("record record keeping") == "keeping | record"

    def test_query_with_no_usable_terms_is_empty(self) -> None:
        assert build_tsquery("a of") == ""


class TestReciprocalRankFusion:
    def test_document_in_both_lists_outranks_either_alone(self) -> None:
        both = chunk("both")
        fused = reciprocal_rank_fusion([both, chunk("lex")], [both, chunk("dense")], k=60)
        assert fused[0].citation == "both"

    def test_ranks_are_recorded_for_the_inspector(self) -> None:
        both = chunk("both")
        fused = reciprocal_rank_fusion([chunk("lex"), both], [both], k=60)
        merged = next(item for item in fused if item.citation == "both")
        assert merged.lexical_rank == 2
        assert merged.dense_rank == 1

    def test_dense_only_document_keeps_null_lexical_rank(self) -> None:
        fused = reciprocal_rank_fusion([], [chunk("dense")], k=60)
        assert fused[0].lexical_rank is None
        assert fused[0].dense_rank == 1

    def test_dense_score_is_carried_onto_the_merged_record(self) -> None:
        shared = chunk("shared", lexical=0.4)
        fused = reciprocal_rank_fusion([shared], [chunk("shared", dense=0.9)], k=60)
        assert fused[0].dense_score == 0.9
        assert fused[0].lexical_score == 0.4

    def test_score_depends_only_on_rank_position(self) -> None:
        top_relevant = reciprocal_rank_fusion([chunk("a", lexical=5.0)], [], k=60)
        top_irrelevant = reciprocal_rank_fusion([chunk("b", lexical=0.01)], [], k=60)
        assert top_relevant[0].fused_score == top_irrelevant[0].fused_score

    def test_empty_inputs_produce_empty_output(self) -> None:
        assert reciprocal_rank_fusion([], [], k=60) == []


class TestHashEmbedder:
    def test_dimensions_match_configuration(self) -> None:
        assert len(HashEmbedder(384).embed_query("text")) == 384

    def test_is_deterministic_across_instances(self) -> None:
        assert HashEmbedder(64).embed_query("hola") == HashEmbedder(64).embed_query("hola")

    def test_vectors_are_unit_length(self) -> None:
        vector = HashEmbedder(128).embed_query("some words here")
        assert sum(component**2 for component in vector) ** 0.5 == pytest.approx(1.0)

    def test_different_text_gives_different_vectors(self) -> None:
        embedder = HashEmbedder(128)
        assert embedder.embed_query("alpha") != embedder.embed_query("beta")

    def test_empty_text_does_not_raise(self) -> None:
        assert len(HashEmbedder(32).embed_query("")) == 32
