from __future__ import annotations

from pydantic import BaseModel, Field


class Citation(BaseModel):
    marker: int = Field(description="The [n] marker used in the answer text.")
    citation: str = Field(description="Pin cite exactly as given in the context block.")
    supporting_quote: str = Field(
        description="Verbatim span from that context block which supports the claim."
    )


class GroundedAnswer(BaseModel):
    """The only shape the synthesiser is allowed to return.

    `answerable` is separate from `answer` so a refusal is a first-class outcome
    that the eval harness can score, rather than prose we have to pattern-match.
    """

    answerable: bool
    answer: str = Field(description="Answer text with [n] markers. Empty when not answerable.")
    citations: list[Citation]
    refusal_reason: str = Field(
        default="", description="Why the context was insufficient. Empty when answerable."
    )


class AnswerOutcome(BaseModel):
    answer: GroundedAnswer
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    model: str = "extractive"
