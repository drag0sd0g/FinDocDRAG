"""Embedding drift detector — weekly CronJob (TDD Section 8.4).

Computes the corpus-wide mean embedding vector and the mean for chunks
ingested in the past DRIFT_LOOKBACK_DAYS days, then measures cosine
distance between them.  A large distance indicates that recent filings
are embedding into a different region of the vector space (model drift,
document distribution shift, or preprocessing changes).

Metrics emitted:
  findoc_embedding_drift_score  Gauge — cosine distance [0, 1]
  findoc_embedding_drift_alert  Gauge — 1 if score > threshold, else 0

When PUSHGATEWAY_URL is set the metrics are pushed to the Prometheus
Pushgateway.  Regardless, the result is always logged as structured JSON
so the CronJob output is visible in ``kubectl logs``.

Environment variables:
  POSTGRES_USER / POSTGRES_PASSWORD / POSTGRES_HOST / POSTGRES_PORT / POSTGRES_DB
    Standard PostgreSQL connection settings.
  PUSHGATEWAY_URL
    Optional Prometheus Pushgateway URL (e.g. ``http://pushgateway:9091``).
    When absent, only structured logs are emitted.
  DRIFT_THRESHOLD
    Float, default 0.15 — cosine distance that triggers the alert metric.
  DRIFT_LOOKBACK_DAYS
    Integer, default 7 — window size for the "recent" mean in days.
"""

from __future__ import annotations

import logging
import os
import sys

import numpy as np
import psycopg2
import structlog
from pgvector.psycopg2 import register_vector
from prometheus_client import CollectorRegistry, Gauge, push_to_gateway

# ── Configuration ─────────────────────────────────────────────────

POSTGRES_DSN = (
    f"postgresql://{os.getenv('POSTGRES_USER', 'findocrag')}"
    f":{os.getenv('POSTGRES_PASSWORD', 'changeme')}"
    f"@{os.getenv('POSTGRES_HOST', 'postgres')}"
    f":{os.getenv('POSTGRES_PORT', '5432')}"
    f"/{os.getenv('POSTGRES_DB', 'findocrag')}"
)
PUSHGATEWAY_URL: str | None = os.getenv("PUSHGATEWAY_URL")
DRIFT_THRESHOLD = float(os.getenv("DRIFT_THRESHOLD", "0.15"))
DRIFT_LOOKBACK_DAYS = int(os.getenv("DRIFT_LOOKBACK_DAYS", "7"))
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")

# ── Structured logging ────────────────────────────────────────────

SERVICE_NAME = "drift-detector"


def _add_service_field(_logger: object, _method_name: str, event_dict: dict) -> dict:
    event_dict["service"] = SERVICE_NAME
    return event_dict


structlog.configure(
    processors=[
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        _add_service_field,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.JSONRenderer(),
    ],
    wrapper_class=structlog.make_filtering_bound_logger(
        logging.getLevelName(LOG_LEVEL),
    ),
)

logger = structlog.get_logger()


# ── Math helper ────────────────────────────────────────────────────

def _cosine_distance(a: np.ndarray, b: np.ndarray) -> float:
    """Return cosine distance in [0, 1] between two vectors."""
    norm_a = float(np.linalg.norm(a))
    norm_b = float(np.linalg.norm(b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 1.0
    return float(1.0 - np.dot(a, b) / (norm_a * norm_b))


# ── Main ──────────────────────────────────────────────────────────

def main() -> None:
    logger.info("drift_detection_starting", lookback_days=DRIFT_LOOKBACK_DAYS)

    # ── Connect to PostgreSQL ─────────────────────────────────────
    try:
        conn = psycopg2.connect(POSTGRES_DSN)
        conn.autocommit = True
        register_vector(conn)
    except Exception as exc:
        logger.error("db_connect_failed", error=str(exc))
        sys.exit(1)

    # ── Query corpus mean and recent mean ─────────────────────────
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT avg(embedding) FROM document_chunks")
            row = cur.fetchone()
            corpus_mean: np.ndarray | None = row[0] if row else None

        with conn.cursor() as cur:
            cur.execute(
                "SELECT avg(embedding) FROM document_chunks "
                "WHERE created_at >= NOW() - INTERVAL '%s days'",
                (DRIFT_LOOKBACK_DAYS,),
            )
            row = cur.fetchone()
            recent_mean: np.ndarray | None = row[0] if row else None
    except Exception as exc:
        logger.error("db_query_failed", error=str(exc))
        conn.close()
        sys.exit(1)
    finally:
        conn.close()

    # ── Handle missing data ────────────────────────────────────────
    if corpus_mean is None:
        logger.warning(
            "drift_detection_skipped",
            reason="no_chunks_in_corpus",
        )
        return

    if recent_mean is None:
        logger.warning(
            "drift_detection_skipped",
            reason="no_chunks_in_lookback_window",
            lookback_days=DRIFT_LOOKBACK_DAYS,
        )
        return

    # ── Compute drift score ────────────────────────────────────────
    drift_score = _cosine_distance(corpus_mean, recent_mean)
    is_alert = drift_score > DRIFT_THRESHOLD

    logger.info(
        "drift_detection_complete",
        drift_score=round(drift_score, 6),
        threshold=DRIFT_THRESHOLD,
        alert=is_alert,
        lookback_days=DRIFT_LOOKBACK_DAYS,
    )
    if is_alert:
        logger.warning(
            "embedding_drift_alert",
            drift_score=round(drift_score, 6),
            threshold=DRIFT_THRESHOLD,
        )

    # ── Emit Prometheus metrics ────────────────────────────────────
    registry = CollectorRegistry()
    drift_score_gauge = Gauge(
        "findoc_embedding_drift_score",
        "Cosine distance between recent and corpus-wide mean embeddings",
        registry=registry,
    )
    drift_alert_gauge = Gauge(
        "findoc_embedding_drift_alert",
        "1 if embedding drift exceeds the configured threshold, 0 otherwise",
        registry=registry,
    )
    drift_score_gauge.set(drift_score)
    drift_alert_gauge.set(1.0 if is_alert else 0.0)

    if PUSHGATEWAY_URL:
        try:
            push_to_gateway(
                PUSHGATEWAY_URL,
                job="findoc-embedding-drift-detection",
                registry=registry,
            )
            logger.info("metrics_pushed", pushgateway=PUSHGATEWAY_URL)
        except Exception as exc:
            # Pushgateway push failure must not fail the job — the result
            # is already captured in the structured log above.
            logger.warning("pushgateway_push_failed", error=str(exc))
    else:
        logger.info(
            "pushgateway_not_configured",
            hint="Set PUSHGATEWAY_URL to persist metrics in Prometheus",
        )


if __name__ == "__main__":
    main()
