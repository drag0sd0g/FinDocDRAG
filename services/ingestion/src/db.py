"""PostgreSQL helpers for ingestion deduplication.

References:
  - TDD: FR-4 (idempotent re-ingestion — skip if accession_number exists)
  - TDD: Section 5.2.1 (ingest flow: deduplicate → publish → record)
  - TDD: Section 5.3 (ingestion_log schema)
"""

from __future__ import annotations

import contextlib
from typing import TYPE_CHECKING

import psycopg2
import structlog

if TYPE_CHECKING:
    from collections.abc import Generator

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

    @contextlib.contextmanager
    def _cursor(self) -> Generator[psycopg2.extensions.cursor, None, None]:
        """Context manager: yields a cursor and always closes it on exit."""
        cur = self._get_conn().cursor()
        try:
            yield cur
        finally:
            cur.close()

    def is_already_ingested(self, accession_number: str) -> bool:
        """Return True if the accession number already exists in ingestion_log (FR-4)."""
        with self._cursor() as cur:
            cur.execute(
                "SELECT 1 FROM ingestion_log WHERE accession_number = %s",
                (accession_number,),
            )
            return cur.fetchone() is not None

    def record_ingestion(self, filing: Filing) -> None:
        """Insert a new row into ingestion_log with status PUBLISHED.

        Uses ON CONFLICT DO NOTHING so concurrent or replayed calls are safe.
        """
        with self._cursor() as cur:
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
        logger.info(
            "ingestion_recorded",
            accession=filing.accession_number,
            ticker=filing.ticker,
        )
