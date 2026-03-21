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

# ── Configuration ────────────────────────────────────────────────
import os
import threading
import time
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import structlog
import uvicorn
from confluent_kafka import Consumer, KafkaError, Producer
from fastapi import FastAPI, HTTPException
from prometheus_client import make_asgi_app

from src.chunker import chunk_filing
from src.embedder import Embedder
from src.metrics import (
    BATCH_DURATION,
    CHUNK_TOKENS,
    CHUNKS_PROCESSED_TOTAL,
    DLQ_MESSAGES_TOTAL,
    KAFKA_LAG,
)
from src.store import ChunkStore

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

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

# ── Structured logging ───────────────────────────────────────────

structlog.configure(
    processors=[
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.JSONRenderer(),
    ],
    wrapper_class=structlog.make_filtering_bound_logger(
        logging.getLevelName(LOG_LEVEL),
    ),
)

logger = structlog.get_logger()

# ── Shared state ─────────────────────────────────────────────────

_embedder: Embedder | None = None
_store: ChunkStore | None = None
_consumer_thread: threading.Thread | None = None
_shutdown_event = threading.Event()


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

        try:
            payload: dict[str, Any] = json.loads(msg.value().decode("utf-8"))
            accession = payload["accession_number"]
            ticker = payload["ticker"]
            filing_date = payload["filing_date"]
            raw_text = payload["raw_text"]

            logger.info(
                "processing_filing",
                accession=accession,
                ticker=ticker,
            )

            # Chunk
            chunks = chunk_filing(
                raw_text=raw_text,
                accession_number=accession,
                ticker=ticker,
                filing_date=filing_date,
            )

            if not chunks:
                logger.warning("no_chunks_produced", accession=accession)
                consumer.commit(msg)
                continue

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

            # Record successful chunk processing
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

            # Manual offset commit (TDD Section 7.2)
            consumer.commit(msg)

            # Update Kafka lag gauge
            try:
                partitions = consumer.assignment()
                for tp in partitions:
                    (lo, hi) = consumer.get_watermark_offsets(tp, cached=True)
                    committed = consumer.committed([tp])[0].offset
                    if committed >= 0 and hi >= 0:
                        KAFKA_LAG.labels(partition=str(tp.partition)).set(hi - committed)
            except Exception:
                pass  # lag reporting is best-effort

            logger.info(
                "filing_processed",
                accession=accession,
                ticker=ticker,
                chunks=len(chunks),
            )

        except Exception as exc:
            logger.error(
                "processing_failed",
                error=str(exc),
                accession=payload.get("accession_number", "unknown") if 'payload' in dir() else "unknown",
            )

            # Record failed chunk processing
            _ticker = payload.get("ticker", "unknown") if 'payload' in dir() else "unknown"
            CHUNKS_PROCESSED_TOTAL.labels(ticker=_ticker, status="error").inc()

            # Publish to DLQ (TDD Section 7.3)
            try:
                dlq_msg = {
                    "original_message": payload if 'payload' in dir() else {},
                    "error": str(exc),
                    "failed_at": datetime.now(UTC).isoformat(),
                    "retry_count": MAX_RETRIES,
                }
                producer.produce(
                    topic=TOPIC_DLQ,
                    key=msg.key(),
                    value=json.dumps(dlq_msg),
                )
                producer.poll(0)
                DLQ_MESSAGES_TOTAL.inc()
            except Exception as dlq_exc:
                logger.error("dlq_publish_failed", error=str(dlq_exc))

            # Commit offset so we don't reprocess forever
            consumer.commit(msg)

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


@app.get("/health")
async def health() -> dict[str, str]:
    """Liveness probe (FR-21)."""
    return {"status": "healthy"}


@app.get("/ready")
async def ready() -> dict[str, str]:
    """Readiness probe (FR-22) — ready when model is loaded and DB connected."""
    if _embedder is None or _store is None:
        raise HTTPException(status_code=503, detail="Service not initialized")
    return {"status": "ready"}


if __name__ == "__main__":
    uvicorn.run("src.main:app", host="0.0.0.0", port=8002, reload=False)
