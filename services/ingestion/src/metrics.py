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
