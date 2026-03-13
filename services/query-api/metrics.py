"""Prometheus metrics for the Query API.

Implements all metrics from TDD Section 8.1.3:
  - findoc_query_requests_total           (Counter)
  - findoc_query_embedding_duration       (Histogram)
  - findoc_query_retrieval_duration       (Histogram)
  - findoc_query_llm_duration             (Histogram)
  - findoc_query_total_duration           (Histogram)
  - findoc_query_retrieval_score          (Histogram)
  - findoc_query_llm_tokens_used          (Counter)
  - findoc_query_degraded_responses_total (Counter)

References:
  - TDD: Section 8.1.3 (Query API Metrics)
"""

from prometheus_client import Counter, Histogram

# ── Counters ─────────────────────────────────────────────────────

REQUESTS_TOTAL = Counter(
    "findoc_query_requests_total",
    "Total API requests by endpoint and HTTP status",
    ["endpoint", "status"],
)

LLM_TOKENS_USED = Counter(
    "findoc_query_llm_tokens_used",
    "Tokens consumed (prompt/completion)",
    ["backend", "type"],
)

DEGRADED_RESPONSES_TOTAL = Counter(
    "findoc_query_degraded_responses_total",
    "Responses returned in degraded mode",
    ["reason"],
)

# ── Histograms ───────────────────────────────────────────────────

EMBEDDING_DURATION = Histogram(
    "findoc_query_embedding_duration",
    "Time to embed the user's query in seconds",
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0),
)

RETRIEVAL_DURATION = Histogram(
    "findoc_query_retrieval_duration",
    "Time for pgvector similarity search in seconds",
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0),
)

LLM_DURATION = Histogram(
    "findoc_query_llm_duration",
    "Time for LLM generation in seconds",
    ["backend"],
    buckets=(0.5, 1.0, 2.5, 5.0, 10.0, 15.0, 20.0, 30.0),
)

TOTAL_DURATION = Histogram(
    "findoc_query_total_duration",
    "Total end-to-end query latency in seconds",
    buckets=(0.5, 1.0, 2.5, 5.0, 10.0, 15.0, 20.0, 30.0, 60.0),
)

RETRIEVAL_SCORE = Histogram(
    "findoc_query_retrieval_score",
    "Distribution of top-1 relevance scores",
    buckets=(0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 0.95, 1.0),
)
