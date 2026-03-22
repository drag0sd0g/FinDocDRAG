"""RAG answer generator — orchestrates retrieval, prompt, and LLM.

References:
  - TDD: FR-14 through FR-17
  - TDD: Section 7.4 (graceful degradation if LLM unavailable)
  - TDD: Section 8.1.3 (Query API Metrics)
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    from src.llm.backend import LLMBackend, LLMResponse
    from src.rag.retriever import Retriever

from src.metrics import (
    EMBEDDING_DURATION,
    LLM_DURATION,
    LLM_TOKENS_USED,
    RETRIEVAL_DURATION,
    RETRIEVAL_SCORE,
    TOTAL_DURATION,
)
from src.models import QueryResponse, SourceChunk, TimingInfo
from src.rag.prompts import build_prompt

logger = structlog.get_logger()


class RAGGenerator:
    """Orchestrates the full RAG pipeline: retrieve → prompt → generate."""

    def __init__(self, retriever: Retriever, llm: LLMBackend) -> None:
        self._retriever = retriever
        self._llm = llm

    def _record_llm_metrics(
        self,
        llm_response: LLMResponse,
        backend_name: str,
        generation_ms: float,
    ) -> None:
        """Record LLM duration and token usage metrics."""
        LLM_DURATION.labels(backend=backend_name).observe(generation_ms / 1000.0)
        LLM_TOKENS_USED.labels(backend=backend_name, type="prompt").inc(
            llm_response.prompt_tokens
        )
        LLM_TOKENS_USED.labels(backend=backend_name, type="completion").inc(
            llm_response.completion_tokens
        )

    async def answer(
        self,
        question: str,
        top_k: int = 5,
        ticker_filter: str | None = None,
    ) -> QueryResponse:
        """Run the full RAG pipeline and return a QueryResponse.

        If the LLM is unavailable, returns sources without an answer
        (degraded mode — TDD Section 7.4).
        """
        total_start = time.perf_counter()

        # ── Retrieve ─────────────────────────────────────────────
        # Time the full retrieve() call so we can split it into:
        #   embedding_ms  — query vector generation (returned by retriever)
        #   retrieval_ms  — pgvector SQL query (total minus embedding)
        t0_retrieve = time.perf_counter()
        chunks, _, embedding_ms = self._retriever.retrieve(
            question=question,
            top_k=top_k,
            ticker_filter=ticker_filter,
        )
        retrieval_ms = (time.perf_counter() - t0_retrieve) * 1000 - embedding_ms

        # Record embedding and retrieval duration metrics (convert ms → seconds)
        EMBEDDING_DURATION.observe(embedding_ms / 1000.0)
        RETRIEVAL_DURATION.observe(retrieval_ms / 1000.0)

        # Record top-1 relevance score
        if chunks:
            RETRIEVAL_SCORE.observe(chunks[0].relevance_score)

        # Build sources for the response (FR-17)
        sources = [
            SourceChunk(
                chunk_id=c.chunk_id,
                ticker=c.ticker,
                filing_date=c.filing_date,
                section=c.section,
                relevance_score=round(c.relevance_score, 4),
                text_preview=c.text[:200],
            )
            for c in chunks
        ]

        # ── Generate ────────────────────────────────────────────
        generation_ms = 0.0
        answer_text: str | None = None
        degraded = False

        if chunks:
            prompt = build_prompt(question, chunks)

            gen_start = time.perf_counter()
            try:
                llm_response = await self._llm.generate(prompt)
                answer_text = llm_response.text
                generation_ms = (time.perf_counter() - gen_start) * 1000
                self._record_llm_metrics(llm_response, self._llm.model_name, generation_ms)

            except Exception as exc:
                # Graceful degradation (TDD Section 7.4):
                # Return sources without answer
                generation_ms = (time.perf_counter() - gen_start) * 1000
                degraded = True
                logger.warning(
                    "llm_unavailable",
                    error=str(exc),
                    degraded=True,
                )

        total_ms = (time.perf_counter() - total_start) * 1000

        # Record total end-to-end duration
        TOTAL_DURATION.observe(total_ms / 1000.0)

        return QueryResponse(
            answer=answer_text,
            sources=sources,
            model=self._llm.model_name,
            timing=TimingInfo(
                embedding_ms=round(embedding_ms, 1),
                retrieval_ms=round(retrieval_ms, 1),
                generation_ms=round(generation_ms, 1),
                total_ms=round(total_ms, 1),
            ),
            degraded=degraded,
        )
