"""Unit tests for the retriever.

The embedding model and database are mocked — no real resources needed.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import numpy as np

from src.rag.prompts import RetrievedChunk


class TestRetriever:
    """Tests for the Retriever class."""

    @patch("src.rag.retriever.SentenceTransformer")
    def test_embed_query_returns_list(self, mock_st_class: MagicMock) -> None:
        """embed_query should return a list of floats."""
        from src.rag.retriever import Retriever

        mock_model = MagicMock()
        mock_model.encode.return_value = np.array([0.1] * 384)
        mock_st_class.return_value = mock_model

        with patch("src.rag.retriever.psycopg2"):
            retriever = Retriever(dsn="postgresql://fake", model_name="test")

        result = retriever.embed_query("What is Apple's revenue?")
        assert isinstance(result, list)
        assert len(result) == 384

    @patch("src.rag.retriever.SentenceTransformer")
    def test_retrieve_with_ticker_filter(self, mock_st_class: MagicMock) -> None:
        """retrieve should include a WHERE clause when ticker_filter is set."""
        from src.rag.retriever import Retriever

        mock_model = MagicMock()
        mock_model.encode.return_value = np.array([0.1] * 384)
        mock_st_class.return_value = mock_model

        with patch("src.rag.retriever.psycopg2"):
            retriever = Retriever(dsn="postgresql://fake", model_name="test")

        # Mock the connection and cursor
        mock_cur = MagicMock()
        mock_cur.fetchall.return_value = [
            ("chunk1", "AAPL", "2024-11-01", "Item 1A", "Risk text...", np.array([0.1] * 384), 0.87),
        ]
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cur
        mock_conn.closed = False
        retriever._conn = mock_conn

        chunks, embedding, emb_ms = retriever.retrieve(
            "What are Apple's risks?", top_k=5, ticker_filter="AAPL"
        )

        assert len(chunks) == 1
        assert chunks[0].ticker == "AAPL"
        assert chunks[0].relevance_score == 0.87
        # Verify the SQL included ticker filter
        executed_sql = mock_cur.execute.call_args[0][0]
        assert "ticker = %s" in executed_sql

    @patch("src.rag.retriever.SentenceTransformer")
    def test_retrieve_without_ticker_filter(self, mock_st_class: MagicMock) -> None:
        """retrieve without filter should not have WHERE ticker clause."""
        from src.rag.retriever import Retriever

        mock_model = MagicMock()
        mock_model.encode.return_value = np.array([0.1] * 384)
        mock_st_class.return_value = mock_model

        with patch("src.rag.retriever.psycopg2"):
            retriever = Retriever(dsn="postgresql://fake", model_name="test")

        mock_cur = MagicMock()
        mock_cur.fetchall.return_value = []
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cur
        mock_conn.closed = False
        retriever._conn = mock_conn

        chunks, _, _ = retriever.retrieve("General question", top_k=3)

        assert chunks == []
        executed_sql = mock_cur.execute.call_args[0][0]
        assert "ticker = %s" not in executed_sql


class TestEmbeddingModelConsistency:
    """Tests for Retriever.verify_embedding_model_consistency."""

    @patch("src.rag.retriever.SentenceTransformer")
    def test_consistent_dimensions_logs_info(self, mock_st_class: MagicMock) -> None:
        """No error logged when stored dim matches model dim."""
        from src.rag.retriever import Retriever

        mock_model = MagicMock()
        mock_model.get_sentence_embedding_dimension.return_value = 384
        mock_st_class.return_value = mock_model

        with patch("src.rag.retriever.psycopg2"):
            retriever = Retriever(dsn="postgresql://fake", model_name="test")

        mock_cur = MagicMock()
        mock_cur.fetchone.return_value = (384,)
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cur
        mock_conn.closed = False
        retriever._conn = mock_conn

        # Should complete without raising
        retriever.verify_embedding_model_consistency()
        mock_cur.execute.assert_called_once()

    @patch("src.rag.retriever.SentenceTransformer")
    def test_dimension_mismatch_logs_error(self, mock_st_class: MagicMock) -> None:
        """Error logged when stored dim differs from model dim (silent corruption risk)."""
        from src.rag.retriever import Retriever

        mock_model = MagicMock()
        mock_model.get_sentence_embedding_dimension.return_value = 384
        mock_st_class.return_value = mock_model

        with patch("src.rag.retriever.psycopg2"):
            retriever = Retriever(dsn="postgresql://fake", model_name="test")

        mock_cur = MagicMock()
        mock_cur.fetchone.return_value = (768,)  # mismatch!
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cur
        mock_conn.closed = False
        retriever._conn = mock_conn

        with patch("src.rag.retriever.logger") as mock_logger:
            retriever.verify_embedding_model_consistency()
            mock_logger.error.assert_called_once()
            call_kwargs = mock_logger.error.call_args[1]
            assert call_kwargs["stored_dim"] == 768
            assert call_kwargs["model_dim"] == 384

    @patch("src.rag.retriever.SentenceTransformer")
    def test_skips_check_when_no_data(self, mock_st_class: MagicMock) -> None:
        """Check is skipped (not an error) when document_chunks is empty."""
        from src.rag.retriever import Retriever

        mock_model = MagicMock()
        mock_model.get_sentence_embedding_dimension.return_value = 384
        mock_st_class.return_value = mock_model

        with patch("src.rag.retriever.psycopg2"):
            retriever = Retriever(dsn="postgresql://fake", model_name="test")

        mock_cur = MagicMock()
        mock_cur.fetchone.return_value = None  # empty table
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cur
        mock_conn.closed = False
        retriever._conn = mock_conn

        with patch("src.rag.retriever.logger") as mock_logger:
            retriever.verify_embedding_model_consistency()
            mock_logger.error.assert_not_called()


class TestMMR:
    """Tests for _apply_mmr."""

    def _make(self, relevance: float, vec: list[float]) -> tuple:
        from src.rag.prompts import RetrievedChunk
        chunk = RetrievedChunk(
            chunk_id="x",
            ticker="AAPL",
            filing_date="2024-01-01",
            section="Item 1A",
            relevance_score=relevance,
            text="text",
        )
        return (chunk, np.array(vec, dtype=float))

    def test_empty_candidates_returns_empty(self) -> None:
        from src.rag.retriever import _apply_mmr
        assert _apply_mmr([], top_k=5) == []

    def test_fewer_candidates_than_top_k(self) -> None:
        from src.rag.retriever import _apply_mmr
        candidates = [self._make(0.9, [1.0, 0.0]), self._make(0.8, [0.0, 1.0])]
        result = _apply_mmr(candidates, top_k=5)
        assert len(result) == 2

    def test_first_selected_is_highest_relevance(self) -> None:
        from src.rag.retriever import _apply_mmr
        # Orthogonal vectors → diversity plays no role for first pick
        candidates = [
            self._make(0.7, [1.0, 0.0, 0.0]),
            self._make(0.9, [0.0, 1.0, 0.0]),
            self._make(0.5, [0.0, 0.0, 1.0]),
        ]
        result = _apply_mmr(candidates, top_k=1)
        assert result[0].relevance_score == 0.9

    def test_prefers_diverse_chunk_over_redundant_one(self) -> None:
        """With lambda=0.5 and top_k=2, MMR should pick the diverse chunk
        over the near-duplicate even when the duplicate has higher relevance.

        Setup (unit vectors, so dot == cosine):
          A: relevance=0.90, vec=[1, 0, 0]  ← selected first
          B: relevance=0.85, vec=[1, 0, 0]  ← near-duplicate of A (sim≈1.0)
          C: relevance=0.70, vec=[0, 1, 0]  ← diverse (sim=0.0 with A)

        Round 2 MMR scores:
          B: 0.5*0.85 - 0.5*1.0 = -0.075
          C: 0.5*0.70 - 0.5*0.0 = +0.350  ← wins
        """
        from src.rag.retriever import _apply_mmr
        candidates = [
            self._make(0.90, [1.0, 0.0, 0.0]),  # A
            self._make(0.85, [1.0, 0.0, 0.0]),  # B — near-duplicate
            self._make(0.70, [0.0, 1.0, 0.0]),  # C — diverse
        ]
        result = _apply_mmr(candidates, top_k=2)
        assert result[0].relevance_score == 0.90  # A always first
        assert result[1].relevance_score == 0.70  # C beats B

    def test_lambda_1_gives_pure_relevance_order(self) -> None:
        """lambda=1.0 disables diversity — identical to sorting by relevance."""
        from src.rag.retriever import _apply_mmr
        candidates = [
            self._make(0.90, [1.0, 0.0, 0.0]),
            self._make(0.85, [1.0, 0.0, 0.0]),  # near-duplicate but higher relevance
            self._make(0.70, [0.0, 1.0, 0.0]),
        ]
        result = _apply_mmr(candidates, top_k=2, lambda_mmr=1.0)
        scores = [c.relevance_score for c in result]
        assert scores == [0.90, 0.85]

    def test_returns_exactly_top_k_when_enough_candidates(self) -> None:
        from src.rag.retriever import _apply_mmr
        candidates = [self._make(1.0 - i * 0.1, [float(i == j) for j in range(6)]) for i in range(6)]
        result = _apply_mmr(candidates, top_k=4)
        assert len(result) == 4


class TestRetrievedChunk:
    """Tests for the RetrievedChunk dataclass."""

    def test_fields(self) -> None:
        c = RetrievedChunk(
            chunk_id="abc123",
            ticker="MSFT",
            filing_date="2024-10-15",
            section="Item 7",
            relevance_score=0.92,
            text="Revenue grew...",
        )
        assert c.ticker == "MSFT"
        assert c.relevance_score == 0.92
