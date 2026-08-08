from __future__ import annotations

import re
from dataclasses import dataclass

from lexground.ingest.parse import LegalUnit

MAX_CHARS = 2400
OVERLAP_CHARS = 200

PAGE_BREAK = "\n\n\n"
MAX_HEADING_CHARS = 90
_MARKDOWN_HEADING = re.compile(r"^#{1,6}\s+(.*)")
_SENTENCE_END = re.compile(r"[.:;!?]\s*$")

RECITAL_LABEL = {"en": "Recital", "es": "Considerando", "fr": "Considérant", "de": "Erwägungsgrund"}
ARTICLE_LABEL = {"en": "Art.", "es": "Art.", "fr": "Art.", "de": "Art."}


@dataclass(slots=True)
class Chunk:
    ordinal: int
    unit_type: str
    unit_number: str | None
    paragraph: str | None
    heading: str | None
    citation: str
    text: str
    token_estimate: int


def build_citation(unit: LegalUnit, short_title: str, language: str) -> str:
    if unit.unit_type == "recital":
        label = RECITAL_LABEL.get(language, RECITAL_LABEL["en"])
        return f"{short_title} {label} {unit.number}"
    label = ARTICLE_LABEL.get(language, ARTICLE_LABEL["en"])
    if unit.paragraph:
        return f"{short_title} {label} {unit.number}({unit.paragraph})"
    return f"{short_title} {label} {unit.number}"


def estimate_tokens(text: str) -> int:
    """Cheap proxy — good enough for budgeting context, not for billing."""
    return max(1, len(text) // 4)


def _split_long(text: str) -> list[str]:
    """Overflow split for the rare provision longer than the context budget."""
    if len(text) <= MAX_CHARS:
        return [text]

    parts: list[str] = []
    start = 0
    while start < len(text):
        end = min(start + MAX_CHARS, len(text))
        if end < len(text):
            boundary = text.rfind(". ", start + MAX_CHARS // 2, end)
            if boundary != -1:
                end = boundary + 1
        parts.append(text[start:end].strip())
        if end >= len(text):
            break
        start = max(end - OVERLAP_CHARS, start + 1)
    return [part for part in parts if part]


def chunk_units(units: list[LegalUnit], *, short_title: str, language: str) -> list[Chunk]:
    """One chunk per citable unit."""
    chunks: list[Chunk] = []
    ordinal = 0

    for unit in units:
        citation = build_citation(unit, short_title, language)
        pieces = _split_long(unit.text)
        for index, piece in enumerate(pieces):
            suffix = f" [{index + 1}/{len(pieces)}]" if len(pieces) > 1 else ""
            chunks.append(
                Chunk(
                    ordinal=ordinal,
                    unit_type=unit.unit_type,
                    unit_number=unit.number,
                    paragraph=unit.paragraph,
                    heading=unit.heading,
                    citation=f"{citation}{suffix}",
                    text=piece,
                    token_estimate=estimate_tokens(piece),
                )
            )
            ordinal += 1

    return chunks


def detect_heading(block: str) -> str | None:
    """A heading is a markdown heading, or a short line that does not end a sentence."""
    stripped = block.strip()
    if match := _MARKDOWN_HEADING.match(stripped):
        return match.group(1).strip() or None
    if "\n" in stripped or len(stripped) > MAX_HEADING_CHARS:
        return None
    if _SENTENCE_END.search(stripped) or not stripped:
        return None
    return stripped


def chunk_prose(text: str, *, short_title: str) -> list[Chunk]:
    """Chunk a document that carries no article numbering.

    Uploaded material rarely has provisions to cite, so the locator becomes the nearest
    heading, falling back to a page number and then a paragraph ordinal. Each is
    something a reader can actually find in the original, which is what keeps quote
    fidelity meaningful outside legislation.
    """
    chunks: list[Chunk] = []
    ordinal = 0
    heading: str | None = None
    paragraph_number = 0

    for page_number, page in enumerate(text.split(PAGE_BREAK), start=1):
        paged = PAGE_BREAK in text
        for block in page.split("\n\n"):
            body = block.strip()
            if not body:
                continue

            if found := detect_heading(body):
                heading = found
                continue

            paragraph_number += 1
            if heading:
                citation = f"{short_title} § {heading}"
            elif paged:
                citation = f"{short_title} p. {page_number}"
            else:
                citation = f"{short_title} ¶{paragraph_number}"

            pieces = _split_long(body)
            for index, piece in enumerate(pieces):
                suffix = f" [{index + 1}/{len(pieces)}]" if len(pieces) > 1 else ""
                chunks.append(
                    Chunk(
                        ordinal=ordinal,
                        unit_type="section" if heading else "passage",
                        unit_number=str(page_number) if paged else None,
                        paragraph=str(paragraph_number),
                        heading=heading,
                        citation=f"{citation}{suffix}",
                        text=piece,
                        token_estimate=estimate_tokens(piece),
                    )
                )
                ordinal += 1

    return chunks
