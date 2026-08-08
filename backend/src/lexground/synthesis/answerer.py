from __future__ import annotations

import json
from abc import ABC, abstractmethod
from typing import Any

from lexground.config import Settings
from lexground.retrieval.types import RetrievalResult
from lexground.synthesis.prompts import SYSTEM_PROMPT, build_user_prompt
from lexground.synthesis.schema import AnswerOutcome, Citation, GroundedAnswer

INPUT_COST_PER_MTOK = 5.00
OUTPUT_COST_PER_MTOK = 25.00

WEAK_CONTEXT_REFUSAL = (
    "Retrieval did not surface a provision close enough to the question to answer from. "
    "Answering would require relying on knowledge outside the indexed corpus."
)


def harden_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Close every object and mark every property required."""
    if schema.get("type") == "object":
        schema["additionalProperties"] = False
        if properties := schema.get("properties"):
            schema["required"] = sorted(properties)
    for key in ("properties", "$defs"):
        for value in schema.get(key, {}).values():
            if isinstance(value, dict):
                harden_schema(value)
    if isinstance(items := schema.get("items"), dict):
        harden_schema(items)
    return schema


class Answerer(ABC):
    @abstractmethod
    async def answer(self, question: str, retrieval: RetrievalResult) -> AnswerOutcome: ...


class ExtractiveAnswerer(Answerer):
    """Offline fallback used when no API key is configured."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    async def answer(self, question: str, retrieval: RetrievalResult) -> AnswerOutcome:
        if retrieval.weak_context or not retrieval.chunks:
            return AnswerOutcome(
                answer=GroundedAnswer(
                    answerable=False,
                    answer="",
                    citations=[],
                    refusal_reason=WEAK_CONTEXT_REFUSAL,
                )
            )

        top = retrieval.chunks[0]
        excerpt = top.text.strip()
        if len(excerpt) > 600:
            excerpt = excerpt[:600].rsplit(" ", 1)[0] + "…"

        return AnswerOutcome(
            answer=GroundedAnswer(
                answerable=True,
                answer=f"{excerpt} [1]",
                citations=[Citation(marker=1, citation=top.citation, supporting_quote=excerpt)],
            )
        )


class ClaudeAnswerer(Answerer):
    def __init__(self, settings: Settings) -> None:
        from anthropic import AsyncAnthropic

        self._settings = settings
        self._client = AsyncAnthropic(
            api_key=settings.anthropic_api_key,
            timeout=settings.request_timeout_seconds,
        )
        self._schema = harden_schema(GroundedAnswer.model_json_schema())

    async def answer(self, question: str, retrieval: RetrievalResult) -> AnswerOutcome:
        if retrieval.weak_context or not retrieval.chunks:
            return AnswerOutcome(
                answer=GroundedAnswer(
                    answerable=False,
                    answer="",
                    citations=[],
                    refusal_reason=WEAK_CONTEXT_REFUSAL,
                ),
                model=self._settings.synthesis_model,
            )

        response = await self._client.messages.create(
            model=self._settings.synthesis_model,
            max_tokens=4096,
            system=[
                {
                    "type": "text",
                    "text": SYSTEM_PROMPT,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            output_config={
                "effort": self._settings.synthesis_effort,
                "format": {"type": "json_schema", "schema": self._schema},
            },
            messages=[{"role": "user", "content": build_user_prompt(question, retrieval.chunks)}],
        )

        usage = response.usage
        cost = (
            usage.input_tokens / 1_000_000 * INPUT_COST_PER_MTOK
            + usage.output_tokens / 1_000_000 * OUTPUT_COST_PER_MTOK
        )

        if response.stop_reason == "refusal":
            return AnswerOutcome(
                answer=GroundedAnswer(
                    answerable=False,
                    answer="",
                    citations=[],
                    refusal_reason="The model declined to answer this request.",
                ),
                input_tokens=usage.input_tokens,
                output_tokens=usage.output_tokens,
                cost_usd=cost,
                model=response.model,
            )

        payload = "".join(block.text for block in response.content if block.type == "text")
        return AnswerOutcome(
            answer=GroundedAnswer.model_validate(json.loads(payload)),
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            cost_usd=cost,
            model=response.model,
        )


def build_answerer(settings: Settings) -> Answerer:
    if settings.synthesis_backend == "claude":
        return ClaudeAnswerer(settings)
    return ExtractiveAnswerer(settings)
