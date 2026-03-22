"""Vector retriever — embeds the query and searches pgvector.

References:
  - TDD: FR-13 (embed query, retrieve top-k via cosine distance,
                 optional ticker filter)
  - TDD: FR-19, FR-20 (list ingested filings)
  - TDD: NFR-1 (retrieval within 200ms at p99)
"""

from __future__ import annotations

import contextlib
import time
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Generator

import numpy as np
import psycopg2
import structlog
from pgvector.psycopg2 import register_vector
from sentence_transformers import SentenceTransformer

from src.rag.prompts import RetrievedChunk

logger = structlog.get_logger()

DEFAULT_MODEL = "sentence-transformers/all-MiniLM-L6-v2"

# How many candidates to fetch from pgvector before MMR reranking.
# 4× top_k gives the algorithm enough diversity headroom without a
# meaningful latency cost (HNSW lookup is O(log n) regardless of LIMIT).
_CANDIDATE_MULTIPLIER = 4
_MAX_CANDIDATES = 100


def _apply_mmr(
    candidates: list[tuple[RetrievedChunk, np.ndarray]],
    top_k: int,
    lambda_mmr: float = 0.5,
) -> list[RetrievedChunk]:
    """Select top_k chunks using Maximal Marginal Relevance.

    Iteratively picks the chunk that best balances relevance to the query
    against redundancy with chunks already selected:

        score = λ × relevance_score − (1−λ) × max_cos_sim(chunk, selected)

    lambda_mmr=1.0 → pure relevance order (identical to no reranking).
    lambda_mmr=0.0 → pure diversity (ignores relevance entirely).
    lambda_mmr=0.5 → equal weight, the recommended default.

    Embeddings must be L2-normalised so that dot product == cosine similarity.

    Reference: Carbonell & Goldstein (1998), "The Use of MMR, Diversity-Based
    Reranking for Reordering Documents and Producing Summaries", SIGIR.
    """
    if not candidates:
        return []

    selected: list[RetrievedChunk] = []
    selected_vecs: list[np.ndarray] = []
    remaining = list(candidates)

    while remaining and len(selected) < top_k:
        best_idx = 0
        best_score = float("-inf")

        for i, (chunk, emb) in enumerate(remaining):
            if not selected_vecs:
                # First pick is always the highest-relevance chunk.
                mmr_score = chunk.relevance_score
            else:
                max_sim = max(float(np.dot(emb, sel)) for sel in selected_vecs)
                mmr_score = lambda_mmr * chunk.relevance_score - (1 - lambda_mmr) * max_sim

            if mmr_score > best_score:
                best_score = mmr_score
                best_idx = i

        chunk, emb = remaining.pop(best_idx)
        selected.append(chunk)
        selected_vecs.append(emb)

    return selected


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

    @contextlib.contextmanager
    def _cursor(self) -> Generator[psycopg2.extensions.cursor, None, None]:
        """Context manager: yields a cursor and always closes it on exit."""
        cur = self._get_conn().cursor()
        try:
            yield cur
        finally:
            cur.close()

    def embed_query(self, question: str) -> list[float]:
        """Embed a user's question using the same model as ingestion (FR-13)."""
        embedding = self._model.encode(
            question,
            normalize_embeddings=True,
            show_progress_bar=False,
        )
        return embedding.tolist()

    def verify_embedding_model_consistency(self) -> None:
        """Compare the loaded model's output dimension against stored embeddings.

        Called at startup to detect silent model mismatches — e.g. the embedding
        worker used all-MiniLM-L6-v2 (384-dim) but the query API is now configured
        with a different model. A dimension mismatch will corrupt all retrieval
        results silently at query time.

        Logs INFO when consistent, ERROR when a mismatch is detected. Skips
        the check when the database contains no embeddings yet (fresh install).
        """
        with self._cursor() as cur:
            cur.execute("SELECT vector_dims(embedding) FROM document_chunks LIMIT 1")
            row = cur.fetchone()

        if row is None:
            logger.info("embedding_consistency_check_skipped", reason="no_chunks_in_db")
            return

        stored_dim: int = row[0]
        model_dim = self._model.get_sentence_embedding_dimension()

        if stored_dim != model_dim:
            logger.error(
                "embedding_model_dimension_mismatch",
                stored_dim=stored_dim,
                model_dim=model_dim,
                advice="Re-embed all documents with the current model or restore the original model",
            )
        else:
            logger.info("embedding_model_consistent", dim=model_dim)

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

        # Step 2: Query pgvector — fetch more candidates than requested so
        # MMR has enough material to trade off relevance against diversity.
        candidate_k = min(top_k * _CANDIDATE_MULTIPLIER, _MAX_CANDIDATES)

        if ticker_filter:
            sql = """
                SELECT chunk_id, ticker, filing_date, section_name,
                       chunk_text, embedding,
                       1 - (embedding <=> %s::vector) AS relevance_score
                FROM document_chunks
                WHERE ticker = %s
                ORDER BY embedding <=> %s::vector
                LIMIT %s
            """
            params: tuple[Any, ...] = (query_embedding, ticker_filter, query_embedding, candidate_k)
        else:
            sql = """
                SELECT chunk_id, ticker, filing_date, section_name,
                       chunk_text, embedding,
                       1 - (embedding <=> %s::vector) AS relevance_score
                FROM document_chunks
                ORDER BY embedding <=> %s::vector
                LIMIT %s
            """
            params = (query_embedding, query_embedding, candidate_k)

        with self._cursor() as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()

        # Build (chunk, embedding_vector) pairs for MMR.
        # row[5] is the pgvector embedding (numpy array after register_vector).
        # row[6] is the relevance score.
        candidates: list[tuple[RetrievedChunk, np.ndarray]] = []
        for row in rows:
            chunk = RetrievedChunk(
                chunk_id=row[0],
                ticker=row[1],
                filing_date=str(row[2]),
                section=row[3],
                relevance_score=float(row[6]),
                text=row[4],
            )
            candidates.append((chunk, np.asarray(row[5])))

        # Step 3: Apply MMR to select top_k diverse chunks.
        chunks = _apply_mmr(candidates, top_k)

        logger.info(
            "retrieval_complete",
            top_k=top_k,
            candidates_fetched=len(candidates),
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
        with self._cursor() as cur:
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
        return rows, total
