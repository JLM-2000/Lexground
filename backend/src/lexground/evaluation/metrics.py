from __future__ import annotations

import math
import re
import statistics
import unicodedata
from collections.abc import Sequence

_WHITESPACE = re.compile(r"\s+")


def normalise(text: str) -> str:
    """Fold case, unicode form and whitespace so quote checks survive PDF artefacts."""
    folded = unicodedata.normalize("NFKC", text).casefold()
    folded = folded.replace("’", "'").replace("‘", "'")
    folded = folded.replace("“", '"').replace("”", '"')
    folded = folded.replace("–", "-").replace("—", "-")
    return _WHITESPACE.sub(" ", folded).strip()


_PARAGRAPH_SUFFIX = re.compile(r"\(\d+[a-z]?\)\s*$")
_PART_SUFFIX = re.compile(r"\s*\[\d+/\d+\]\s*$")


def citation_key(citation: str) -> str:
    """Reduce a pin cite to the provision it belongs to."""
    stripped = _PART_SUFFIX.sub("", citation.strip())
    stripped = _PARAGRAPH_SUFFIX.sub("", stripped).strip()
    return normalise(stripped)


def keys(citations: Sequence[str]) -> list[str]:
    return [citation_key(citation) for citation in citations]


def dedupe(items: Sequence[str]) -> list[str]:
    """Collapse a ranking to distinct entries, keeping the best-ranked occurrence."""
    seen: set[str] = set()
    ordered: list[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            ordered.append(item)
    return ordered


def recall_at_k(retrieved: Sequence[str], relevant: Sequence[str], k: int) -> float:
    if not relevant:
        return 1.0
    hits = set(dedupe(retrieved)[:k]) & set(relevant)
    return len(hits) / len(set(relevant))


def precision_at_k(retrieved: Sequence[str], relevant: Sequence[str], k: int) -> float:
    window = dedupe(retrieved)[:k]
    if not window:
        return 0.0
    return len(set(window) & set(relevant)) / len(window)


def reciprocal_rank(retrieved: Sequence[str], relevant: Sequence[str]) -> float:
    relevant_set = set(relevant)
    for index, item in enumerate(retrieved, start=1):
        if item in relevant_set:
            return 1.0 / index
    return 0.0


def ndcg_at_k(retrieved: Sequence[str], relevant: Sequence[str], k: int) -> float:
    """Binary-relevance nDCG. Rewards ranking the right provision near the top."""
    relevant_set = set(relevant)
    if not relevant_set:
        return 1.0
    dcg = sum(
        1.0 / math.log2(index + 1)
        for index, item in enumerate(dedupe(retrieved)[:k], start=1)
        if item in relevant_set
    )
    ideal = sum(1.0 / math.log2(index + 1) for index in range(1, min(len(relevant_set), k) + 1))
    return dcg / ideal if ideal else 0.0


def citation_scores(actual: Sequence[str], expected: Sequence[str]) -> tuple[float, float]:
    """Precision and recall over pin cites the answer actually claims."""
    if not expected:
        return (1.0, 1.0) if not actual else (0.0, 1.0)
    if not actual:
        return 0.0, 0.0
    actual_set = set(keys(actual))
    expected_set = set(keys(expected))
    overlap = len(actual_set & expected_set)
    return overlap / len(actual_set), overlap / len(expected_set)


def quote_fidelity(quote: str, source_text: str) -> bool:
    """True when the claimed supporting quote really is a span of the cited chunk."""
    normalised_quote = normalise(quote)
    if len(normalised_quote) < 12:
        return False
    return normalised_quote in normalise(source_text)


def percentile(values: Sequence[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(math.ceil(fraction * len(ordered)) - 1, len(ordered) - 1)
    return ordered[max(index, 0)]


def aggregate(per_case: list[dict[str, float]], latencies: list[float]) -> dict[str, float]:
    def mean_of(key: str) -> float:
        values = [case[key] for case in per_case if key in case]
        return round(statistics.fmean(values), 4) if values else 0.0

    keys = {key for case in per_case for key in case}
    summary = {key: mean_of(key) for key in sorted(keys)}
    summary["latency_p50_ms"] = round(percentile(latencies, 0.50), 1)
    summary["latency_p95_ms"] = round(percentile(latencies, 0.95), 1)
    return summary
