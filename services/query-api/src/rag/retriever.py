"""Vector retriever — embeds the query and searches pgvector.

References:
  - TDD: FR-13 (embed query, retrieve top-k via cosine distance,
                 optional ticker filter)
  - TDD: FR-19, FR-20 (list ingested filings)
  - TDD: NFR-1 (retrieval within 200ms at p99)
"""

from __future__ import annotations

import time
from typing import Any

import psycopg2
import structlog
from pgvector.psycopg2 import register_vector
from sentence_transformers import SentenceTransformer

from src.rag.prompts import RetrievedChunk

logger = structlog.get_logger()

DEFAULT_MODEL = "sentence-transformers/all-MiniLM-L6-v2"


class Retriever:
    """Embeds a query and retrieves the top-k most similar chunks."""

    def __init__(self, dsn: str, model_name: str = DEFAULT_MODEL) -> None:
        self._dsn = dsn
        self._conn: psycopg2.extensions.connection | None = None
        logger.info("retriever_loading_model", model=model_name)
        self._model = SentenceTransformer(model_name)
        logger.info("retriever_model_loaded", model=model_name)

    def connect(self) -> None:
        """Establish database connection and register pgvector type."""
        self._conn = psycopg2.connect(self._dsn)
        self._conn.autocommit = True
        register_vector(self._conn)
        logger.info("retriever_db_connected")

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def _get_conn(self) -> psycopg2.extensions.connection:
        if self._conn is None or self._conn.closed:
            self.connect()
        if self._conn is None:
            raise RuntimeError("Failed to establish database connection")
        return self._conn

    def embed_query(self, question: str) -> list[float]:
        """Embed a user's question using the same model as ingestion (FR-13)."""
        embedding = self._model.encode(
            question,
            normalize_embeddings=True,
            show_progress_bar=False,
        )
        return embedding.tolist()

    def retrieve(
        self,
        question: str,
        top_k: int = 5,
        ticker_filter: str | None = None,
    ) -> tuple[list[RetrievedChunk], list[float], float]:
        """Embed the question and retrieve the top-k chunks.

        Returns:
            (chunks, query_embedding, embedding_time_ms)
        """
        # Step 1: Embed the query
        t0 = time.perf_counter()
        query_embedding = self.embed_query(question)
        embedding_ms = (time.perf_counter() - t0) * 1000

        # Step 2: Query pgvector
        conn = self._get_conn()
        cur = conn.cursor()

        if ticker_filter:
            sql = """
                SELECT chunk_id, ticker, filing_date, section_name,
                       chunk_text,
                       1 - (embedding <=> %s::vector) AS relevance_score
                FROM document_chunks
                WHERE ticker = %s
                ORDER BY embedding <=> %s::vector
                LIMIT %s
            """
            params: tuple[Any, ...] = (query_embedding, ticker_filter, query_embedding, top_k)
        else:
            sql = """
                SELECT chunk_id, ticker, filing_date, section_name,
                       chunk_text,
                       1 - (embedding <=> %s::vector) AS relevance_score
                FROM document_chunks
                ORDER BY embedding <=> %s::vector
                LIMIT %s
            """
            params = (query_embedding, query_embedding, top_k)

        try:
            cur.execute(sql, params)
            rows = cur.fetchall()
        finally:
            cur.close()

        chunks: list[RetrievedChunk] = []
        for row in rows:
            chunks.append(
                RetrievedChunk(
                    chunk_id=row[0],
                    ticker=row[1],
                    filing_date=str(row[2]),
                    section=row[3],
                    relevance_score=float(row[5]),
                    text=row[4],
                )
            )

        logger.info(
            "retrieval_complete",
            top_k=top_k,
            results=len(chunks),
            ticker_filter=ticker_filter,
            embedding_ms=round(embedding_ms, 1),
        )

        return chunks, query_embedding, embedding_ms

    def list_documents(
        self,
        ticker: str | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> tuple[list[tuple[Any, ...]], int]:
        """Return a page of ingested filings from ingestion_log (FR-19, FR-20)."""
        conn = self._get_conn()
        cur = conn.cursor()
        try:
            if ticker:
                cur.execute(
                    "SELECT COUNT(*) FROM ingestion_log WHERE ticker = %s",
                    (ticker,),
                )
            else:
                cur.execute("SELECT COUNT(*) FROM ingestion_log")
            total: int = cur.fetchone()[0]

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
        return rows, total
