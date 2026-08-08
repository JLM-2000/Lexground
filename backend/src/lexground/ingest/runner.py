from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from lexground.db.models import Chunk as ChunkRow
from lexground.db.models import Document
from lexground.ingest.chunk import chunk_units
from lexground.ingest.fetch import EurLexClient
from lexground.ingest.parse import parse_document
from lexground.retrieval.embedder import Embedder


class CorpusEntry(BaseModel):
    celex_id: str
    short_title: str
    title: str
    languages: list[str]
    version: str = "original"
    jurisdiction: str = "EU"
    adopted_on: str | None = None


class CorpusManifest(BaseModel):
    documents: list[CorpusEntry]

    @classmethod
    def load(cls, path: Path) -> CorpusManifest:
        return cls.model_validate(json.loads(path.read_text(encoding="utf-8")))


@dataclass(slots=True)
class IngestSummary:
    documents: int = 0
    chunks: int = 0
    skipped: list[str] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.skipped is None:
            self.skipped = []


class Ingestor:
    def __init__(self, embedder: Embedder, client: EurLexClient) -> None:
        self._embedder = embedder
        self._client = client

    async def ingest_manifest(
        self, session: AsyncSession, manifest: CorpusManifest
    ) -> IngestSummary:
        summary = IngestSummary()
        for entry in manifest.documents:
            for language in entry.languages:
                try:
                    chunk_count = await self.ingest_one(session, entry, language)
                except Exception as error:
                    summary.skipped.append(f"{entry.celex_id}/{language}: {error}")
                    continue
                summary.documents += 1
                summary.chunks += chunk_count
        return summary

    async def ingest_one(self, session: AsyncSession, entry: CorpusEntry, language: str) -> int:
        fetched = await self._client.fetch(entry.celex_id, language)
        units = parse_document(fetched.text, language=language)
        if not units:
            raise ValueError("parser produced no citable units")

        chunks = chunk_units(units, short_title=entry.short_title, language=language)

        existing = await session.scalar(
            select(Document).where(
                Document.celex_id == entry.celex_id,
                Document.language == language,
                Document.version == entry.version,
            )
        )
        if existing is not None:
            await session.execute(delete(ChunkRow).where(ChunkRow.document_id == existing.id))
            document = existing
            document.title = entry.title
            document.source_url = fetched.source_url
        else:
            document = Document(
                celex_id=entry.celex_id,
                title=entry.title,
                short_title=entry.short_title,
                language=language,
                version=entry.version,
                jurisdiction=entry.jurisdiction,
                source_url=fetched.source_url,
                adopted_on=(
                    datetime.fromisoformat(entry.adopted_on).replace(tzinfo=UTC)
                    if entry.adopted_on
                    else None
                ),
            )
            session.add(document)
            await session.flush()

        vectors = self._embedder.embed_documents([chunk.text for chunk in chunks])
        session.add_all(
            ChunkRow(
                document_id=document.id,
                ordinal=chunk.ordinal,
                unit_type=chunk.unit_type,
                unit_number=chunk.unit_number,
                paragraph=chunk.paragraph,
                heading=chunk.heading,
                citation=chunk.citation,
                text=chunk.text,
                token_estimate=chunk.token_estimate,
                language=language,
                embedding=vector,
            )
            for chunk, vector in zip(chunks, vectors, strict=True)
        )
        await session.flush()
        return len(chunks)


def index_version(manifest: CorpusManifest, embedder_name: str) -> str:
    """Stable id for the (corpus, embedding model) pair.

    Eval results are only comparable within one index version, so it is recorded on
    every run rather than inferred later.
    """
    payload = json.dumps(manifest.model_dump(), sort_keys=True) + embedder_name
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]
