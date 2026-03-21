"""Embedding Worker — Kafka consumer + FastAPI health sidecar.

Consumes raw filings from filings.raw, chunks, embeds, stores in
pgvector, and publishes chunk IDs to filings.embedded.

References:
  - TDD: FR-6 through FR-11
  - TDD: Section 5.2.2
  - TDD: Section 7.2 (idempotency), 7.3 (DLQ)
  - TDD: Section 8.1.2 (Embedding Worker Metrics)
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
import uuid
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from logging import LogRecord
from typing import Any

import structlog
import uvicorn
from confluent_kafka import Consumer, KafkaError, Producer
from fastapi import FastAPI, HTTPException, Request, Response
from prometheus_client import make_asgi_app

from src.chunker import Chunk, chunk_filing
from src.embedder import Embedder
from src.metrics import (
    BATCH_DURATION,
    CHUNK_TOKENS,
    CHUNKS_PROCESSED_TOTAL,
    DLQ_MESSAGES_TOTAL,
    KAFKA_LAG,
)
from src.store import ChunkStore

# ── Configuration ────────────────────────────────────────────────

KAFKA_BOOTSTRAP = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "kafka:9092")
POSTGRES_DSN = (
    f"postgresql://{os.getenv('POSTGRES_USER', 'findocrag')}"
    f":{os.getenv('POSTGRES_PASSWORD', 'changeme')}"
    f"@{os.getenv('POSTGRES_HOST', 'postgres')}"
    f":{os.getenv('POSTGRES_PORT', '5432')}"
    f"/{os.getenv('POSTGRES_DB', 'findocrag')}"
)
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "sentence-transformers/all-MiniLM-L6-v2")
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")

TOPIC_RAW = "filings.raw"
TOPIC_EMBEDDED = "filings.embedded"
TOPIC_DLQ = "filings.dlq"

MAX_RETRIES = 3

# Kafka consumer timeouts — embedding a large 10-K on CPU takes several minutes.
_KAFKA_MAX_POLL_INTERVAL_MS = 1_800_000  # 30 minutes
_KAFKA_SESSION_TIMEOUT_MS = 60_000       # 60 seconds

# ── Structured logging ───────────────────────────────────────────

structlog.configure(
    processors=[
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.JSONRenderer(),
    ],
    wrapper_class=structlog.make_filtering_bound_logger(
        logging.getLevelName(LOG_LEVEL),
    ),
)

logger = structlog.get_logger()


# ── Suppress noisy health/metrics access log lines ───────────────

class _SuppressHealthMetrics(logging.Filter):
    """Drop uvicorn access-log records for /health and /metrics."""

    def filter(self, record: LogRecord) -> bool:
        msg = record.getMessage()
        return "/health" not in msg and "/metrics" not in msg


logging.getLogger("uvicorn.access").addFilter(_SuppressHealthMetrics())


# ── Shared state ─────────────────────────────────────────────────

_embedder: Embedder | None = None
_store: ChunkStore | None = None
_consumer_thread: threading.Thread | None = None
_shutdown_event = threading.Event()


# ── DLQ helper ───────────────────────────────────────────────────

def _publish_to_dlq(
    producer: Producer,
    msg_key: bytes | None,
    error: str,
    payload: dict[str, Any] | None = None,
    retry_count: int = 0,
) -> None:
    """Publish a failed message to the dead-letter queue topic (ADR-010)."""
    dlq_msg = {
        "original_message": payload or {},
        "error": error,
        "failed_at": datetime.now(UTC).isoformat(),
        "retry_count": retry_count,
    }
    try:
        producer.produce(
            topic=TOPIC_DLQ,
            key=msg_key,
            value=json.dumps(dlq_msg),
        )
        producer.poll(0)
        DLQ_MESSAGES_TOTAL.inc()
    except Exception as dlq_exc:
        logger.error("dlq_publish_failed", error=str(dlq_exc))


# ── Kafka consumer loop (runs in a background thread) ────────────

def _consume_loop() -> None:
    """Poll filings.raw, process each message, commit offset."""
    global _embedder, _store  # noqa: PLW0603

    consumer = Consumer(
        {
            "bootstrap.servers": KAFKA_BOOTSTRAP,
            "group.id": "embedding-worker",
            "auto.offset.reset": "earliest",
            "enable.auto.commit": False,
            # Chunking + embedding a large 10-K on CPU can take several minutes.
            "max.poll.interval.ms": _KAFKA_MAX_POLL_INTERVAL_MS,
            "session.timeout.ms": _KAFKA_SESSION_TIMEOUT_MS,
        }
    )
    producer = Producer({"bootstrap.servers": KAFKA_BOOTSTRAP})

    consumer.subscribe([TOPIC_RAW])
    logger.info("consumer_started", topic=TOPIC_RAW)

    while not _shutdown_event.is_set():
        msg = consumer.poll(timeout=1.0)
        if msg is None:
            continue
        if msg.error():
            if msg.error().code() == KafkaError._PARTITION_EOF:
                continue
            logger.error("consumer_error", error=str(msg.error()))
            continue

        # ── Step 1: Parse message ─────────────────────────────────────
        # Malformed messages cannot be retried — route straight to DLQ.
        payload: dict[str, Any] | None = None
        try:
            payload = json.loads(msg.value().decode("utf-8"))
            accession = payload["accession_number"]
            ticker = payload["ticker"]
            filing_date = payload["filing_date"]
            raw_text = payload["raw_text"]
        except Exception as parse_exc:
            logger.error("message_parse_failed", error=str(parse_exc))
            _publish_to_dlq(producer, msg.key(), str(parse_exc))
            consumer.commit(msg)
            continue

        logger.info("processing_filing", accession=accession, ticker=ticker)

        # ── Step 2: Process with retries (ADR-010) ────────────────────
        # Attempt the full pipeline up to MAX_RETRIES + 1 times, sleeping
        # 2^(attempt+1) seconds between failures: 2 s, 4 s, 8 s → ~14 s max.
        last_exc: Exception | None = None
        chunks: list[Chunk] = []

        for attempt in range(MAX_RETRIES + 1):
            try:
                chunks = chunk_filing(
                    raw_text=raw_text,
                    accession_number=accession,
                    ticker=ticker,
                    filing_date=filing_date,
                )

                if not chunks:
                    logger.warning("no_chunks_produced", accession=accession)
                    break

                # Record token distribution (TDD Section 8.1.2)
                for c in chunks:
                    CHUNK_TOKENS.observe(c.token_count)

                # Embed (with batch duration timing)
                assert _embedder is not None
                texts = [c.text for c in chunks]
                t0 = time.perf_counter()
                embeddings = _embedder.embed(texts)
                embed_elapsed = time.perf_counter() - t0
                BATCH_DURATION.observe(embed_elapsed)

                # Store
                assert _store is not None
                _store.store_chunks(chunks, embeddings)
                _store.update_ingestion_status(accession, len(chunks))

                CHUNKS_PROCESSED_TOTAL.labels(ticker=ticker, status="success").inc(len(chunks))

                # Publish confirmation to filings.embedded (FR-11)
                confirmation = {
                    "accession_number": accession,
                    "chunk_ids": [c.chunk_id for c in chunks],
                    "chunk_count": len(chunks),
                    "processed_at": datetime.now(UTC).isoformat(),
                }
                producer.produce(
                    topic=TOPIC_EMBEDDED,
                    key=accession,
                    value=json.dumps(confirmation),
                )
                producer.poll(0)

                last_exc = None
                break  # success

            except Exception as exc:
                last_exc = exc
                if attempt < MAX_RETRIES:
                    sleep_s = 2 ** (attempt + 1)  # 2 s, 4 s, 8 s
                    logger.warning(
                        "processing_attempt_failed",
                        attempt=attempt + 1,
                        max_retries=MAX_RETRIES,
                        sleep_s=sleep_s,
                        accession=accession,
                        error=str(exc),
                    )
                    time.sleep(sleep_s)
                else:
                    logger.error(
                        "processing_failed_all_retries",
                        accession=accession,
                        ticker=ticker,
                        error=str(exc),
                    )

        # ── Step 3: DLQ if all retries exhausted (ADR-010, TDD 7.3) ──
        if last_exc is not None:
            CHUNKS_PROCESSED_TOTAL.labels(ticker=ticker, status="error").inc()
            _publish_to_dlq(
                producer, msg.key(), str(last_exc),
                payload=payload, retry_count=MAX_RETRIES,
            )

        # ── Step 4: Commit offset (always — success or DLQ) ──────────
        # TDD Section 7.2: only commit after the message is fully handled.
        consumer.commit(msg)

        if last_exc is None and chunks:
            # Update Kafka lag gauge (best-effort, success path only)
            try:
                partitions = consumer.assignment()
                for tp in partitions:
                    (lo, hi) = consumer.get_watermark_offsets(tp, cached=True)
                    committed = consumer.committed([tp])[0].offset
                    if committed >= 0 and hi >= 0:
                        KAFKA_LAG.labels(partition=str(tp.partition)).set(
                            max(0, hi - committed)
                        )
            except Exception:
                pass  # lag reporting is best-effort

            logger.info(
                "filing_processed",
                accession=accession,
                ticker=ticker,
                chunks=len(chunks),
            )

    consumer.close()
    producer.flush()
    logger.info("consumer_stopped")


# ── FastAPI health sidecar ───────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Start the Kafka consumer thread and initialise dependencies."""
    global _embedder, _store, _consumer_thread  # noqa: PLW0603

    logger.info("embedding_worker_starting")

    _embedder = Embedder(model_name=EMBEDDING_MODEL)
    _store = ChunkStore(dsn=POSTGRES_DSN)
    _store.connect()

    _consumer_thread = threading.Thread(target=_consume_loop, daemon=True)
    _consumer_thread.start()

    logger.info("embedding_worker_started")

    yield

    _shutdown_event.set()
    if _consumer_thread is not None:
        _consumer_thread.join(timeout=10)
    if _store is not None:
        _store.close()
    logger.info("embedding_worker_stopped")


