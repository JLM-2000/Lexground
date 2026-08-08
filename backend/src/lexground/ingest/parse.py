from __future__ import annotations

import re
from dataclasses import dataclass

# EUR-Lex publishes the same act in every official language, so the parser keys on
# the article keyword per language rather than assuming an English corpus.
ARTICLE_KEYWORD = {
    "en": "Article",
    "es": "Artículo",
    "fr": "Article",
    "de": "Artikel",
}

_RECITAL = re.compile(r"^\(\s*(\d{1,3})\s*\)\s+(.*)", re.DOTALL)
_PARAGRAPH = re.compile(r"^(\d{1,2})\.\s+")
_WHITESPACE = re.compile(r"[ \t]+")
_BLANK_LINES = re.compile(r"\n{3,}")


@dataclass(slots=True)
class LegalUnit:
    unit_type: str
    number: str | None
    paragraph: str | None
    heading: str | None
    text: str


def clean_text(raw: str) -> str:
    text = raw.replace("\xa0", " ").replace("\r\n", "\n").replace("\r", "\n")
    text = _WHITESPACE.sub(" ", text)
    text = "\n".join(line.strip() for line in text.split("\n"))
    return _BLANK_LINES.sub("\n\n", text).strip()


def _article_pattern(language: str) -> re.Pattern[str]:
    keyword = ARTICLE_KEYWORD.get(language, ARTICLE_KEYWORD["en"])
    return re.compile(rf"^{re.escape(keyword)}\s+(\d{{1,3}}[a-z]?)\s*$", re.MULTILINE)


def split_recitals(preamble: str) -> list[LegalUnit]:
    """Recitals are numbered '(1) …' blocks. They are kept whole — a recital is the
    interpretive unit courts cite, and splitting one destroys its meaning."""
    units: list[LegalUnit] = []
    for block in preamble.split("\n\n"):
        match = _RECITAL.match(block.strip())
        if match:
            units.append(
                LegalUnit(
                    unit_type="recital",
                    number=match.group(1),
                    paragraph=None,
                    heading=None,
                    text=match.group(2).strip(),
                )
            )
    return units


def split_article_paragraphs(number: str, heading: str | None, body: str) -> list[LegalUnit]:
    """Split an article on its numbered paragraphs.

    Paragraph is the level lawyers actually cite ('Article 22(1)'), so it is the level
    the index addresses. Articles with no numbered paragraphs stay whole.
    """
    lines = body.split("\n\n")
    units: list[LegalUnit] = []
    current_number: str | None = None
    buffer: list[str] = []

    def flush() -> None:
        if not buffer:
            return
        text = "\n\n".join(buffer).strip()
        if text:
            units.append(
                LegalUnit(
                    unit_type="article",
                    number=number,
                    paragraph=current_number,
                    heading=heading,
                    text=text,
                )
            )

    for block in lines:
        stripped = block.strip()
        if not stripped:
            continue
        match = _PARAGRAPH.match(stripped)
        if match:
            flush()
            buffer = [stripped[match.end() :].strip()]
            current_number = match.group(1)
        else:
            buffer.append(stripped)
    flush()

    if not units and body.strip():
        units.append(
            LegalUnit(
                unit_type="article",
                number=number,
                paragraph=None,
                heading=heading,
                text=body.strip(),
            )
        )
    return units


def parse_document(text: str, *, language: str = "en") -> list[LegalUnit]:
    """Split an act into its citable units: recitals, then articles by paragraph."""
    cleaned = clean_text(text)
    pattern = _article_pattern(language)
    matches = list(pattern.finditer(cleaned))

    if not matches:
        return split_recitals(cleaned)

    units = split_recitals(cleaned[: matches[0].start()])

    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(cleaned)
        block = cleaned[match.end() : end].strip()
        if not block:
            continue
        parts = block.split("\n\n", 1)
        heading, body = (
            (parts[0].strip(), parts[1])
            if len(parts) == 2 and len(parts[0]) < 120
            else (None, block)
        )
        units.extend(split_article_paragraphs(match.group(1), heading, body))

    return units
