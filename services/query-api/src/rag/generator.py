"""RAG answer generator — orchestrates retrieval, prompt, and LLM.

References:
  - TDD: FR-14 through FR-17
  - TDD: Section 7.4 (graceful degradation if LLM unavailable)
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    from src.llm.backend import LLMBackend
    from src.rag.retriever import Retriever

from src.models import QueryResponse, SourceChunk, TimingInfo
from src.rag.prompts import build_prompt

logger = structlog.get_logger()


class RAGGenerator:
    """Orchestrates the full RAG pipeline: retrieve → prompt → generate."""

    def __init__(self, retriever: Retriever, llm: LLMBackend) -> None:
        self._retriever = retriever
        self._llm = llm

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
        chunks, _, embedding_ms = self._retriever.retrieve(
            question=question,
            top_k=top_k,
            ticker_filter=ticker_filter,
        )

        retrieval_start = time.perf_counter()
        # retrieval_ms is measured inside retriever; we compute the
        # total overhead here
        retrieval_ms = (time.perf_counter() - retrieval_start) * 1000

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
