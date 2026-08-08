import pytest

from lexground.ingest.chunk import build_citation, chunk_units, estimate_tokens
from lexground.ingest.parse import (
    LegalUnit,
    clean_text,
    parse_document,
    split_article_paragraphs,
    split_recitals,
)

ENGLISH_ACT = """
(1) The first recital explains the background to this Regulation and why it matters.

(2) The second recital explains something else entirely about the internal market.

Article 1

Subject matter

1. This Regulation lays down harmonised rules on a topic of interest.

2. This Regulation does not apply to purely personal activities.

Article 2

Definitions

For the purposes of this Regulation, 'thing' means a thing.
"""

SPANISH_ACT = """
(1) El primer considerando explica los antecedentes del presente Reglamento.

Artículo 1

Objeto

1. El presente Reglamento establece normas armonizadas sobre una materia.

2. El presente Reglamento no se aplica a las actividades personales.
"""


class TestCleanText:
    def test_collapses_nbsp_and_runs_of_blank_lines(self) -> None:
        assert clean_text("a\xa0b\n\n\n\nc") == "a b\n\nc"

    def test_normalises_windows_line_endings(self) -> None:
        assert clean_text("a\r\n\r\nb") == "a\n\nb"


class TestRecitals:
    def test_extracts_numbered_recitals(self) -> None:
        units = split_recitals(clean_text(ENGLISH_ACT))
        assert [unit.number for unit in units] == ["1", "2"]
        assert units[0].unit_type == "recital"

    def test_recital_body_excludes_its_own_number(self) -> None:
        units = split_recitals(clean_text(ENGLISH_ACT))
        assert units[0].text.startswith("The first recital")


class TestArticleParagraphs:
    def test_splits_on_numbered_paragraphs(self) -> None:
        units = split_article_paragraphs("1", "Subject matter", "1. First.\n\n2. Second.")
        assert [unit.paragraph for unit in units] == ["1", "2"]
        assert units[0].text == "First."

    def test_article_without_paragraphs_stays_whole(self) -> None:
        units = split_article_paragraphs("2", "Definitions", "A single unnumbered body.")
        assert len(units) == 1
        assert units[0].paragraph is None

    def test_unnumbered_lead_in_attaches_to_preceding_paragraph(self) -> None:
        units = split_article_paragraphs("1", None, "1. First.\n\nContinued.\n\n2. Second.")
        assert units[0].text == "First.\n\nContinued."


class TestParseDocument:
    def test_parses_recitals_and_articles_together(self) -> None:
        units = parse_document(ENGLISH_ACT, language="en")
        assert [unit.unit_type for unit in units[:2]] == ["recital", "recital"]
        articles = [unit for unit in units if unit.unit_type == "article"]
        assert {unit.number for unit in articles} == {"1", "2"}

    def test_captures_article_heading(self) -> None:
        units = parse_document(ENGLISH_ACT, language="en")
        article_one = next(
            unit for unit in units if unit.unit_type == "article" and unit.number == "1"
        )
        assert article_one.heading == "Subject matter"

    def test_spanish_uses_its_own_article_keyword(self) -> None:
        units = parse_document(SPANISH_ACT, language="es")
        articles = [unit for unit in units if unit.unit_type == "article"]
        assert [unit.paragraph for unit in articles] == ["1", "2"]

    def test_english_keyword_does_not_match_spanish_text(self) -> None:
        units = parse_document(SPANISH_ACT, language="en")
        assert not [unit for unit in units if unit.unit_type == "article"]

    def test_document_with_no_articles_still_yields_recitals(self) -> None:
        units = parse_document("(1) Only a recital here.", language="en")
        assert len(units) == 1
        assert units[0].unit_type == "recital"


class TestCitations:
    @pytest.mark.parametrize(
        ("unit", "expected"),
        [
            (LegalUnit("article", "22", "1", None, "x"), "GDPR Art. 22(1)"),
            (LegalUnit("article", "22", None, None, "x"), "GDPR Art. 22"),
            (LegalUnit("recital", "71", None, None, "x"), "GDPR Recital 71"),
        ],
    )
    def test_english_citation_forms(self, unit: LegalUnit, expected: str) -> None:
        assert build_citation(unit, "GDPR", "en") == expected

    def test_spanish_recital_label_is_localised(self) -> None:
        unit = LegalUnit("recital", "6", None, None, "x")
        assert build_citation(unit, "ADSR", "es") == "ADSR Considerando 6"


class TestChunking:
    def test_every_chunk_carries_exactly_one_citation(self) -> None:
        units = parse_document(ENGLISH_ACT, language="en")
        chunks = chunk_units(units, short_title="TEST", language="en")
        assert all(chunk.citation for chunk in chunks)
        assert len({chunk.ordinal for chunk in chunks}) == len(chunks)

    def test_ordinals_are_contiguous_from_zero(self) -> None:
        units = parse_document(ENGLISH_ACT, language="en")
        chunks = chunk_units(units, short_title="TEST", language="en")
        assert [chunk.ordinal for chunk in chunks] == list(range(len(chunks)))

    def test_oversized_provision_is_split_and_parts_are_labelled(self) -> None:
        long_unit = LegalUnit("article", "1", "1", None, "word. " * 900)
        chunks = chunk_units([long_unit], short_title="TEST", language="en")
        assert len(chunks) > 1
        assert chunks[0].citation.endswith(f"[1/{len(chunks)}]")

    def test_split_parts_overlap_so_boundary_text_stays_findable(self) -> None:
        long_unit = LegalUnit("article", "1", None, None, "alpha. " * 700)
        chunks = chunk_units([long_unit], short_title="TEST", language="en")
        total = sum(len(chunk.text) for chunk in chunks)
        assert total > len(long_unit.text.strip())

    def test_token_estimate_is_positive(self) -> None:
        assert estimate_tokens("a") >= 1
