"""Query API — FastAPI application.

Endpoints:
  GET  /health         Liveness probe  (FR-21)
  GET  /ready          Readiness probe (FR-22)
  GET  /metrics        Prometheus metrics (TDD: Section 8.1.3)
  POST /v1/query       RAG query       (FR-12 through FR-18)
  GET  /v1/documents   List filings    (FR-19, FR-20)

References:
  - TDD: Section 5.2.3
  - TDD: Section 8.1.3 (Query API Metrics)
  - TDD: Section 9.1 (API key auth)
  - TDD: Section 9.3 (rate limiting)
"""

import logging
import os
import uuid
from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import asynccontextmanager
from logging import LogRecord
from typing import Any

import structlog
import uvicorn
from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from prometheus_client import make_asgi_app
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from slowapi.util import get_remote_address

from src.auth import verify_api_key
from src.llm.anthropic_backend import AnthropicBackend
from src.llm.ollama_backend import OllamaBackend
from src.llm.openai_backend import OpenAIBackend
from src.metrics import DEGRADED_RESPONSES_TOTAL, REQUESTS_TOTAL
from src.models import (
    DocumentInfo,
    DocumentListResponse,
    QueryRequest,
    QueryResponse,
)
from src.rag.generator import RAGGenerator
from src.rag.retriever import Retriever

# ── Configuration ────────────────────────────────────────────────

POSTGRES_DSN = (
    f"postgresql://{os.getenv('POSTGRES_USER', 'findocdrag')}"
    f":{os.getenv('POSTGRES_PASSWORD', 'changeme')}"
    f"@{os.getenv('POSTGRES_HOST', 'postgres')}"
    f":{os.getenv('POSTGRES_PORT', '5432')}"
    f"/{os.getenv('POSTGRES_DB', 'findocdrag')}"
)
LLM_BACKEND = os.getenv("LLM_BACKEND", "ollama")
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://ollama:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "mistral:7b")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
CLAUDE_MODEL = os.getenv("CLAUDE_MODEL", "claude-opus-4-6")
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "sentence-transformers/all-MiniLM-L6-v2")
RATE_LIMIT = os.getenv("RATE_LIMIT", "30/minute")
QUERY_API_PORT = int(os.getenv("QUERY_API_PORT", "8000"))
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
# Comma-separated list of allowed CORS origins.
# Defaults to "*" (allow all) for local development.
# Restrict to specific origins in production (e.g. "https://app.example.com").
CORS_ORIGINS = [o.strip() for o in os.getenv("CORS_ORIGINS", "*").split(",") if o.strip()]

# ── Structured logging ───────────────────────────────────────────

SERVICE_NAME = "query-api"


def _add_service_field(_logger: Any, _method_name: str, event_dict: Any) -> Any:
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


# ── Suppress noisy health/metrics access log lines ───────────────

class _SuppressHealthMetrics(logging.Filter):
    def filter(self, record: LogRecord) -> bool:
        msg = record.getMessage()
        return "/health" not in msg and "/metrics" not in msg


logging.getLogger("uvicorn.access").addFilter(_SuppressHealthMetrics())


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
    _retriever.verify_embedding_model_consistency()

    # LLM backend (FR-18) — select via LLM_BACKEND env var
    if LLM_BACKEND == "openai":
        llm = OpenAIBackend(model=OPENAI_MODEL)  # type: ignore[assignment]
    elif LLM_BACKEND == "claude":
        llm = AnthropicBackend(model=CLAUDE_MODEL)  # type: ignore[assignment]
    else:
        llm = OllamaBackend(base_url=OLLAMA_URL, model=OLLAMA_MODEL)  # type: ignore[assignment]

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
app.add_middleware(SlowAPIMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST"],
    allow_headers=["X-API-Key", "Content-Type", "X-Request-ID"],
)


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

# ── Mount Prometheus /metrics endpoint (TDD: Section 8.1) ────────
metrics_app = make_asgi_app()
app.mount("/metrics", metrics_app)


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
    """Liveness probe (FR-21) — healthy when DB is reachable."""
    if _retriever is None:
        raise HTTPException(status_code=503, detail="unhealthy: not initialized")
    try:
        cur = _retriever._get_conn().cursor()
        cur.execute("SELECT 1")
        cur.close()
    except Exception as exc:
        raise HTTPException(status_code=503, detail="unhealthy: db_unreachable") from exc
    return {"status": "healthy"}


@app.get("/ready")
async def ready() -> dict[str, str]:
    if _retriever is None or _generator is None:
        raise HTTPException(status_code=503, detail="Service not initialized")
    return {"status": "ready"}


# ── POST /v1/query (FR-12 through FR-18) ────────────────────────

@app.post("/v1/query", response_model=QueryResponse)
@limiter.limit(RATE_LIMIT)
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

    # Record Prometheus metrics (TDD Section 8.1.3)
    status = "degraded" if result.degraded else "success"
    REQUESTS_TOTAL.labels(endpoint="/v1/query", status=status).inc()

    if result.degraded:
        DEGRADED_RESPONSES_TOTAL.labels(reason="llm_unavailable").inc()

    logger.info(
        "query_completed",
        sources=len(result.sources),
        degraded=result.degraded,
        total_ms=result.timing.total_ms,
    )

    return result.model_copy(update={"request_id": request.state.request_id})


# ── GET /v1/documents (FR-19, FR-20) ────────────────────────────

@app.get("/v1/documents", response_model=DocumentListResponse)
@limiter.limit(RATE_LIMIT)
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

    REQUESTS_TOTAL.labels(endpoint="/v1/documents", status="success").inc()

    rows, total = _retriever.list_documents(ticker=ticker, limit=limit, offset=offset)

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
    uvicorn.run("src.main:app", host="0.0.0.0", port=QUERY_API_PORT, reload=True)
