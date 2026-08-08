from __future__ import annotations

import json
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

import httpx

logger = logging.getLogger(__name__)

PRICING_USD_PER_MTOK: dict[str, tuple[float, float]] = {
    "claude-opus-5": (5.00, 25.00),
    "claude-sonnet-5": (3.00, 15.00),
    "claude-haiku-4-5": (1.00, 5.00),
    "deepseek-chat": (0.28, 0.42),
    "deepseek-reasoner": (0.28, 1.68),
}


class SynthesisError(RuntimeError):
    """The provider returned nothing usable."""


@dataclass(slots=True)
class Completion:
    payload: str
    input_tokens: int
    output_tokens: int
    model: str
    refused: bool = False
    requested_model: str = ""

    def cost_usd(self) -> float:
        """Aliases resolve server-side, so fall back to the model that was asked for."""
        rates = PRICING_USD_PER_MTOK.get(self.model) or PRICING_USD_PER_MTOK.get(
            self.requested_model
        )
        if rates is None:
            return 0.0
        return self.input_tokens / 1e6 * rates[0] + self.output_tokens / 1e6 * rates[1]


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


def extract_json_object(text: str) -> str:
    """Recover the outermost JSON object from a response that wrapped it in prose."""
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end <= start:
        raise SynthesisError(f"no JSON object in response: {text[:200]!r}")
    return text[start : end + 1]


class LLMProvider(ABC):
    name: str
    model: str

    @abstractmethod
    async def complete_json(
        self, *, system: str, user: str, schema: dict[str, Any], max_tokens: int
    ) -> Completion: ...


class AnthropicProvider(LLMProvider):
    """Enforces the schema server-side, so the response is valid by construction."""

    name = "anthropic"

    def __init__(self, api_key: str, model: str, effort: str, timeout: float) -> None:
        from anthropic import AsyncAnthropic

        self.model = model
        self._effort = effort
        self._client = AsyncAnthropic(api_key=api_key, timeout=timeout)

    async def complete_json(
        self, *, system: str, user: str, schema: dict[str, Any], max_tokens: int
    ) -> Completion:
        response = await self._client.messages.create(  # type: ignore[call-overload]
            model=self.model,
            max_tokens=max_tokens,
            system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            output_config={
                "effort": self._effort,
                "format": {"type": "json_schema", "schema": schema},
            },
            messages=[{"role": "user", "content": user}],
        )
        return Completion(
            payload="".join(block.text for block in response.content if block.type == "text"),
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
            model=response.model,
            refused=response.stop_reason == "refusal",
            requested_model=self.model,
        )


class DeepSeekProvider(LLMProvider):
    """OpenAI-compatible chat completions.

    DeepSeek offers a JSON *mode*, not schema enforcement: it guarantees syntactically
    valid JSON but nothing about the shape. The schema therefore goes in the prompt and
    the caller validates, with a bounded retry that feeds the validation error back.
    """

    name = "deepseek"
    base_url = "https://api.deepseek.com"
    max_attempts = 3

    def __init__(self, api_key: str, model: str, timeout: float) -> None:
        self.model = model
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=timeout,
        )

    async def complete_json(
        self, *, system: str, user: str, schema: dict[str, Any], max_tokens: int
    ) -> Completion:
        instructions = (
            f"{system}\n\n"
            "Reply with a single JSON object and nothing else. It must validate against "
            f"this JSON Schema:\n{json.dumps(schema, indent=2)}"
        )
        messages = [
            {"role": "system", "content": instructions},
            {"role": "user", "content": user},
        ]

        last_error = ""
        for attempt in range(1, self.max_attempts + 1):
            response = await self._client.post(
                "/chat/completions",
                json={
                    "model": self.model,
                    "messages": messages,
                    "response_format": {"type": "json_object"},
                    "max_tokens": max_tokens,
                    "stream": False,
                },
            )
            response.raise_for_status()
            body = response.json()

            choice = body["choices"][0]
            usage = body.get("usage", {})
            completion = Completion(
                payload=extract_json_object(choice["message"]["content"] or ""),
                input_tokens=usage.get("prompt_tokens", 0),
                output_tokens=usage.get("completion_tokens", 0),
                model=body.get("model", self.model),
                refused=choice.get("finish_reason") == "content_filter",
                requested_model=self.model,
            )

            try:
                json.loads(completion.payload)
            except json.JSONDecodeError as error:
                last_error = str(error)
                logger.warning("deepseek returned invalid json (attempt %d): %s", attempt, error)
                messages.append({"role": "assistant", "content": completion.payload})
                messages.append(
                    {
                        "role": "user",
                        "content": (
                            f"That was not valid JSON ({error}). Return only the JSON object."
                        ),
                    }
                )
                continue
            return completion

        raise SynthesisError(
            f"deepseek produced no valid JSON in {self.max_attempts} attempts: {last_error}"
        )

    async def aclose(self) -> None:
        await self._client.aclose()


def build_provider(settings: Any) -> LLMProvider | None:
    backend = settings.synthesis_backend
    if backend == "anthropic":
        return AnthropicProvider(
            api_key=settings.anthropic_api_key,
            model=settings.anthropic_model,
            effort=settings.synthesis_effort,
            timeout=settings.request_timeout_seconds,
        )
    if backend == "deepseek":
        return DeepSeekProvider(
            api_key=settings.deepseek_api_key,
            model=settings.deepseek_model,
            timeout=settings.request_timeout_seconds,
        )
    return None
