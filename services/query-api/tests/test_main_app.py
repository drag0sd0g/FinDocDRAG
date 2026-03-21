"""Unit tests for the Query API FastAPI endpoints (main.py).

All dependencies (retriever, generator, DB) are mocked.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from src.models import QueryResponse, SourceChunk, TimingInfo

# ── Health & Ready ───────────────────────────────────────────────


class TestHealthAndReady:
    """Test /health and /ready endpoints."""

    def test_health_returns_healthy(self) -> None:
        from fastapi.testclient import TestClient

        import src.main as main_mod

        mock_retriever = MagicMock()
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_conn.cursor.return_value = mock_cur
        mock_retriever._get_conn.return_value = mock_conn

        original_retriever = main_mod._retriever
        main_mod._retriever = mock_retriever

        try:
            client = TestClient(app=main_mod.app, raise_server_exceptions=False)
            response = client.get("/health")
            assert response.status_code == 200
            assert response.json() == {"status": "healthy"}
            assert "X-Request-ID" in response.headers
        finally:
            main_mod._retriever = original_retriever

    def test_health_returns_503_when_not_initialized(self) -> None:
        from fastapi.testclient import TestClient

        import src.main as main_mod

        original_retriever = main_mod._retriever
        main_mod._retriever = None

        try:
            client = TestClient(app=main_mod.app, raise_server_exceptions=False)
            response = client.get("/health")
            assert response.status_code == 503
        finally:
            main_mod._retriever = original_retriever

    def test_ready_returns_503_when_not_initialized(self) -> None:
        from fastapi.testclient import TestClient

        import src.main as main_mod

        original_retriever = main_mod._retriever
        original_generator = main_mod._generator
        main_mod._retriever = None
        main_mod._generator = None

        try:
            client = TestClient(app=main_mod.app, raise_server_exceptions=False)
            response = client.get("/ready")
            assert response.status_code == 503
        finally:
            main_mod._retriever = original_retriever
            main_mod._generator = original_generator

    def test_ready_returns_ready_when_initialized(self) -> None:
        from fastapi.testclient import TestClient

        import src.main as main_mod

        original_retriever = main_mod._retriever
        original_generator = main_mod._generator
        main_mod._retriever = MagicMock()
        main_mod._generator = MagicMock()

        try:
            client = TestClient(app=main_mod.app, raise_server_exceptions=False)
            response = client.get("/ready")
            assert response.status_code == 200
            assert response.json() == {"status": "ready"}
        finally:
            main_mod._retriever = original_retriever
            main_mod._generator = original_generator


# ── POST /v1/query ───────────────────────────────────────────────


class TestQueryEndpoint:
    """Test the /v1/query endpoint."""

    def test_query_returns_503_when_not_initialized(self) -> None:
        from fastapi.testclient import TestClient

        import src.main as main_mod

        original_retriever = main_mod._retriever
        original_generator = main_mod._generator
        main_mod._retriever = None
        main_mod._generator = None

        try:
            client = TestClient(app=main_mod.app, raise_server_exceptions=False)
            response = client.post(
                "/v1/query",
                json={"question": "What is revenue?"},
            )
            assert response.status_code == 503
        finally:
            main_mod._retriever = original_retriever
            main_mod._generator = original_generator

    def test_query_success(self) -> None:
        from fastapi.testclient import TestClient

        import src.main as main_mod

        mock_generator = MagicMock()
        mock_generator.answer = AsyncMock(return_value=QueryResponse(
            answer="Revenue was $394B.",
            sources=[
                SourceChunk(
                    chunk_id="c1",
                    ticker="AAPL",
                    filing_date="2024-11-01",
                    section="Item 7",
                    relevance_score=0.9,
                    text_preview="Revenue was...",
                )
            ],
            model="test-model",
            timing=TimingInfo(
                embedding_ms=10.0,
                retrieval_ms=5.0,
                generation_ms=100.0,
                total_ms=115.0,
            ),
            degraded=False,
        ))

        original_retriever = main_mod._retriever
        original_generator = main_mod._generator
        main_mod._retriever = MagicMock()
        main_mod._generator = mock_generator

        try:
            with patch.dict("os.environ", {"API_KEYS": ""}, clear=False):
                client = TestClient(app=main_mod.app, raise_server_exceptions=False)
                response = client.post(
                    "/v1/query",
                    json={"question": "What is Apple's revenue?", "ticker_filter": "AAPL"},
                )
            assert response.status_code == 200
            data = response.json()
            assert data["answer"] == "Revenue was $394B."
            assert len(data["sources"]) == 1
            assert data["degraded"] is False
            assert "request_id" in data
            assert len(data["request_id"]) == 36  # UUID format
            assert "X-Request-ID" in response.headers
        finally:
            main_mod._retriever = original_retriever
            main_mod._generator = original_generator


# ── GET /v1/documents ────────────────────────────────────────────


class TestDocumentsEndpoint:
    """Test the /v1/documents endpoint."""

    def test_documents_returns_503_when_not_initialized(self) -> None:
        from fastapi.testclient import TestClient

        import src.main as main_mod

        original_retriever = main_mod._retriever
        original_generator = main_mod._generator
        main_mod._retriever = None
        main_mod._generator = None

        try:
            client = TestClient(app=main_mod.app, raise_server_exceptions=False)
            response = client.get("/v1/documents")
            assert response.status_code == 503
        finally:
            main_mod._retriever = original_retriever
            main_mod._generator = original_generator

    def test_documents_success_no_filter(self) -> None:
        from fastapi.testclient import TestClient

        import src.main as main_mod

        mock_retriever = MagicMock()
        mock_retriever.list_documents.return_value = (
            [
                ("0001-24-000001", "AAPL", "Apple Inc.", "2024-11-01", "10-K", 10, "2024-11-15"),
                ("0001-24-000002", "MSFT", "Microsoft Corp.", "2024-10-15", "10-K", 8, "2024-10-20"),
            ],
            2,
        )

        original_retriever = main_mod._retriever
        original_generator = main_mod._generator
        main_mod._retriever = mock_retriever
        main_mod._generator = MagicMock()

        try:
            with patch.dict("os.environ", {"API_KEYS": ""}, clear=False):
                client = TestClient(app=main_mod.app, raise_server_exceptions=False)
                response = client.get("/v1/documents")
            assert response.status_code == 200
            data = response.json()
            assert data["total"] == 2
            assert len(data["documents"]) == 2
            assert data["documents"][0]["ticker"] == "AAPL"
        finally:
            main_mod._retriever = original_retriever
            main_mod._generator = original_generator

    def test_documents_with_ticker_filter(self) -> None:
        from fastapi.testclient import TestClient

        import src.main as main_mod

        mock_retriever = MagicMock()
        mock_retriever.list_documents.return_value = (
            [
                ("0001-24-000001", "AAPL", "Apple Inc.", "2024-11-01", "10-K", 10, "2024-11-15"),
            ],
            1,
        )

        original_retriever = main_mod._retriever
        original_generator = main_mod._generator
        main_mod._retriever = mock_retriever
        main_mod._generator = MagicMock()

        try:
            with patch.dict("os.environ", {"API_KEYS": ""}, clear=False):
                client = TestClient(app=main_mod.app, raise_server_exceptions=False)
                response = client.get("/v1/documents?ticker=AAPL")
            assert response.status_code == 200
            data = response.json()
            assert data["total"] == 1
            assert data["documents"][0]["ticker"] == "AAPL"

            # Verify the ticker filter was forwarded to the retriever
            mock_retriever.list_documents.assert_called_once_with(
                ticker="AAPL", limit=20, offset=0
            )
        finally:
            main_mod._retriever = original_retriever
            main_mod._generator = original_generator

    def test_documents_with_pagination(self) -> None:
        from fastapi.testclient import TestClient

        import src.main as main_mod

        mock_retriever = MagicMock()
        mock_retriever.list_documents.return_value = (
            [
                ("0001-24-000003", "AAPL", "Apple Inc.", "2024-09-01", "10-K", 7, "2024-09-10"),
            ],
            5,
        )

        original_retriever = main_mod._retriever
        original_generator = main_mod._generator
        main_mod._retriever = mock_retriever
        main_mod._generator = MagicMock()

        try:
            with patch.dict("os.environ", {"API_KEYS": ""}, clear=False):
                client = TestClient(app=main_mod.app, raise_server_exceptions=False)
                response = client.get("/v1/documents?limit=1&offset=2")
            assert response.status_code == 200
            data = response.json()
            assert data["total"] == 5
            assert data["limit"] == 1
            assert data["offset"] == 2
        finally:
            main_mod._retriever = original_retriever
            main_mod._generator = original_generator


# ── Retriever connect/close/get_conn coverage ────────────────────


class TestRetrieverConnMethods:
    """Additional tests for retriever.py connect/close/_get_conn to cover missing lines."""

    @patch("src.rag.retriever.SentenceTransformer")
    @patch("src.rag.retriever.register_vector")
    @patch("src.rag.retriever.psycopg2")
    def test_connect_sets_autocommit(
        self,
        mock_psycopg2: MagicMock,
        mock_register: MagicMock,
        mock_st: MagicMock,
    ) -> None:
        from src.rag.retriever import Retriever

        mock_conn = MagicMock()
        mock_psycopg2.connect.return_value = mock_conn
        mock_st.return_value = MagicMock()

        retriever = Retriever(dsn="postgresql://fake", model_name="test")
        retriever.connect()

        mock_psycopg2.connect.assert_called_with("postgresql://fake")
        assert mock_conn.autocommit is True
        mock_register.assert_called_once_with(mock_conn)

    @patch("src.rag.retriever.SentenceTransformer")
    @patch("src.rag.retriever.psycopg2")
    def test_close_closes_conn(
        self,
        mock_psycopg2: MagicMock,
        mock_st: MagicMock,
    ) -> None:
        from src.rag.retriever import Retriever

        mock_st.return_value = MagicMock()
        retriever = Retriever(dsn="postgresql://fake", model_name="test")

        mock_conn = MagicMock()
        retriever._conn = mock_conn

        retriever.close()
        mock_conn.close.assert_called_once()
        assert retriever._conn is None

    @patch("src.rag.retriever.SentenceTransformer")
    @patch("src.rag.retriever.psycopg2")
    def test_close_when_no_conn(
        self,
        mock_psycopg2: MagicMock,
        mock_st: MagicMock,
    ) -> None:
        from src.rag.retriever import Retriever

        mock_st.return_value = MagicMock()
        retriever = Retriever(dsn="postgresql://fake", model_name="test")
        retriever._conn = None

        retriever.close()  # should not raise

    @patch("src.rag.retriever.SentenceTransformer")
    @patch("src.rag.retriever.register_vector")
    @patch("src.rag.retriever.psycopg2")
    def test_get_conn_reconnects_when_closed(
        self,
        mock_psycopg2: MagicMock,
        mock_register: MagicMock,
        mock_st: MagicMock,
    ) -> None:
        from src.rag.retriever import Retriever

        mock_conn = MagicMock()
        mock_conn.closed = True
        mock_new_conn = MagicMock()
        mock_psycopg2.connect.return_value = mock_new_conn
        mock_st.return_value = MagicMock()

        retriever = Retriever(dsn="postgresql://fake", model_name="test")
        retriever._conn = mock_conn

        result = retriever._get_conn()
        assert result is not None
        mock_psycopg2.connect.assert_called()


# ── Rate-limit key function ──────────────────────────────────────


class TestRateLimitKeyFunc:
    """Test the _get_api_key rate-limit key function."""

    def test_returns_api_key_when_present(self) -> None:
        from src.main import _get_api_key

        mock_request = MagicMock()
        mock_request.headers = {"X-API-Key": "my-key"}

        result = _get_api_key(mock_request)
        assert result == "my-key"

    @patch("src.main.get_remote_address", return_value="127.0.0.1")
    def test_returns_ip_when_no_api_key(self, mock_addr: MagicMock) -> None:
        from src.main import _get_api_key

        mock_request = MagicMock()
        mock_request.headers = {}

        result = _get_api_key(mock_request)
        assert result == "127.0.0.1"
