"""Prometheus metrics for the Embedding Worker.

Implements all metrics from TDD Section 8.1.2:
  - findoc_embedding_chunks_processed_total (Counter)
  - findoc_embedding_batch_duration         (Histogram)
  - findoc_embedding_chunk_tokens           (Histogram)
  - findoc_embedding_dlq_messages_total     (Counter)
  - findoc_embedding_kafka_lag              (Gauge)

References:
  - TDD: Section 8.1.2 (Embedding Worker Metrics)
"""

from prometheus_client import Counter, Gauge, Histogram

# ── Counters ─────────────────────────────────────────────────────

CHUNKS_PROCESSED_TOTAL = Counter(
    "findoc_embedding_chunks_processed_total",
    "Chunks embedded and stored (success/error)",
    ["ticker", "status"],
)

DLQ_MESSAGES_TOTAL = Counter(
    "findoc_embedding_dlq_messages_total",
    "Messages sent to dead letter queue",
)

# ── Histograms ───────────────────────────────────────────────────

BATCH_DURATION = Histogram(
    "findoc_embedding_batch_duration",
    "Time to embed a batch of chunks in seconds",
    buckets=(0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0),
)

CHUNK_TOKENS = Histogram(
    "findoc_embedding_chunk_tokens",
    "Token count distribution per chunk",
    buckets=(32, 64, 128, 256, 384, 512, 640, 768, 1024),
)

# ── Gauges ───────────────────────────────────────────────────────

KAFKA_LAG = Gauge(
    "findoc_embedding_kafka_lag",
    "Consumer lag per partition",
    ["partition"],
)

DRIFT_SCORE = Gauge(
    "findoc_embedding_drift_score",
    "Cosine distance between recent (7-day) and corpus-wide mean embeddings",
)

DRIFT_ALERT = Gauge(
    "findoc_embedding_drift_alert",
    "1 if embedding drift exceeds the configured threshold, 0 otherwise",
)
