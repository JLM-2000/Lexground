from __future__ import annotations

from dataclasses import dataclass

from lexground.ingest.parse import LegalUnit

MAX_CHARS = 2400
OVERLAP_CHARS = 200

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
    """Overflow split for the rare provision longer than the context budget.

    Overlap keeps a sentence that straddles the boundary retrievable from either half.
    """
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
    """One chunk per citable unit.

    Fixed-window chunking would cut across article boundaries and make it impossible to
    say which provision a retrieved span belongs to. Here every chunk carries exactly
    one pin cite, which is what makes citation accuracy measurable downstream.
    """
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