app = FastAPI(
    title="FinDoc RAG — Embedding Worker",
    version="0.1.0",
    lifespan=lifespan,
)

# ── Mount Prometheus /metrics endpoint (TDD: Section 8.1) ────────
metrics_app = make_asgi_app()
app.mount("/metrics", metrics_app)


# ── Request-ID middleware (TDD Section 8.3) ───────────────────────

@app.middleware("http")
async def _request_id_middleware(request: Request, call_next: object) -> Response:
    """Attach a request_id to every request for log correlation (TDD 8.3)."""
    request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
    structlog.contextvars.clear_contextvars()
    structlog.contextvars.bind_contextvars(request_id=request_id)
    request.state.request_id = request_id
    response = await call_next(request)  # type: ignore[operator]
    response.headers["X-Request-ID"] = request_id
    return response


@app.get("/health")
async def health() -> dict[str, str]:
    """Liveness probe (FR-21) — healthy when DB reachable and consumer running."""
    if _store is None or _consumer_thread is None:
        raise HTTPException(status_code=503, detail="unhealthy: not initialized")
    if not _consumer_thread.is_alive():
        raise HTTPException(status_code=503, detail="unhealthy: consumer_thread_dead")
    try:
        cur = _store._get_conn().cursor()
        cur.execute("SELECT 1")
        cur.close()
    except Exception as exc:
        raise HTTPException(status_code=503, detail="unhealthy: db_unreachable") from exc
    return {"status": "healthy"}


@app.get("/ready")
async def ready() -> dict[str, str]:
    """Readiness probe (FR-22) — ready when model is loaded and DB connected."""
    if _embedder is None or _store is None:
        raise HTTPException(status_code=503, detail="Service not initialized")
    return {"status": "ready"}


if __name__ == "__main__":
    uvicorn.run("src.main:app", host="0.0.0.0", port=8002, reload=False)
