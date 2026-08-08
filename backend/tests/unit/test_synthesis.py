import json
import uuid

import pytest

from lexground.config import Settings
from lexground.evaluation.golden import GoldenCase, load_golden_set
from lexground.evaluation.harness import Thresholds
from lexground.retrieval.types import RetrievalResult, RetrievedChunk
from lexground.synthesis.answerer import ExtractiveAnswerer, harden_schema
from lexground.synthesis.prompts import build_user_prompt, format_context
from lexground.synthesis.schema import GroundedAnswer


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


class TestPromptAssembly:
    def test_context_blocks_are_numbered_from_one(self) -> None:
        rendered = format_context([chunk("Art. 1", "first"), chunk("Art. 2", "second")])
        assert rendered.startswith("[1] Art. 1")
        assert "[2] Art. 2" in rendered

    def test_source_text_is_delimited(self) -> None:
        assert "<source>first</source>" in format_context([chunk("Art. 1", "first")])

    def test_question_is_separated_from_context(self) -> None:
        prompt = build_user_prompt("why?", [chunk("Art. 1", "first")])
        assert "<question>why?</question>" in prompt
        assert prompt.index("<context>") < prompt.index("<question>")


class TestSchemaHardening:
    def test_objects_are_closed(self) -> None:
        hardened = harden_schema(GroundedAnswer.model_json_schema())
        assert hardened["additionalProperties"] is False

    def test_nested_definitions_are_closed(self) -> None:
        hardened = harden_schema(GroundedAnswer.model_json_schema())
        citation = hardened["$defs"]["Citation"]
        assert citation["additionalProperties"] is False

    def test_all_properties_are_required(self) -> None:
        hardened = harden_schema(GroundedAnswer.model_json_schema())
        assert set(hardened["required"]) == set(hardened["properties"])

    def test_result_is_json_serialisable(self) -> None:
        json.dumps(harden_schema(GroundedAnswer.model_json_schema()))


class TestExtractiveAnswerer:
    @pytest.fixture
    def answerer(self) -> ExtractiveAnswerer:
        return ExtractiveAnswerer(Settings(anthropic_api_key=None))

    async def test_quotes_the_top_ranked_provision(self, answerer: ExtractiveAnswerer) -> None:
        retrieval = RetrievalResult(chunks=[chunk("Art. 4(2)", "Thirty days applies.")])
        outcome = await answerer.answer("how long?", retrieval)
        assert outcome.answer.answerable
        assert outcome.answer.citations[0].citation == "Art. 4(2)"

    async def test_answer_carries_a_marker(self, answerer: ExtractiveAnswerer) -> None:
        retrieval = RetrievalResult(chunks=[chunk("Art. 4(2)", "Thirty days applies.")])
        outcome = await answerer.answer("how long?", retrieval)
        assert outcome.answer.answer.endswith("[1]")

    async def test_quote_is_a_verbatim_span_of_the_source(
        self, answerer: ExtractiveAnswerer
    ) -> None:
        source = "Thirty days applies to the review."
        retrieval = RetrievalResult(chunks=[chunk("Art. 4(2)", source)])
        outcome = await answerer.answer("how long?", retrieval)
        assert outcome.answer.citations[0].supporting_quote in source

    async def test_refuses_on_weak_context(self, answerer: ExtractiveAnswerer) -> None:
        retrieval = RetrievalResult(chunks=[chunk("Art. 4", "x")], weak_context=True)
        outcome = await answerer.answer("unrelated?", retrieval)
        assert not outcome.answer.answerable
        assert outcome.answer.refusal_reason

    async def test_refuses_on_empty_retrieval(self, answerer: ExtractiveAnswerer) -> None:
        outcome = await answerer.answer("anything?", RetrievalResult(chunks=[]))
        assert not outcome.answer.answerable

    async def test_costs_nothing(self, answerer: ExtractiveAnswerer) -> None:
        retrieval = RetrievalResult(chunks=[chunk("Art. 1", "text here")])
        outcome = await answerer.answer("q?", retrieval)
        assert outcome.cost_usd == 0.0


class TestSettingsBackendSelection:
    def test_no_key_means_extractive(self) -> None:
        assert Settings(anthropic_api_key=None).synthesis_backend == "extractive"

    def test_key_present_means_claude(self) -> None:
        assert Settings(anthropic_api_key="sk-test").synthesis_backend == "claude"


class TestGoldenSet:
    def test_answerable_case_requires_relevant_citations(self) -> None:
        with pytest.raises(ValueError, match="relevant_citations"):
            GoldenCase(id="x", question="q", answerable=True)

    def test_expected_citations_default_to_relevant(self) -> None:
        case = GoldenCase(id="x", question="q", relevant_citations=["Art. 1"])
        assert case.expected_citations == ["Art. 1"]

    def test_unanswerable_case_needs_no_citations(self) -> None:
        case = GoldenCase(id="x", question="q", answerable=False)
        assert case.expected_citations == []

    def test_duplicate_ids_are_rejected(self, tmp_path) -> None:
        path = tmp_path / "cases.jsonl"
        row = '{"id": "dup", "question": "q", "answerable": false}'
        path.write_text(f"{row}\n{row}\n", encoding="utf-8")
        with pytest.raises(ValueError, match="duplicate case id"):
            load_golden_set(path)

    def test_comments_and_blank_lines_are_ignored(self, tmp_path) -> None:
        path = tmp_path / "cases.jsonl"
        path.write_text(
            '// a comment\n\n{"id": "a", "question": "q", "answerable": false}\n',
            encoding="utf-8",
        )
        assert len(load_golden_set(path)) == 1

    def test_empty_file_is_rejected(self, tmp_path) -> None:
        path = tmp_path / "cases.jsonl"
        path.write_text("// only a comment\n", encoding="utf-8")
        with pytest.raises(ValueError, match="empty"):
            load_golden_set(path)

    def test_error_reports_the_offending_line(self, tmp_path) -> None:
        path = tmp_path / "cases.jsonl"
        path.write_text(
            '{"id": "a", "question": "q", "answerable": false}\nnot json\n',
            encoding="utf-8",
        )
        with pytest.raises(ValueError, match=":2:"):
            load_golden_set(path)


class TestThresholds:
    def test_floor_breach_fails_the_gate(self) -> None:
        passed, failures = Thresholds(recall_at_5=0.9).evaluate({"recall_at_5": 0.5})
        assert not passed
        assert "recall_at_5" in failures[0]

    def test_meeting_the_floor_exactly_passes(self) -> None:
        passed, _ = Thresholds(recall_at_5=0.9).evaluate({"recall_at_5": 0.9})
        assert passed

    def test_latency_is_a_ceiling_not_a_floor(self) -> None:
        passed, _ = Thresholds(latency_p95_ms=100.0).evaluate({"latency_p95_ms": 50.0})
        assert passed
        breached, failures = Thresholds(latency_p95_ms=100.0).evaluate({"latency_p95_ms": 500.0})
        assert not breached
        assert "ceiling" in failures[0]

    def test_metrics_absent_from_the_run_are_skipped(self) -> None:
        passed, _ = Thresholds().evaluate({"recall_at_5": 1.0})
        assert passed

    def test_loading_a_missing_file_yields_defaults(self, tmp_path) -> None:
        assert Thresholds.load(tmp_path / "absent.json") == Thresholds()

    def test_loads_overrides_from_disk(self, tmp_path) -> None:
        path = tmp_path / "t.json"
        path.write_text('{"recall_at_5": 0.42}', encoding="utf-8")
        assert Thresholds.load(path).recall_at_5 == 0.42
