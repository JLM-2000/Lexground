from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from lexground.db.models import Chunk as ChunkRow
from lexground.db.models import Document
from lexground.ingest.chunk import Chunk, chunk_prose, chunk_units
from lexground.ingest.parse import parse_document
from lexground.ingest.sources import DocumentSource, SourceName
from lexground.retrieval.embedder import Embedder


class CorpusEntry(BaseModel):
    source: SourceName = "eurlex"
    source_id: str
    short_title: str
    title: str
    languages: list[str] = ["en"]
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
    skipped: list[str] = field(default_factory=list)


def chunk_document(text: str, *, short_title: str, language: str) -> list[Chunk]:
    """Use the legal chunker when the document has provisions, prose chunking otherwise.

    Detection is the parser itself: if it finds no articles or recitals there is nothing
    to cite by provision, so the locator falls back to headings and pages.
    """
    units = parse_document(text, language=language)
    if units:
        return chunk_units(units, short_title=short_title, language=language)
    return chunk_prose(text, short_title=short_title)


class Ingestor:
    def __init__(self, embedder: Embedder, sources: dict[SourceName, DocumentSource]) -> None:
        self._embedder = embedder
        self._sources = sources

    async def ingest_manifest(
        self, session: AsyncSession, manifest: CorpusManifest
    ) -> IngestSummary:
        summary = IngestSummary()
        for entry in manifest.documents:
            for language in entry.languages:
                try:
                    chunk_count = await self.ingest_one(session, entry, language)
                except Exception as error:
                    summary.skipped.append(f"{entry.source_id}/{language}: {error}")
                    continue
                summary.documents += 1
                summary.chunks += chunk_count
        return summary

    async def ingest_one(self, session: AsyncSession, entry: CorpusEntry, language: str) -> int:
        source = self._sources.get(entry.source)
        if source is None:
            raise ValueError(f"no connector configured for source {entry.source!r}")

        fetched = await source.fetch(entry.source_id, language)
        chunks = chunk_document(fetched.text, short_title=entry.short_title, language=language)
        if not chunks:
            raise ValueError("document produced no chunks")

        document = await self._upsert_document(session, entry, language, fetched.source_url)
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

    async def _upsert_document(
        self, session: AsyncSession, entry: CorpusEntry, language: str, source_url: str
    ) -> Document:
        existing = await session.scalar(
            select(Document).where(
                Document.source == entry.source,
                Document.source_id == entry.source_id,
                Document.language == language,
                Document.version == entry.version,
            )
        )
        if existing is not None:
            await session.execute(delete(ChunkRow).where(ChunkRow.document_id == existing.id))
            existing.title = entry.title
            existing.short_title = entry.short_title
            existing.source_url = source_url
            return existing

        document = Document(
            source=entry.source,
            source_id=entry.source_id,
            title=entry.title,
            short_title=entry.short_title,
            language=language,
            version=entry.version,
            jurisdiction=entry.jurisdiction,
            source_url=source_url,
            adopted_on=(
                datetime.fromisoformat(entry.adopted_on).replace(tzinfo=UTC)
                if entry.adopted_on
                else None
            ),
        )
        session.add(document)
        await session.flush()
        return document


def index_version(manifest: CorpusManifest, embedder_name: str) -> str:
    """Stable id for the (corpus, embedding model) pair."""
    payload = json.dumps(manifest.model_dump(), sort_keys=True) + embedder_name
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]
