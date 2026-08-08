from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path

import httpx
from lxml import html as lxml_html

EURLEX_HTML_URL = "https://eur-lex.europa.eu/legal-content/{language}/TXT/HTML/?uri=CELEX:{celex}"

# EUR-Lex content is freely reusable under the Commission's reuse notice
# (Decision 2011/833/EU). Be a polite client anyway.
USER_AGENT = "Lexground/0.1 (+https://github.com/JLM-2000/Lexground)"
REQUEST_DELAY_SECONDS = 1.0
MAX_ATTEMPTS = 3
CHALLENGE_MAX_BYTES = 8192
"""Challenge stubs are ~2 KB; the smallest real act runs to hundreds of KB."""

_STRIP_TAGS = ("script", "style", "head", "noscript")


class CorpusUnavailableError(RuntimeError):
    """The source could not be read. Distinct from a parse failure so the ingest
    runner can report an upstream problem separately from a bad document."""


@dataclass(slots=True)
class FetchedDocument:
    celex_id: str
    language: str
    source_url: str
    text: str


def extract_text(document_html: str) -> str:
    tree = lxml_html.fromstring(document_html)
    for element in tree.xpath("|".join(f"//{tag}" for tag in _STRIP_TAGS)):
        element.getparent().remove(element)
    for break_tag in tree.xpath("//br"):
        break_tag.tail = "\n" + (break_tag.tail or "")
    blocks = [
        text.strip()
        for text in tree.xpath("//p | //div[not(descendant::p)] | //td")
        for text in [element_text(text)]
        if text and text.strip()
    ]
    deduplicated: list[str] = []
    for block in blocks:
        if not deduplicated or deduplicated[-1] != block:
            deduplicated.append(block)
    return "\n\n".join(deduplicated)


def element_text(element: object) -> str:
    return "".join(element.itertext())  # type: ignore[attr-defined]


class EurLexClient:
    def __init__(self, cache_dir: Path | None = None, *, offline: bool = False) -> None:
        self._cache_dir = cache_dir
        self._offline = offline
        if cache_dir is not None:
            cache_dir.mkdir(parents=True, exist_ok=True)

    def _cache_path(self, celex_id: str, language: str) -> Path | None:
        if self._cache_dir is None:
            return None
        return self._cache_dir / f"{celex_id}.{language}.txt"

    async def fetch(self, celex_id: str, language: str) -> FetchedDocument:
        url = EURLEX_HTML_URL.format(language=language.upper(), celex=celex_id)
        cache_path = self._cache_path(celex_id, language)

        if cache_path is not None and cache_path.exists():
            return FetchedDocument(celex_id, language, url, cache_path.read_text("utf-8"))

        if self._offline:
            raise CorpusUnavailableError(
                f"{celex_id}/{language}: not in the corpus cache and offline mode is set"
            )

        text = await self._fetch_remote(url, celex_id, language)

        if cache_path is not None:
            cache_path.write_text(text, encoding="utf-8")
        return FetchedDocument(celex_id, language, url, text)

    async def _fetch_remote(self, url: str, celex_id: str, language: str) -> str:
        """EUR-Lex fronts its HTML views with a JavaScript bot challenge that answers
        202 with a stub body. That is indistinguishable from success to a naive client,
        so it is checked explicitly rather than left to fail later in the parser."""
        async with httpx.AsyncClient(
            headers={"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml"},
            timeout=60.0,
            follow_redirects=True,
        ) as client:
            for attempt in range(MAX_ATTEMPTS):
                response = await client.get(url)
                if response.status_code == 200 and len(response.text) > CHALLENGE_MAX_BYTES:
                    text = extract_text(response.text)
                    if text.strip():
                        await asyncio.sleep(REQUEST_DELAY_SECONDS)
                        return text
                if attempt < MAX_ATTEMPTS - 1:
                    await asyncio.sleep(REQUEST_DELAY_SECONDS * (2**attempt))

        raise CorpusUnavailableError(
            f"{celex_id}/{language}: EUR-Lex served a bot challenge "
            f"(HTTP {response.status_code}, {len(response.text)} bytes) after "
            f"{MAX_ATTEMPTS} attempts. Seed the cache in data/corpus/ from a browser "
            f"session, or run against the committed fixture corpus."
        )
