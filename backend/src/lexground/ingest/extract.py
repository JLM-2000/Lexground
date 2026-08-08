from __future__ import annotations

import io
from pathlib import Path

from lxml import html as lxml_html

TEXT_SUFFIXES = {".txt", ".md", ".markdown", ".text"}
HTML_SUFFIXES = {".html", ".htm", ".xhtml"}
SUPPORTED_SUFFIXES = TEXT_SUFFIXES | HTML_SUFFIXES | {".pdf", ".docx"}

_STRIP_TAGS = ("script", "style", "head", "noscript")


class UnsupportedDocumentError(ValueError):
    """The file is not a format the ingest pipeline can read."""


def extract_html(markup: str) -> str:
    tree = lxml_html.fromstring(markup)
    for element in tree.xpath("|".join(f"//{tag}" for tag in _STRIP_TAGS)):
        element.getparent().remove(element)
    for break_tag in tree.xpath("//br"):
        break_tag.tail = "\n" + (break_tag.tail or "")

    blocks: list[str] = []
    for element in tree.xpath("//h1 | //h2 | //h3 | //h4 | //p | //li | //td"):
        text = "".join(element.itertext()).strip()
        if text and (not blocks or blocks[-1] != text):
            blocks.append(text)
    return "\n\n".join(blocks)


def extract_pdf(payload: bytes) -> str:
    """One blank line between paragraphs, two between pages.

    Page boundaries are kept because a page number is often the only locator a reader
    has for a PDF that carries no article numbering.
    """
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(payload))
    pages = []
    for page in reader.pages:
        text = (page.extract_text() or "").strip()
        if text:
            pages.append(text)
    return "\n\n\n".join(pages)


def extract_docx(payload: bytes) -> str:
    from docx import Document as DocxDocument

    document = DocxDocument(io.BytesIO(payload))
    blocks = []
    for paragraph in document.paragraphs:
        text = paragraph.text.strip()
        if not text:
            continue
        if paragraph.style is not None and paragraph.style.name.startswith("Heading"):
            blocks.append(f"# {text}")
        else:
            blocks.append(text)
    return "\n\n".join(blocks)


def extract_text(payload: bytes, filename: str) -> str:
    """Turn an uploaded file into plain text, dispatching on its suffix."""
    suffix = Path(filename).suffix.lower()

    if suffix == ".pdf":
        return extract_pdf(payload)
    if suffix == ".docx":
        return extract_docx(payload)

    decoded = payload.decode("utf-8", errors="replace")
    if suffix in HTML_SUFFIXES:
        return extract_html(decoded)
    if suffix in TEXT_SUFFIXES:
        return decoded

    raise UnsupportedDocumentError(
        f"{filename}: expected one of {', '.join(sorted(SUPPORTED_SUFFIXES))}"
    )
