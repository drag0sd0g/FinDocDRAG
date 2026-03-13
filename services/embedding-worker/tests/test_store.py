"""Unit tests for the ChunkStore.

All database calls are mocked — no real PostgreSQL needed.
"""

from __future__ import annotations

import contextlib
from unittest.mock import MagicMock, patch

from src.chunker import Chunk
from src.store import ChunkStore


class TestChunkStoreInit:
    """Test construction and connection management."""

    @patch("src.store.psycopg2")
    @patch("src.store.register_vector")
    def test_connect_sets_conn(
        self, mock_register: MagicMock, mock_psycopg2: MagicMock
    ) -> None:
        mock_conn = MagicMock()
        mock_psycopg2.connect.return_value = mock_conn

        store = ChunkStore(dsn="postgresql://fake")
        store.connect()

        mock_psycopg2.connect.assert_called_once_with("postgresql://fake")
        assert mock_conn.autocommit is False
        mock_register.assert_called_once_with(mock_conn)

    @patch("src.store.psycopg2")
    @patch("src.store.register_vector")
    def test_close_closes_conn(
        self, mock_register: MagicMock, mock_psycopg2: MagicMock
    ) -> None:
        mock_conn = MagicMock()
        mock_psycopg2.connect.return_value = mock_conn

        store = ChunkStore(dsn="postgresql://fake")
        store.connect()
        store.close()

        mock_conn.close.assert_called_once()

    def test_close_when_no_conn(self) -> None:
        """Closing without connecting should not raise."""
        store = ChunkStore(dsn="postgresql://fake")
        store.close()  # should not raise

    @patch("src.store.psycopg2")
    @patch("src.store.register_vector")
    def test_get_conn_reconnects_when_closed(
        self, mock_register: MagicMock, mock_psycopg2: MagicMock
    ) -> None:
        mock_conn = MagicMock()
        mock_conn.closed = True
        mock_psycopg2.connect.return_value = mock_conn

        store = ChunkStore(dsn="postgresql://fake")
        store._conn = mock_conn

        # _get_conn should call connect() because conn.closed is True
        result = store._get_conn()
        assert result is not None


class TestStoreChunks:
    """Test store_chunks method."""

    def test_empty_chunks_returns_zero(self) -> None:
        store = ChunkStore(dsn="postgresql://fake")
        result = store.store_chunks([], [])
        assert result == 0

    @patch("src.store.psycopg2")
    @patch("src.store.register_vector")
    def test_store_chunks_executes_and_commits(
        self, mock_register: MagicMock, mock_psycopg2: MagicMock
    ) -> None:
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_cur.rowcount = 1
        mock_conn.cursor.return_value = mock_cur
        mock_conn.closed = False
        mock_psycopg2.connect.return_value = mock_conn

        store = ChunkStore(dsn="postgresql://fake")
        store._conn = mock_conn

        chunk = Chunk(
            chunk_id="abc123",
            accession_number="0001-24-000001",
            ticker="AAPL",
            filing_date="2024-11-01",
            section_name="Item 1",
            chunk_index=0,
            text="Business description.",
            token_count=5,
        )
        embedding = [0.1] * 384

        result = store.store_chunks([chunk], [embedding])

        assert result == 1
        mock_cur.executemany.assert_called_once()
        mock_conn.commit.assert_called_once()
        mock_cur.close.assert_called_once()

    @patch("src.store.psycopg2")
    @patch("src.store.register_vector")
    def test_store_chunks_rolls_back_on_error(
        self, mock_register: MagicMock, mock_psycopg2: MagicMock
    ) -> None:
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_cur.executemany.side_effect = RuntimeError("DB error")
        mock_conn.cursor.return_value = mock_cur
        mock_conn.closed = False
        mock_psycopg2.connect.return_value = mock_conn

        store = ChunkStore(dsn="postgresql://fake")
        store._conn = mock_conn

        chunk = Chunk(
            chunk_id="abc123",
            accession_number="0001-24-000001",
            ticker="AAPL",
            filing_date="2024-11-01",
            section_name="Item 1",
            chunk_index=0,
            text="Business description.",
            token_count=5,
        )

        with contextlib.suppress(RuntimeError):
            store.store_chunks([chunk], [[0.1] * 384])

        mock_conn.rollback.assert_called_once()
        mock_cur.close.assert_called_once()


class TestUpdateIngestionStatus:
    """Test update_ingestion_status method."""

    @patch("src.store.psycopg2")
    @patch("src.store.register_vector")
    def test_updates_status_and_commits(
        self, mock_register: MagicMock, mock_psycopg2: MagicMock
    ) -> None:
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_conn.cursor.return_value = mock_cur
        mock_conn.closed = False
        mock_psycopg2.connect.return_value = mock_conn

        store = ChunkStore(dsn="postgresql://fake")
        store._conn = mock_conn

        store.update_ingestion_status("0001-24-000001", 10)

        mock_cur.execute.assert_called_once()
        sql = mock_cur.execute.call_args[0][0]
        assert "EMBEDDED" in sql
        mock_conn.commit.assert_called_once()
        mock_cur.close.assert_called_once()

    @patch("src.store.psycopg2")
    @patch("src.store.register_vector")
    def test_rolls_back_on_error(
        self, mock_register: MagicMock, mock_psycopg2: MagicMock
    ) -> None:
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_cur.execute.side_effect = RuntimeError("DB error")
        mock_conn.cursor.return_value = mock_cur
        mock_conn.closed = False
        mock_psycopg2.connect.return_value = mock_conn

        store = ChunkStore(dsn="postgresql://fake")
        store._conn = mock_conn

        with contextlib.suppress(RuntimeError):
            store.update_ingestion_status("0001-24-000001", 10)

        mock_conn.rollback.assert_called_once()
        mock_cur.close.assert_called_once()
