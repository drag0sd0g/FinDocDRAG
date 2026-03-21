"""pgvector storage for document chunks.

References:
  - TDD: FR-10 (store chunks in document_chunks table)
  - TDD: Section 5.3 PostgreSQL schema
  - TDD: Section 7.2 Idempotency (INSERT ... ON CONFLICT DO NOTHING)
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import psycopg2
import structlog
from pgvector.psycopg2 import register_vector
from psycopg2.extras import execute_values

if TYPE_CHECKING:
    from src.chunker import Chunk

logger = structlog.get_logger()


class ChunkStore:
    """Stores embedded chunks in PostgreSQL with pgvector.

    Uses INSERT ... ON CONFLICT DO NOTHING for idempotent upserts
    (TDD Section 7.2).
    """

    def __init__(self, dsn: str) -> None:
        self._dsn = dsn
        self._conn: psycopg2.extensions.connection | None = None

    def connect(self) -> None:
        """Establish database connection and register pgvector type."""
        self._conn = psycopg2.connect(self._dsn)
        self._conn.autocommit = False
        register_vector(self._conn)
        logger.info("chunk_store_connected")

    def close(self) -> None:
        """Close the database connection."""
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def _get_conn(self) -> psycopg2.extensions.connection:
        if self._conn is None or self._conn.closed:
            self.connect()
        assert self._conn is not None
        return self._conn

    def store_chunks(
        self,
        chunks: list[Chunk],
        embeddings: list[list[float]],
    ) -> int:
        """Insert chunks + embeddings into document_chunks.

        Returns the number of rows actually inserted (excludes
        duplicates suppressed by ON CONFLICT DO NOTHING).
        """
        if not chunks:
            return 0

        conn = self._get_conn()

        # Build a single-statement batch INSERT using execute_values so that
        # cursor.rowcount reflects the total number of rows actually inserted
        # (executemany sets rowcount to the *last* statement only).
        query = """
            INSERT INTO document_chunks
                (chunk_id, accession_number, ticker, filing_date,
                 section_name, chunk_index, chunk_text, token_count, embedding)
            VALUES %s
            ON CONFLICT (chunk_id) DO NOTHING
        """

        rows: list[tuple[Any, ...]] = [
            (
                chunk.chunk_id,
                chunk.accession_number,
                chunk.ticker,
                chunk.filing_date,
                chunk.section_name,
                chunk.chunk_index,
                chunk.text,
                chunk.token_count,
                emb,
            )
            for chunk, emb in zip(chunks, embeddings, strict=True)
        ]

        cur = conn.cursor()
        try:
            execute_values(cur, query, rows)
            inserted = cur.rowcount
            conn.commit()
            logger.info("chunks_stored", count=inserted, total=len(chunks))
            return inserted
        except Exception:
            conn.rollback()
            raise
        finally:
            cur.close()

    def update_ingestion_status(
        self,
        accession_number: str,
        chunk_count: int,
    ) -> None:
        """Update the ingestion_log row to EMBEDDED status with chunk count."""
        conn = self._get_conn()
        cur = conn.cursor()
        try:
            cur.execute(
                """
                UPDATE ingestion_log
                SET status = 'EMBEDDED', chunk_count = %s, completed_at = NOW()
                WHERE accession_number = %s
                """,
                (chunk_count, accession_number),
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            cur.close()
