"""Query API — FastAPI application.

Endpoints:
  GET  /health         Liveness probe  (FR-21)
  GET  /ready          Readiness probe (FR-22)
  POST /v1/query       RAG query       (FR-12 through FR-18)
  GET  /v1/documents   List filings    (FR-19, FR-20)

References:
  - TDD: Section 5.2.3
  - TDD: Section 9.1 (API key auth)
  - TDD: Section 9.3 (rate limiting)
"""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

import structlog
import uvicorn
from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from src.auth import verify_api_key
from src.llm.ollama_backend import OllamaBackend
from src.llm.openai_backend import OpenAIBackend
from src.models import (
    DocumentInfo,
    DocumentListResponse,
    QueryRequest,
    QueryResponse,
)
from src.rag.generator import RAGGenerator
from src.rag.retriever import Retriever

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

# ── Configuration ────────────────────────────────────────────────

POSTGRES_DSN = (
    f"postgresql://{os.getenv('POSTGRES_USER', 'findocrag')}"
    f":{os.getenv('POSTGRES_PASSWORD', 'changeme')}"
    f"@{os.getenv('POSTGRES_HOST', 'postgres')}"
    f":{os.getenv('POSTGRES_PORT', '5432')}"
    f"/{os.getenv('POSTGRES_DB', 'findocrag')}"
)
LLM_BACKEND = os.getenv("LLM_BACKEND", "ollama")
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://ollama:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "mistral:7b")
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "sentence-transformers/all-MiniLM-L6-v2")
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")

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

# ── Rate limiting (TDD Section 9.3) ─────────────────────────────


def _get_api_key(request: Request) -> str:
    """Rate-limit key function: use API key if present, else IP."""
    return request.headers.get("X-API-Key", get_remote_address(request))


limiter = Limiter(key_func=_get_api_key)

# ── Application state ────────────────────────────────────────────

_retriever: Retriever | None = None
_generator: RAGGenerator | None = None


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Startup / shutdown lifecycle."""
    global _retriever, _generator  # noqa: PLW0603

    logger.info("query_api_starting", llm_backend=LLM_BACKEND)

    # Retriever (loads embedding model)
    _retriever = Retriever(dsn=POSTGRES_DSN, model_name=EMBEDDING_MODEL)
    _retriever.connect()

    # LLM backend (FR-18)
    llm = OpenAIBackend() if LLM_BACKEND == "openai" else OllamaBackend(base_url=OLLAMA_URL, model=OLLAMA_MODEL)  # type: ignore[assignment]

    _generator = RAGGenerator(retriever=_retriever, llm=llm)

    logger.info("query_api_started")

    yield

    if _retriever is not None:
        _retriever.close()
    logger.info("query_api_stopped")


app = FastAPI(
    title="FinDoc RAG — Query API",
    version="0.1.0",
    lifespan=lifespan,
)
app.state.limiter = limiter


# ── Error handler for rate limiting ──────────────────────────────

@app.exception_handler(RateLimitExceeded)
async def _rate_limit_handler(request: Request, exc: RateLimitExceeded) -> Response:
    return Response(
        content='{"detail": "Rate limit exceeded"}',
        status_code=429,
        media_type="application/json",
    )


# ── Health & Readiness (FR-21, FR-22) ────────────────────────────

@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "healthy"}


@app.get("/ready")
async def ready() -> dict[str, str]:
    if _retriever is None or _generator is None:
        raise HTTPException(status_code=503, detail="Service not initialized")
    return {"status": "ready"}


# ── POST /v1/query (FR-12 through FR-18) ────────────────────────

@app.post("/v1/query", response_model=QueryResponse)
async def query(
    body: QueryRequest,
    request: Request,
    _auth: None = Depends(verify_api_key),
) -> QueryResponse:
    """Accept a natural language question and return a cited answer."""
    if _generator is None:
        raise HTTPException(status_code=503, detail="Service not initialized")

    logger.info(
        "query_received",
        question_length=len(body.question),
        ticker_filter=body.ticker_filter,
        top_k=body.top_k,
    )

    result = await _generator.answer(
        question=body.question,
        top_k=body.top_k,
        ticker_filter=body.ticker_filter,
    )

    logger.info(
        "query_completed",
        sources=len(result.sources),
        degraded=result.degraded,
        total_ms=result.timing.total_ms,
    )

    return result


# ── GET /v1/documents (FR-19, FR-20) ────────────────────────────

@app.get("/v1/documents", response_model=DocumentListResponse)
@limiter.limit("30/minute")
async def list_documents(
    request: Request,
    ticker: str | None = Query(default=None, description="Filter by ticker symbol"),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    _auth: None = Depends(verify_api_key),
) -> DocumentListResponse:
    """List all ingested filings with metadata."""
    if _retriever is None:
        raise HTTPException(status_code=503, detail="Service not initialized")


    conn = _retriever._get_conn()
    cur = conn.cursor()

    try:
        # Count total
        if ticker:
            cur.execute("SELECT COUNT(*) FROM ingestion_log WHERE ticker = %s", (ticker,))
        else:
            cur.execute("SELECT COUNT(*) FROM ingestion_log")
        total = cur.fetchone()[0]

        # Fetch page
        if ticker:
            cur.execute(
                """SELECT accession_number, ticker, company_name, filing_date,
                          filing_type, chunk_count, ingested_at
                   FROM ingestion_log
                   WHERE ticker = %s
                   ORDER BY filing_date DESC
                   LIMIT %s OFFSET %s""",
                (ticker, limit, offset),
            )
        else:
            cur.execute(
                """SELECT accession_number, ticker, company_name, filing_date,
                          filing_type, chunk_count, ingested_at
                   FROM ingestion_log
                   ORDER BY filing_date DESC
                   LIMIT %s OFFSET %s""",
                (limit, offset),
            )

        rows = cur.fetchall()
    finally:
        cur.close()

    documents = [
        DocumentInfo(
            accession_number=row[0],
            ticker=row[1],
            company_name=row[2],
            filing_date=str(row[3]),
            filing_type=row[4],
            chunk_count=row[5],
            ingested_at=str(row[6]),
        )
        for row in rows
    ]

    return DocumentListResponse(
        documents=documents,
        total=total,
        limit=limit,
        offset=offset,
    )


# ── Entrypoint ───────────────────────────────────────────────────

if __name__ == "__main__":
    uvicorn.run("src.main:app", host="0.0.0.0", port=8000, reload=True)
