from prometheus_client import Counter, Gauge, Histogram

QUERY_TOTAL = Counter(
    "lexground_queries_total",
    "Questions answered, split by whether the system was willing to answer.",
    ["answered", "language"],
)

QUERY_LATENCY = Histogram(
    "lexground_query_latency_seconds",
    "End-to-end question latency.",
    buckets=(0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0),
)

RETRIEVAL_LATENCY = Histogram(
    "lexground_retrieval_latency_seconds",
    "Hybrid retrieval latency, excluding synthesis.",
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0),
)

ANSWER_COST = Counter(
    "lexground_answer_cost_usd_total",
    "Cumulative provider spend attributable to answer synthesis.",
)

EVAL_METRIC = Gauge(
    "lexground_eval_metric",
    "Latest value of each evaluation metric.",
    ["metric"],
)

INDEXED_CHUNKS = Gauge(
    "lexground_indexed_chunks",
    "Chunks currently in the retrieval index.",
)
