from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import httpx
from lxml import etree

from lexground.ingest.extract import extract_html, extract_text

SourceName = Literal["eurlex", "boe", "file"]

USER_AGENT = "Lexground/0.1 (+https://github.com/JLM-2000/Lexground)"
REQUEST_DELAY_SECONDS = 1.0
MAX_ATTEMPTS = 3
CHALLENGE_MAX_BYTES = 8192


class CorpusUnavailableError(RuntimeError):
    """The source could not be read, as distinct from a document that failed to parse."""


@dataclass(slots=True)
class FetchedDocument:
    identifier: str
    language: str
    source_url: str
    text: str


class DocumentSource(ABC):
    """Where a document comes from.

    Fetching, citation style and structure differ per jurisdiction, so each source owns
    its own retrieval and hands back plain text for the shared parser. Adding a
    jurisdiction means implementing this and nothing else.
    """

    name: SourceName

    @abstractmethod
    async def fetch(self, identifier: str, language: str) -> FetchedDocument: ...


class CachingSource(DocumentSource):
    def __init__(self, cache_dir: Path | None = None, *, offline: bool = False) -> None:
        self._cache_dir = cache_dir
        self._offline = offline
        if cache_dir is not None:
            cache_dir.mkdir(parents=True, exist_ok=True)

    def _cache_path(self, identifier: str, language: str) -> Path | None:
        if self._cache_dir is None:
            return None
        return self._cache_dir / f"{identifier}.{language}.txt"

    async def fetch(self, identifier: str, language: str) -> FetchedDocument:
        url = self.url_for(identifier, language)
        cache_path = self._cache_path(identifier, language)

        if cache_path is not None and cache_path.exists():
            return FetchedDocument(identifier, language, url, cache_path.read_text("utf-8"))

        if self._offline:
            raise CorpusUnavailableError(
                f"{identifier}/{language}: not cached and offline mode is set"
            )

        text = await self.download(url, identifier, language)
        if cache_path is not None:
            cache_path.write_text(text, encoding="utf-8")
        return FetchedDocument(identifier, language, url, text)

    @abstractmethod
    def url_for(self, identifier: str, language: str) -> str: ...

    @abstractmethod
    async def download(self, url: str, identifier: str, language: str) -> str: ...


class EurLexSource(CachingSource):
    """EU legislation. Fronted by a JavaScript bot challenge that answers 202 with a stub."""

    name: SourceName = "eurlex"

    def url_for(self, identifier: str, language: str) -> str:
        return (
            "https://eur-lex.europa.eu/legal-content/"
            f"{language.upper()}/TXT/HTML/?uri=CELEX:{identifier}"
        )

    async def download(self, url: str, identifier: str, language: str) -> str:
        async with httpx.AsyncClient(
            headers={"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml"},
            timeout=60.0,
            follow_redirects=True,
        ) as client:
            for attempt in range(MAX_ATTEMPTS):
                response = await client.get(url)
                if response.status_code == 200 and len(response.text) > CHALLENGE_MAX_BYTES:
                    text = extract_html(response.text)
                    if text.strip():
                        await asyncio.sleep(REQUEST_DELAY_SECONDS)
                        return text
                if attempt < MAX_ATTEMPTS - 1:
                    await asyncio.sleep(REQUEST_DELAY_SECONDS * (2**attempt))

        raise CorpusUnavailableError(
            f"{identifier}/{language}: EUR-Lex served a bot challenge "
            f"(HTTP {response.status_code}, {len(response.text)} bytes) after {MAX_ATTEMPTS} "
            "attempts. Seed data/corpus/ from a browser session, or use the fixture corpus."
        )


class BoeSource(CachingSource):
    """Spanish consolidated legislation from the BOE open-data API.

    Returns structured XML whose <bloque id="a14"> elements already carry the article
    numbering, so the text is rebuilt as `Artículo 14` headings for the shared parser
    rather than re-derived from prose.

    The API answers 400 "No soportado ningún mime type" unless Accept names a concrete
    type. A missing header and `*/*` are both rejected, which is why one is set here.
    """

    name: SourceName = "boe"
    api_url = "https://www.boe.es/datosabiertos/api/legislacion-consolidada/id"

    def url_for(self, identifier: str, language: str) -> str:
        return f"{self.api_url}/{identifier}/texto"

    async def download(self, url: str, identifier: str, language: str) -> str:
        async with httpx.AsyncClient(
            headers={"User-Agent": USER_AGENT, "Accept": "application/xml, */*"},
            timeout=90.0,
            follow_redirects=True,
        ) as client:
            response = await client.get(url)

        if response.status_code != 200:
            raise CorpusUnavailableError(
                f"{identifier}: BOE returned HTTP {response.status_code} for {url}"
            )

        text = self.parse_blocks(response.content)
        if not text.strip():
            raise CorpusUnavailableError(f"{identifier}: BOE returned no readable blocks")
        await asyncio.sleep(REQUEST_DELAY_SECONDS)
        return text

    @staticmethod
    def parse_blocks(payload: bytes) -> str:
        root = etree.fromstring(payload)
        sections: list[str] = []

        for block in root.iter("bloque"):
            identifier = (block.get("id") or "").strip()
            body = "\n\n".join(
                fragment.strip() for fragment in block.itertext() if fragment and fragment.strip()
            )
            if not body:
                continue

            heading = BoeSource.heading_for(identifier)
            sections.append(f"{heading}\n\n{body}" if heading else body)

        return "\n\n".join(sections)

    @staticmethod
    def heading_for(block_id: str) -> str | None:
        """Turn a BOE block id into the heading the Spanish parser recognises."""
        if block_id.startswith("a") and block_id[1:].isdigit():
            return f"Artículo {block_id[1:]}"
        return None


class FileSource(DocumentSource):
    """Any document the user supplies: PDF, DOCX, HTML or plain text."""

    name: SourceName = "file"

    def __init__(self, root: Path) -> None:
        self._root = root

    async def fetch(self, identifier: str, language: str) -> FetchedDocument:
        path = (self._root / identifier).resolve()
        if not path.is_file():
            raise CorpusUnavailableError(f"{identifier}: no such file under {self._root}")
        if self._root.resolve() not in path.parents:
            raise CorpusUnavailableError(f"{identifier}: path escapes the corpus directory")

        text = extract_text(path.read_bytes(), path.name)
        if not text.strip():
            raise CorpusUnavailableError(f"{identifier}: no extractable text")
        return FetchedDocument(identifier, language, path.as_uri(), text)


def build_source(
    name: SourceName, *, cache_dir: Path | None, offline: bool, upload_dir: Path
) -> DocumentSource:
    if name == "boe":
        return BoeSource(cache_dir=cache_dir, offline=offline)
    if name == "file":
        return FileSource(upload_dir)
    return EurLexSource(cache_dir=cache_dir, offline=offline)
