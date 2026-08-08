from __future__ import annotations

import json

from pydantic import BaseModel, Field

from lexground.config import Settings
from lexground.retrieval.types import RetrievedChunk
from lexground.synthesis.providers import LLMProvider, build_provider, harden_schema
from lexground.synthesis.schema import GroundedAnswer

JUDGE_SYSTEM_PROMPT = """\
You audit whether a legal answer is supported by the sources it cites.

You are not judging whether the answer is good law, well written, or complete. You are \
judging one thing: does every legal proposition in the answer follow from the quoted \
context blocks?

Mark a claim unsupported when it states a rule, threshold, exception, or obligation that \
the cited block does not contain — including claims that are correct as a matter of law \
but absent from the supplied text. Restating the sources in different words is supported. \
Adding a condition, scope, or consequence the sources do not state is not.

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
        cited = {citation.citation for citation in answer.citations}
        blocks = [chunk for chunk in chunks if chunk.citation in cited] or chunks
        context = "\n\n".join(
            f"[{index}] {chunk.citation}\n<source>{chunk.text.strip()}</source>"
            for index, chunk in enumerate(blocks, start=1)
        )

        completion = await self._provider.complete_json(
            system=JUDGE_SYSTEM_PROMPT,
            user=(
                f"<question>{question}</question>\n\n"
                f"<cited_sources>\n{context}\n</cited_sources>\n\n"
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
