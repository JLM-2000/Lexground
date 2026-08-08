from __future__ import annotations

import json
from abc import ABC, abstractmethod

from lexground.config import Settings
from lexground.retrieval.types import RetrievalResult
from lexground.synthesis.prompts import SYSTEM_PROMPT, build_user_prompt
from lexground.synthesis.providers import (
    LLMProvider,
    SynthesisError,
    build_provider,
    harden_schema,
)
from lexground.synthesis.schema import AnswerOutcome, Citation, GroundedAnswer

WEAK_CONTEXT_REFUSAL = (
    "Retrieval did not surface a provision close enough to the question to answer from. "
    "Answering would require relying on knowledge outside the indexed corpus."
)

MODEL_REFUSAL = "The model declined to answer this request."


def _refused(reason: str, model: str = "extractive") -> AnswerOutcome:
    return AnswerOutcome(
        answer=GroundedAnswer(answerable=False, answer="", citations=[], refusal_reason=reason),
        model=model,
    )


class Answerer(ABC):
    @abstractmethod
    async def answer(self, question: str, retrieval: RetrievalResult) -> AnswerOutcome: ...


class ExtractiveAnswerer(Answerer):
    """Offline fallback used when no API key is configured."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    async def answer(self, question: str, retrieval: RetrievalResult) -> AnswerOutcome:
        if retrieval.weak_context or not retrieval.chunks:
            return _refused(WEAK_CONTEXT_REFUSAL)

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


class GeneratedAnswerer(Answerer):
    """Composes an answer with whichever provider is configured."""

    def __init__(self, provider: LLMProvider) -> None:
        self._provider = provider
        self._schema = harden_schema(GroundedAnswer.model_json_schema())

    async def answer(self, question: str, retrieval: RetrievalResult) -> AnswerOutcome:
        if retrieval.weak_context or not retrieval.chunks:
            return _refused(WEAK_CONTEXT_REFUSAL, self._provider.model)

        completion = await self._provider.complete_json(
            system=SYSTEM_PROMPT,
            user=build_user_prompt(question, retrieval.chunks),
            schema=self._schema,
            max_tokens=4096,
        )

        if completion.refused:
            return AnswerOutcome(
                answer=GroundedAnswer(
                    answerable=False, answer="", citations=[], refusal_reason=MODEL_REFUSAL
                ),
                input_tokens=completion.input_tokens,
                output_tokens=completion.output_tokens,
                cost_usd=completion.cost_usd(),
                model=completion.model,
            )

        try:
            answer = GroundedAnswer.model_validate(json.loads(completion.payload))
        except Exception as error:
            raise SynthesisError(
                f"{self._provider.name} returned an off-schema answer: {error}"
            ) from error

        return AnswerOutcome(
            answer=answer,
            input_tokens=completion.input_tokens,
            output_tokens=completion.output_tokens,
            cost_usd=completion.cost_usd(),
            model=completion.model,
        )


def build_answerer(settings: Settings) -> Answerer:
    provider = build_provider(settings)
    if provider is None:
        return ExtractiveAnswerer(settings)
    return GeneratedAnswerer(provider)
