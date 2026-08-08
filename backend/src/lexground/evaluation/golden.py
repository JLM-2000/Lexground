from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel, Field


class GoldenCase(BaseModel):
    """One graded question.

    Relevance is keyed on pin cites rather than chunk ids so the golden set survives
    re-ingestion and chunking changes — the thing being asserted is "the answer must
    rest on Article 22", not "on row 4f3a…".
    """

    id: str
    question: str
    language: str = "en"
    answerable: bool = True
    relevant_citations: list[str] = Field(default_factory=list)
    expected_citations: list[str] = Field(default_factory=list)
    notes: str = ""

    def model_post_init(self, _context: object) -> None:
        if self.answerable and not self.relevant_citations:
            raise ValueError(f"case {self.id}: answerable case needs relevant_citations")
        if not self.expected_citations and self.answerable:
            self.expected_citations = list(self.relevant_citations)


def load_golden_set(path: Path) -> list[GoldenCase]:
    cases: list[GoldenCase] = []
    seen: set[str] = set()
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            line = line.strip()
            if not line or line.startswith("//"):
                continue
            try:
                case = GoldenCase.model_validate(json.loads(line))
            except Exception as error:
                raise ValueError(f"{path}:{line_number}: {error}") from error
            if case.id in seen:
                raise ValueError(f"{path}:{line_number}: duplicate case id {case.id!r}")
            seen.add(case.id)
            cases.append(case)
    if not cases:
        raise ValueError(f"{path}: golden set is empty")
    return cases
