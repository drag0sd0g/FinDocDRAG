"""Ingestion Service — FastAPI application.

Endpoints:
  GET  /health       Liveness probe  (TDD: FR-21)
  GET  /ready        Readiness probe (TDD: FR-22)
  GET  /metrics      Prometheus metrics (TDD: Section 8.1.1)
  POST /v1/ingest    Trigger filing ingestion (TDD: Section 5.2.1)

References:
  - TDD: FR-1 through FR-5
  - TDD: Section 5.2.1 (Ingestion Service description)
  - TDD: Section 8.1.1 (Ingestion Service Metrics)
"""

from __future__ import annotations

import logging
import time
import uuid
from contextlib import asynccontextmanager
from logging import LogRecord
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Awaitable, Callable

import aiohttp
import structlog
import uvicorn
from fastapi import FastAPI, HTTPException, Request, Response
from prometheus_client import make_asgi_app
from pydantic import BaseModel

from src.config import load_tickers, settings
from src.db import IngestionDB
from src.edgar_client import EdgarClient
from src.kafka_producer import FilingProducer
from src.metrics import FILINGS_FETCHED_TOTAL

# ── Structured logging (TDD: Section 8.3) ───────────────────────

SERVICE_NAME = "ingestion"


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
        logging.getLevelName(settings.log_level),
    ),
)

logger = structlog.get_logger()


# ── Suppress noisy health/metrics access log lines ───────────────

class _SuppressHealthMetrics(logging.Filter):
    def filter(self, record: LogRecord) -> bool:
        msg = record.getMessage()
        return "/health" not in msg and "/metrics" not in msg


logging.getLogger("uvicorn.access").addFilter(_SuppressHealthMetrics())


# ── Request / Response models ────────────────────────────────────

class IngestRequest(BaseModel):
    """Request body for POST /v1/ingest."""

    tickers: list[str] | None = None


class IngestResponse(BaseModel):
    """Response body for POST /v1/ingest."""

    status: str
    tickers_processed: list[str]
    filings_published: int
    filings_skipped: int
    errors: list[str]


# ── Application state ────────────────────────────────────────────

