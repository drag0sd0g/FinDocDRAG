"""Prometheus metrics for the Ingestion Service.

Implements all metrics from TDD Section 8.1.1:
  - findoc_ingestion_filings_fetched_total  (Counter)
  - findoc_ingestion_edgar_request_duration (Histogram)
  - findoc_ingestion_kafka_publish_total    (Counter)

References:
  - TDD: Section 8.1.1 (Ingestion Service Metrics)
"""

from prometheus_client import Counter, Histogram

# ── Counters ─────────────────────────────────────────────────────

FILINGS_FETCHED_TOTAL = Counter(
    "findoc_ingestion_filings_fetched_total",
    "Total filings fetched (success/error/skipped)",
    ["ticker", "status"],
)

KAFKA_PUBLISH_TOTAL = Counter(
    "findoc_ingestion_kafka_publish_total",
    "Messages published to Kafka (success/error)",
    ["topic", "status"],
)

# ── Histograms ───────────────────────────────────────────────────

EDGAR_REQUEST_DURATION = Histogram(
    "findoc_ingestion_edgar_request_duration",
    "SEC EDGAR API request latency in seconds",
    ["ticker"],
    buckets=(0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0),
)

FILING_SIZE_BYTES = Histogram(
    "findoc_ingestion_filing_size_bytes",
    "Raw filing JSON payload size in bytes before Kafka publish",
    buckets=(
        100_000,       # 100 KB
        500_000,       # 500 KB
        1_000_000,     # 1 MB
        5_000_000,     # 5 MB
        10_000_000,    # 10 MB
        50_000_000,    # 50 MB
        100_000_000,   # 100 MB
        157_286_400,   # 150 MB (hard limit)
    ),
)
