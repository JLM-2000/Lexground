from __future__ import annotations

import hashlib
import math
import struct
from abc import ABC, abstractmethod
from functools import lru_cache

from lexground.config import Settings


class Embedder(ABC):
    """Embedding backends are swappable so tests and CI never download a model."""

    dimensions: int

    @abstractmethod
    def embed_documents(self, texts: list[str]) -> list[list[float]]: ...

    @abstractmethod
    def embed_query(self, text: str) -> list[float]: ...


def _l2_normalise(vector: list[float]) -> list[float]:
    norm = math.sqrt(sum(component * component for component in vector))
    if norm == 0.0:
        return vector
    return [component / norm for component in vector]


class HashEmbedder(Embedder):
    """Deterministic bag-of-words hashing embedder.

    Not competitive with a trained model, but it is dependency-free, stable across
    runs and machines, and good enough that retrieval tests assert real behaviour
    rather than mocks.
    """

    def __init__(self, dimensions: int = 384) -> None:
        self.dimensions = dimensions

    def _embed(self, text: str) -> list[float]:
        vector = [0.0] * self.dimensions
        tokens = [token for token in text.lower().split() if token]
        for token in tokens:
            digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
            position = struct.unpack("<Q", digest)[0] % self.dimensions
            sign = 1.0 if digest[0] & 1 else -1.0
            vector[position] += sign
        return _l2_normalise(vector)

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._embed(text) for text in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._embed(text)


class FastEmbedEmbedder(Embedder):
    """Multilingual ONNX embeddings.

    E5 models are trained with asymmetric "query:"/"passage:" prefixes and score poorly
    without them; sentence-transformers models are trained symmetrically and score
    poorly *with* them. The prefix is therefore keyed off the model family rather than
    applied unconditionally.
    """

    def __init__(self, model_name: str, dimensions: int) -> None:
        from fastembed import TextEmbedding

        self.dimensions = dimensions
        self._model = TextEmbedding(model_name=model_name)
        self._uses_e5_prefixes = "e5" in model_name.lower()

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        if self._uses_e5_prefixes:
            texts = [f"passage: {text}" for text in texts]
        return [vector.tolist() for vector in self._model.embed(texts)]

    def embed_query(self, text: str) -> list[float]:
        if self._uses_e5_prefixes:
            text = f"query: {text}"
        return next(iter(self._model.embed([text]))).tolist()


@lru_cache
def _build(backend: str, model_name: str, dimensions: int) -> Embedder:
    if backend == "fastembed":
        return FastEmbedEmbedder(model_name, dimensions)
    return HashEmbedder(dimensions)


def get_embedder(settings: Settings) -> Embedder:
    """Cached on the fields that define the model, not on the Settings object —
    loading an ONNX model per request would dominate retrieval latency."""
    return _build(
        settings.embedding_backend, settings.embedding_model, settings.embedding_dimensions
    )
