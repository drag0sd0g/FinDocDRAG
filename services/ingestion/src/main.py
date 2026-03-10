"""Ingestion Service — FastAPI application.

Endpoints:
  GET  /health       Liveness probe  (TDD: FR-21)
  GET  /ready        Readiness probe (TDD: FR-22)
  POST /v1/ingest    Trigger filing ingestion (TDD: Section 5.2.1)

References:
  - TDD: FR-1 through FR-5
  - TDD: Section 5.2.1 (Ingestion Service description)
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import Any, AsyncGenerator

import aiohttp
import structlog
import uvicorn
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from src.config import load_tickers, settings
from src.edgar_client import EdgarClient
from src.kafka_producer import FilingProducer

# ── Structured logging (TDD: Section 8.3) ───────────────────────

structlog.configure(
    processors=[
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.JSONRenderer(),
    ],
    wrapper_class=structlog.make_filtering_bound_logger(
        logging.getLevelName(settings.log_level),
    ),
)

logger = structlog.get_logger()


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


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Startup / shutdown lifecycle."""
    global _edgar_client, _kafka_producer  # noqa: PLW0603

    logger.info("ingestion_service_starting")

    _edgar_client = EdgarClient(
        user_agent=settings.edgar_user_agent,
        rate_limit_rps=settings.edgar_rate_limit_rps,
    )
    _kafka_producer = FilingProducer()

    logger.info("ingestion_service_started")

    yield  # ← application runs here

    if _kafka_producer is not None:
        _kafka_producer.flush()
    logger.info("ingestion_service_stopped")


app = FastAPI(
    title="FinDoc RAG — Ingestion Service",
    version="0.1.0",
    lifespan=lifespan,
)


# ── Health & Readiness (FR-21, FR-22) ────────────────────────────

@app.get("/health")
async def health() -> dict[str, str]:
    """Liveness probe — returns healthy if the process is running."""
    return {"status": "healthy"}


@app.get("/ready")
async def ready() -> dict[str, str]:
    """Readiness probe — ready only when dependencies are initialised."""
    if _edgar_client is None or _kafka_producer is None:
        raise HTTPException(status_code=503, detail="Service not initialized")
    return {"status": "ready"}


# ── Ingestion endpoint (TDD Section 5.2.1) ──────────────────────

@app.post("/v1/ingest", response_model=IngestResponse)
async def ingest(body: IngestRequest | None = None) -> IngestResponse:
    """Trigger ingestion of 10-K filings.

    If ``tickers`` is provided in the request body, ingest those.
    Otherwise fall back to ``config/tickers.yml`` (FR-2).
    """
    if _edgar_client is None or _kafka_producer is None:
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

    async with aiohttp.ClientSession() as session:
        for entry in ticker_list:
            symbol = entry["symbol"]
            name = entry.get("name", symbol)
            tickers_processed.append(symbol)

            try:
                filings = await _edgar_client.get_filings_for_ticker(
                    ticker=symbol,
                    company_name=name,
                    session=session,
                )

                for filing in filings:
                    # TODO: Check ingestion_log for dedup (FR-4).
                    #       Will be wired when DB pool is added.
                    _kafka_producer.publish_filing(filing)
                    filings_published += 1

            except Exception as exc:
                error_msg = f"Error processing {symbol}: {exc}"
                errors.append(error_msg)
                logger.error(
                    "ingest_ticker_error",
                    ticker=symbol,
                    error=str(exc),
                )

    _kafka_producer.flush()

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