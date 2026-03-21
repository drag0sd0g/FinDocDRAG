"""Pydantic request and response models for the Query API.

Matches the API contract in TDD Section 5.2.3 exactly.

References:
  - TDD: FR-12 (POST /v1/query request)
  - TDD: FR-17 (response with answer, sources, timing)
  - TDD: FR-19, FR-20 (GET /v1/documents)
"""

from __future__ import annotations

from pydantic import BaseModel, Field

# ── Query endpoint ───────────────────────────────────────────────

class QueryRequest(BaseModel):
    """POST /v1/query request body (FR-12)."""

    question: str
    ticker_filter: str | None = None
    top_k: int = Field(default=5, ge=1, le=20)


class SourceChunk(BaseModel):
    """A single source chunk returned alongside the answer (FR-17)."""

    chunk_id: str
    ticker: str
    filing_date: str
    section: str
    relevance_score: float
    text_preview: str  # first 200 characters


class TimingInfo(BaseModel):
    """Latency breakdown returned in query responses (FR-17)."""

    embedding_ms: float
    retrieval_ms: float
    generation_ms: float
    total_ms: float


class QueryResponse(BaseModel):
    """POST /v1/query response body (FR-17)."""

    answer: str | None
    sources: list[SourceChunk]
    model: str
    timing: TimingInfo
    degraded: bool = False
    request_id: str = ""


# ── Documents endpoint ───────────────────────────────────────────

class DocumentInfo(BaseModel):
    """A single ingested filing (FR-19)."""

    accession_number: str
    ticker: str
    company_name: str
    filing_date: str
    filing_type: str
    chunk_count: int | None
    ingested_at: str


class DocumentListResponse(BaseModel):
    """GET /v1/documents response (FR-19, FR-20)."""

    documents: list[DocumentInfo]
    total: int
    limit: int
    offset: int
