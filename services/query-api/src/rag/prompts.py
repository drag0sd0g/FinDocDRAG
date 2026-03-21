"""Prompt template for RAG answer generation.

References:
  - TDD: FR-14 (prompt with context + question + citation instruction)
  - TDD: FR-15 (exact prompt template structure)
"""

from __future__ import annotations

from dataclasses import dataclass

SYSTEM_PROMPT = """You are a financial document analyst. Answer the user's question using ONLY
the provided context from SEC filings. If the context does not contain enough
information to answer, say "I don't have enough information to answer this."

For every claim in your answer, cite the source using [Source N] notation,
where N corresponds to the context chunk number."""


@dataclass
class RetrievedChunk:
    """A chunk retrieved from pgvector, used for prompt construction."""

    chunk_id: str
    ticker: str
    filing_date: str
    section: str
    relevance_score: float
    text: str


def build_prompt(question: str, chunks: list[RetrievedChunk]) -> str:
    """Construct the full prompt with numbered context chunks (FR-14, FR-15).

    Format:
      {SYSTEM_PROMPT}

      Context:
      [Source 1] (AAPL, 2024-11-01, Item 1A - Risk Factors)
      <chunk text>
      ...

      Question: <user question>

      Answer:
    """
    context_parts: list[str] = []
    for i, chunk in enumerate(chunks, start=1):
        score = f"{chunk.relevance_score:.2f}"
        header = f"[Source {i}] ({chunk.ticker}, {chunk.filing_date}, {chunk.section}, relevance: {score})"
        context_parts.append(f"{header}\n{chunk.text}")

    context_block = "\n\n".join(context_parts)

    return f"""{SYSTEM_PROMPT}

Context:
{context_block}

Question: {question}

Answer:"""