_edgar_client: EdgarClient | None = None
_kafka_producer: FilingProducer | None = None
_db: IngestionDB | None = None


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Startup / shutdown lifecycle."""
    global _edgar_client, _kafka_producer, _db  # noqa: PLW0603

    logger.info("ingestion_service_starting")

    _edgar_client = EdgarClient(
        user_agent=settings.edgar_user_agent,
        rate_limit_rps=settings.edgar_rate_limit_rps,
    )
    _kafka_producer = FilingProducer()
    _db = IngestionDB(dsn=settings.postgres_dsn)
    _db.connect()

    logger.info("ingestion_service_started")

    yield  # ← application runs here

    if _kafka_producer is not None:
        _kafka_producer.flush()
    if _db is not None:
        _db.close()
    logger.info("ingestion_service_stopped")


app = FastAPI(
    title="FinDoc RAG — Ingestion Service",
    version="0.1.0",
    lifespan=lifespan,
)

# ── Mount Prometheus /metrics endpoint (TDD: Section 8.1) ────────
metrics_app = make_asgi_app()
app.mount("/metrics", metrics_app)


# ── Request-ID middleware (TDD Section 8.3) ───────────────────────

@app.middleware("http")
async def _request_id_middleware(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    """Attach a request_id to every request for log correlation (TDD 8.3)."""
    request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
    structlog.contextvars.clear_contextvars()
    structlog.contextvars.bind_contextvars(request_id=request_id)
    request.state.request_id = request_id
    response = await call_next(request)
    response.headers["X-Request-ID"] = request_id
    return response


# ── Health & Readiness (FR-21, FR-22) ────────────────────────────

@app.get("/health")
async def health() -> dict[str, str]:
    """Liveness probe (FR-21) — healthy when DB is reachable."""
    if _db is None:
        raise HTTPException(status_code=503, detail="unhealthy: not initialized")
    try:
        cur = _db._get_conn().cursor()
        cur.execute("SELECT 1")
        cur.close()
    except Exception as exc:
        raise HTTPException(status_code=503, detail="unhealthy: db_unreachable") from exc
    return {"status": "healthy"}


@app.get("/ready")
async def ready() -> dict[str, str]:
    """Readiness probe — ready only when dependencies are initialised."""
    if _edgar_client is None or _kafka_producer is None or _db is None:
        raise HTTPException(status_code=503, detail="Service not initialized")
    return {"status": "ready"}


# ── Ingestion endpoint (TDD Section 5.2.1) ──────────────────────

@app.post("/v1/ingest", response_model=IngestResponse)
async def ingest(body: IngestRequest | None = None) -> IngestResponse:
    """Trigger ingestion of 10-K filings.

    If ``tickers`` is provided in the request body, ingest those.
    Otherwise fall back to ``config/tickers.yml`` (FR-2).
    """
    if _edgar_client is None or _kafka_producer is None or _db is None:
        raise HTTPException(status_code=503, detail="Service not initialized")

    # Determine which tickers to process
    if body is not None and body.tickers:
        ticker_list: list[dict[str, Any]] = [
            {"symbol": t, "name": t} for t in body.tickers
        ]
    else:
        ticker_list = load_tickers(settings.tickers_config_path)

    if not ticker_list:
        raise HTTPException(status_code=400, detail="No tickers to ingest")

    filings_published = 0
    filings_skipped = 0
    errors: list[str] = []
    tickers_processed: list[str] = []

    t_request = time.perf_counter()

    async with aiohttp.ClientSession() as session:
        for entry in ticker_list:
            symbol = entry["symbol"]
            name = entry.get("name", symbol)
            tickers_processed.append(symbol)
            t_ticker = time.perf_counter()

            logger.info("ingest_ticker_started", ticker=symbol)

            try:
                filings = await _edgar_client.get_filings_for_ticker(
                    ticker=symbol,
                    company_name=name,
                    session=session,
                )

                for filing in filings:
                    # Deduplication check (FR-4): skip if already ingested.
                    if _db.is_already_ingested(filing.accession_number):
                        filings_skipped += 1
                        FILINGS_FETCHED_TOTAL.labels(ticker=symbol, status="skipped").inc()
                        logger.info(
                            "filing_skipped_duplicate",
                            accession=filing.accession_number,
                            ticker=symbol,
                        )
                        continue

                    logger.info(
                        "filing_publishing",
                        ticker=symbol,
                        accession=filing.accession_number,
                        filing_date=filing.filing_date,
                    )
                    t_publish = time.perf_counter()
                    published = _kafka_producer.publish_filing(filing)
                    if not published:
                        filings_skipped += 1
                        FILINGS_FETCHED_TOTAL.labels(ticker=symbol, status="skipped").inc()
                        continue
                    _db.record_ingestion(filing)
                    logger.info(
                        "filing_published",
                        ticker=symbol,
                        accession=filing.accession_number,
                        elapsed_ms=round((time.perf_counter() - t_publish) * 1000, 1),
                    )
                    filings_published += 1
                    FILINGS_FETCHED_TOTAL.labels(ticker=symbol, status="success").inc()

                logger.info(
                    "ingest_ticker_complete",
                    ticker=symbol,
                    filings_published=filings_published,
                    filings_skipped=filings_skipped,
                    elapsed_ms=round((time.perf_counter() - t_ticker) * 1000, 1),
                )

            except Exception as exc:
                error_msg = f"Error processing {symbol}: {exc}"
                errors.append(error_msg)
                FILINGS_FETCHED_TOTAL.labels(ticker=symbol, status="error").inc()
                logger.error(
                    "ingest_ticker_error",
                    ticker=symbol,
                    error=str(exc),
                    elapsed_ms=round((time.perf_counter() - t_ticker) * 1000, 1),
                )

    _kafka_producer.flush()

    logger.info(
        "ingest_request_complete",
        tickers=tickers_processed,
        filings_published=filings_published,
        filings_skipped=filings_skipped,
        errors=len(errors),
        elapsed_ms=round((time.perf_counter() - t_request) * 1000, 1),
    )

    return IngestResponse(
        status="completed",
        tickers_processed=tickers_processed,
        filings_published=filings_published,
        filings_skipped=filings_skipped,
        errors=errors,
    )


# ── Entrypoint ───────────────────────────────────────────────────

if __name__ == "__main__":
    uvicorn.run("src.main:app", host="0.0.0.0", port=8001, reload=True)
