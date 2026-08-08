from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from lexground.config import Settings
from lexground.retrieval.embedder import HashEmbedder
from lexground.retrieval.service import HybridRetriever

pytestmark = pytest.mark.asyncio


class TestHealth:
    async def test_liveness_is_unconditional(self, client: AsyncClient) -> None:
        response = await client.get("/health")
        assert response.status_code == 200
        assert response.json()["status"] == "ok"

    async def test_readiness_reports_the_indexed_corpus(self, client: AsyncClient) -> None:
        payload = (await client.get("/health/ready")).json()
        assert payload["status"] == "ok"
        assert payload["indexed_chunks"] > 0

    async def test_readiness_names_the_active_backends(self, client: AsyncClient) -> None:
        payload = (await client.get("/health/ready")).json()
        assert payload["synthesis_backend"] == "extractive"

    async def test_metrics_are_exposed_in_prometheus_format(self, client: AsyncClient) -> None:
        response = await client.get("/metrics")
        assert response.status_code == 200
        assert "lexground_indexed_chunks" in response.text


class TestRetrieval:
    @pytest.fixture
    def retriever(self, settings: Settings) -> HybridRetriever:
        return HybridRetriever(settings, HashEmbedder(384))

    async def test_finds_the_governing_provision(
        self, retriever: HybridRetriever, db: AsyncSession
    ) -> None:
        result = await retriever.retrieve(db, "How long is the review period?", language="en")
        citations = [chunk.citation for chunk in result.chunks]
        assert "TEST Art. 4(2)" in citations

    async def test_both_arms_contribute_candidates(
        self, retriever: HybridRetriever, db: AsyncSession
    ) -> None:
        result = await retriever.retrieve(db, "record keeping retention", language="en")
        assert result.lexical_candidates > 0
        assert result.dense_candidates > 0

    async def test_lexical_arm_survives_morphology(
        self, retriever: HybridRetriever, db: AsyncSession
    ) -> None:
        # The stored vector is stemmed, so the singular query matches plural text.
        result = await retriever.retrieve(db, "retained record", language="en")
        assert any(chunk.lexical_rank is not None for chunk in result.chunks)

    async def test_language_filter_excludes_other_languages(
        self, retriever: HybridRetriever, db: AsyncSession
    ) -> None:
        result = await retriever.retrieve(db, "review period", language="es")
        assert result.chunks == []

    async def test_top_k_is_respected(self, retriever: HybridRetriever, db: AsyncSession) -> None:
        result = await retriever.retrieve(db, "decision", language="en", top_k=2)
        assert len(result.chunks) <= 2

    async def test_every_chunk_carries_full_provenance(
        self, retriever: HybridRetriever, db: AsyncSession
    ) -> None:
        result = await retriever.retrieve(db, "review", language="en")
        provenance = result.chunks[0].provenance()
        assert {"citation", "lexical_rank", "dense_rank", "fused_score"} <= set(provenance)


class TestQueryEndpoint:
    async def test_answers_a_question_from_the_corpus(self, client: AsyncClient) -> None:
        response = await client.post(
            "/api/query", json={"question": "How long is the review period?", "language": "en"}
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["answerable"] is True
        assert payload["citations"]

    async def test_returns_the_retrieved_context(self, client: AsyncClient) -> None:
        payload = (
            await client.post("/api/query", json={"question": "record keeping duties"})
        ).json()
        assert payload["retrieved"]
        assert payload["retrieved"][0]["citation"].startswith("TEST")

    async def test_reports_latency_and_cost(self, client: AsyncClient) -> None:
        payload = (await client.post("/api/query", json={"question": "review period"})).json()
        assert payload["latency_ms"] >= 0
        assert payload["cost_usd"] == 0.0

    async def test_rejects_a_question_that_is_too_short(self, client: AsyncClient) -> None:
        assert (await client.post("/api/query", json={"question": "a"})).status_code == 422

    async def test_rejects_a_malformed_language_code(self, client: AsyncClient) -> None:
        response = await client.post(
            "/api/query", json={"question": "a valid question", "language": "english"}
        )
        assert response.status_code == 422


class TestTraces:
    async def test_a_query_is_persisted_and_retrievable(self, client: AsyncClient) -> None:
        trace_id = (
            await client.post("/api/query", json={"question": "review period length"})
        ).json()["trace_id"]

        trace = (await client.get(f"/api/traces/{trace_id}")).json()
        assert trace["id"] == trace_id
        assert trace["retrieved"]

    async def test_trace_records_the_ranking_that_produced_the_answer(
        self, client: AsyncClient
    ) -> None:
        trace_id = (await client.post("/api/query", json={"question": "record retention"})).json()[
            "trace_id"
        ]
        trace = (await client.get(f"/api/traces/{trace_id}")).json()
        assert "fused_score" in trace["retrieved"][0]

    async def test_unknown_trace_is_a_404(self, client: AsyncClient) -> None:
        missing = "00000000-0000-0000-0000-000000000000"
        assert (await client.get(f"/api/traces/{missing}")).status_code == 404

    async def test_recent_traces_are_listed_newest_first(self, client: AsyncClient) -> None:
        await client.post("/api/query", json={"question": "an earlier question"})
        await client.post("/api/query", json={"question": "a later question"})
        traces = (await client.get("/api/traces?limit=2")).json()
        assert traces[0]["question"] == "a later question"


class TestCorpusEndpoints:
    async def test_lists_indexed_documents_with_chunk_counts(self, client: AsyncClient) -> None:
        documents = (await client.get("/api/documents")).json()
        assert documents[0]["celex_id"] == "TEST0001"
        assert documents[0]["chunk_count"] > 0

    async def test_eval_runs_endpoint_is_empty_before_any_run(self, client: AsyncClient) -> None:
        assert (await client.get("/api/evaluation/runs")).json() == []
