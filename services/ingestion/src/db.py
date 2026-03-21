"""PostgreSQL helpers for ingestion deduplication.

References:
  - TDD: FR-4 (idempotent re-ingestion — skip if accession_number exists)
  - TDD: Section 5.2.1 (ingest flow: deduplicate → publish → record)
  - TDD: Section 5.3 (ingestion_log schema)
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import psycopg2
import structlog

if TYPE_CHECKING:
    from src.edgar_client import Filing

logger = structlog.get_logger()


class IngestionDB:
    """Manages the ingestion_log table for deduplication tracking (FR-4).

    Keeps a single persistent connection; reconnects lazily if the
    connection is lost between requests.
    """

    def __init__(self, dsn: str) -> None:
        self._dsn = dsn
        self._conn: psycopg2.extensions.connection | None = None

    def connect(self) -> None:
        """Open the database connection."""
        self._conn = psycopg2.connect(self._dsn)
        self._conn.autocommit = True
        logger.info("ingestion_db_connected")

    def close(self) -> None:
        """Close the database connection."""
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def _get_conn(self) -> psycopg2.extensions.connection:
        if self._conn is None or self._conn.closed:
            self.connect()
        if self._conn is None:
            raise RuntimeError("Failed to establish database connection")
        return self._conn

    def is_already_ingested(self, accession_number: str) -> bool:
        """Return True if the accession number already exists in ingestion_log (FR-4)."""
        conn = self._get_conn()
        cur = conn.cursor()
        try:
            cur.execute(
                "SELECT 1 FROM ingestion_log WHERE accession_number = %s",
                (accession_number,),
            )
            result = cur.fetchone()
        finally:
            cur.close()
        return result is not None

    def record_ingestion(self, filing: Filing) -> None:
        """Insert a new row into ingestion_log with status PUBLISHED.

        Uses ON CONFLICT DO NOTHING so concurrent or replayed calls are safe.
        """
        conn = self._get_conn()
        cur = conn.cursor()
        try:
            cur.execute(
                """
                INSERT INTO ingestion_log
                    (accession_number, ticker, company_name, filing_date,
                     filing_type, source_url, status)
                VALUES (%s, %s, %s, %s, %s, %s, 'PUBLISHED')
                ON CONFLICT (accession_number) DO NOTHING
                """,
                (
                    filing.accession_number,
                    filing.ticker,
                    filing.company_name,
                    filing.filing_date,
                    filing.filing_type,
                    filing.source_url,
                ),
            )
        finally:
            cur.close()
        logger.info(
            "ingestion_recorded",
            accession=filing.accession_number,
            ticker=filing.ticker,
        )
