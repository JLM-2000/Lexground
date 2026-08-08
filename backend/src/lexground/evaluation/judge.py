from __future__ import annotations

import json

from pydantic import BaseModel, Field

from lexground.config import Settings
from lexground.retrieval.types import RetrievedChunk
from lexground.synthesis.prompts import format_context
from lexground.synthesis.providers import LLMProvider, build_provider, harden_schema
from lexground.synthesis.schema import GroundedAnswer

JUDGE_SYSTEM_PROMPT = """\
You audit whether a legal answer is supported by the sources supplied with it.

You are not judging whether the answer is good law, well written, or complete. You are \
judging one thing: does every legal proposition in the answer follow from the source \
blocks?

Mark a claim unsupported when it states a rule, threshold, exception, or obligation that \
the sources do not contain, including claims that are correct as a matter of law but \
absent from the supplied text. Restating a source in different words is supported. Adding \
a condition, scope, or consequence the sources do not state is not.

Two rules that decide the awkward cases:

Ignore citation markers entirely. Whether the answer wrote [3] where it meant [5] is \
scored separately and is none of your concern. Judge each proposition against the whole \
set of sources, not against the block whose number the answer happened to write.

A statement that the sources do not address something is supported when it is true of the \
sources you were given. Saying "the text sets out no other conditions" is a claim about \
this material, not an assertion about the law, so verify it rather than rejecting it.

Report the unsupported claims verbatim from the answer.\
"""


class JudgeVerdict(BaseModel):
    grounded: bool = Field(description="True when every claim follows from the cited blocks.")
    unsupported_claims: list[str] = Field(
        default_factory=list, description="Verbatim spans from the answer that lack support."
    )
    rationale: str = Field(description="One or two sentences explaining the verdict.")


class GroundednessJudge:
    """LLM-as-judge over answer faithfulness."""

    def __init__(self, provider: LLMProvider) -> None:
        self._provider = provider
        self._schema = harden_schema(JudgeVerdict.model_json_schema())

    async def evaluate(
        self, question: str, answer: GroundedAnswer, chunks: list[RetrievedChunk]
    ) -> JudgeVerdict:
        completion = await self._provider.complete_json(
            system=JUDGE_SYSTEM_PROMPT,
            user=(
                f"<question>{question}</question>\n\n"
                f"<sources>\n{format_context(chunks)}\n</sources>\n\n"
                f"<answer>{answer.answer}</answer>"
            ),
            schema=self._schema,
            max_tokens=2048,
        )

        if completion.refused:
            return JudgeVerdict(
                grounded=False,
                unsupported_claims=[],
                rationale="Judge declined to evaluate this case.",
            )

        return JudgeVerdict.model_validate(json.loads(completion.payload))


def build_judge(settings: Settings) -> GroundednessJudge | None:
    provider = build_provider(settings)
    return None if provider is None else GroundednessJudge(provider)
