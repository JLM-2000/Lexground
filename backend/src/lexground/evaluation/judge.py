from __future__ import annotations

import json

from pydantic import BaseModel, Field

from lexground.config import Settings
from lexground.retrieval.types import RetrievedChunk
from lexground.synthesis.answerer import harden_schema
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

    def __init__(self, settings: Settings) -> None:
        from anthropic import AsyncAnthropic

        self._settings = settings
        self._client = AsyncAnthropic(
            api_key=settings.anthropic_api_key,
            timeout=settings.request_timeout_seconds,
        )
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

        response = await self._client.messages.create(
            model=self._settings.judge_model,
            max_tokens=2048,
            system=[
                {
                    "type": "text",
                    "text": JUDGE_SYSTEM_PROMPT,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            output_config={
                "effort": "medium",
                "format": {"type": "json_schema", "schema": self._schema},
            },
            messages=[
                {
                    "role": "user",
                    "content": (
                        f"<question>{question}</question>\n\n"
                        f"<cited_sources>\n{context}\n</cited_sources>\n\n"
                        f"<answer>{answer.answer}</answer>"
                    ),
                }
            ],
        )

        if response.stop_reason == "refusal":
            return JudgeVerdict(
                grounded=False,
                unsupported_claims=[],
                rationale="Judge declined to evaluate this case.",
            )

        payload = "".join(block.text for block in response.content if block.type == "text")
        return JudgeVerdict.model_validate(json.loads(payload))
