"""Endpoint tests for the Query API FastAPI application.

All external dependencies (Retriever, RAGGenerator, DB) are mocked by directly
setting module-level globals, following the same pattern used in the
embedding-worker tests.
"""

from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock

from src.models import QueryResponse, SourceChunk, TimingInfo

# ── Helpers ──────────────────────────────────────────────────────


def _make_query_response(*, degraded: bool = False) -> QueryResponse:
    return QueryResponse(
        answer=None if degraded else "Apple faces supply chain risks.",
        sources=[
            SourceChunk(
                chunk_id="chunk1",
                ticker="AAPL",
                filing_date="2024-11-01",
                section="Item 1A",
                relevance_score=0.87,
                text_preview="Supply chain risks...",
            )
        ],
        model="test-model",
        timing=TimingInfo(
            embedding_ms=12.0,
            retrieval_ms=38.0,
            generation_ms=100.0,
            total_ms=150.0,
        ),
        degraded=degraded,
        request_id="",
    )


def _mock_retriever_with_db() -> MagicMock:
    """Return a Retriever mock that passes the /health DB probe."""
    mock_retriever = MagicMock()
    mock_cur = MagicMock()
    mock_conn = MagicMock()
    mock_conn.cursor.return_value = mock_cur
    mock_retriever._get_conn.return_value = mock_conn
    return mock_retriever


# ── Health & Readiness ───────────────────────────────────────────


class TestHealthEndpoints:
    def test_health_returns_healthy(self) -> None:
        from fastapi.testclient import TestClient

        import src.main as main_mod

        original_retriever = main_mod._retriever
        original_generator = main_mod._generator
        main_mod._retriever = _mock_retriever_with_db()
        main_mod._generator = MagicMock()

        try:
            client = TestClient(app=main_mod.app, raise_server_exceptions=False)
            response = client.get("/health")
            assert response.status_code == 200
            assert response.json() == {"status": "healthy"}
            assert "X-Request-ID" in response.headers
        finally:
            main_mod._retriever = original_retriever
            main_mod._generator = original_generator

    def test_health_returns_503_when_not_initialized(self) -> None:
        from fastapi.testclient import TestClient

        import src.main as main_mod

        original_retriever = main_mod._retriever
        original_generator = main_mod._generator
        main_mod._retriever = None
        main_mod._generator = None

        try:
            client = TestClient(app=main_mod.app, raise_server_exceptions=False)
            response = client.get("/health")
            assert response.status_code == 503
        finally:
            main_mod._retriever = original_retriever
            main_mod._generator = original_generator

    def test_health_returns_503_on_db_error(self) -> None:
        from fastapi.testclient import TestClient

        import src.main as main_mod

        mock_retriever = MagicMock()
        mock_retriever._get_conn.side_effect = Exception("db unreachable")

        original_retriever = main_mod._retriever
        original_generator = main_mod._generator
        main_mod._retriever = mock_retriever
        main_mod._generator = MagicMock()

        try:
            client = TestClient(app=main_mod.app, raise_server_exceptions=False)
            response = client.get("/health")
            assert response.status_code == 503
        finally:
            main_mod._retriever = original_retriever
            main_mod._generator = original_generator

    def test_ready_returns_ready(self) -> None:
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


# ── POST /v1/query ───────────────────────────────────────────────


class TestQueryEndpoint:
    def test_query_success(self) -> None:
        from fastapi.testclient import TestClient

        import src.main as main_mod

        mock_generator = MagicMock()
        mock_generator.answer = AsyncMock(return_value=_make_query_response())

        original_retriever = main_mod._retriever
        original_generator = main_mod._generator
        main_mod._retriever = MagicMock()
        main_mod._generator = mock_generator

        try:
            with patch_api_keys(""):
                client = TestClient(app=main_mod.app, raise_server_exceptions=False)
                response = client.post(
                    "/v1/query",
                    json={"question": "What are Apple's risk factors?"},
                )
            assert response.status_code == 200
            data = response.json()
            assert data["answer"] == "Apple faces supply chain risks."
            assert data["degraded"] is False
            assert len(data["sources"]) == 1
            assert "request_id" in data
        finally:
            main_mod._retriever = original_retriever
            main_mod._generator = original_generator

    def test_query_degraded_when_llm_unavailable(self) -> None:
        from fastapi.testclient import TestClient

        import src.main as main_mod

        mock_generator = MagicMock()
        mock_generator.answer = AsyncMock(return_value=_make_query_response(degraded=True))

        original_retriever = main_mod._retriever
        original_generator = main_mod._generator
        main_mod._retriever = MagicMock()
        main_mod._generator = mock_generator

        try:
            with patch_api_keys(""):
                client = TestClient(app=main_mod.app, raise_server_exceptions=False)
                response = client.post(
                    "/v1/query",
                    json={"question": "What are Apple's risk factors?"},
                )
            assert response.status_code == 200
            data = response.json()
            assert data["answer"] is None
            assert data["degraded"] is True
        finally:
            main_mod._retriever = original_retriever
            main_mod._generator = original_generator

    def test_query_empty_question_returns_422(self) -> None:
        from fastapi.testclient import TestClient

        import src.main as main_mod

        original_retriever = main_mod._retriever
        original_generator = main_mod._generator
        main_mod._retriever = MagicMock()
        main_mod._generator = MagicMock()

        try:
            with patch_api_keys(""):
                client = TestClient(app=main_mod.app, raise_server_exceptions=False)
                response = client.post("/v1/query", json={"question": ""})
            assert response.status_code == 422
        finally:
            main_mod._retriever = original_retriever
            main_mod._generator = original_generator

    def test_query_whitespace_only_question_returns_422(self) -> None:
        from fastapi.testclient import TestClient

        import src.main as main_mod

        original_retriever = main_mod._retriever
        original_generator = main_mod._generator
        main_mod._retriever = MagicMock()
        main_mod._generator = MagicMock()

        try:
            with patch_api_keys(""):
                client = TestClient(app=main_mod.app, raise_server_exceptions=False)
                response = client.post("/v1/query", json={"question": "   "})
            assert response.status_code == 422
        finally:
            main_mod._retriever = original_retriever
            main_mod._generator = original_generator

    def test_query_valid_api_key_passes(self) -> None:
        from fastapi.testclient import TestClient

        import src.main as main_mod

        mock_generator = MagicMock()
        mock_generator.answer = AsyncMock(return_value=_make_query_response())

        original_retriever = main_mod._retriever
        original_generator = main_mod._generator
        main_mod._retriever = MagicMock()
        main_mod._generator = mock_generator

        try:
            with patch_api_keys("secret-key"):
                client = TestClient(app=main_mod.app, raise_server_exceptions=False)
                response = client.post(
                    "/v1/query",
                    json={"question": "What are Apple's risk factors?"},
                    headers={"X-API-Key": "secret-key"},
                )
            assert response.status_code == 200
        finally:
            main_mod._retriever = original_retriever
            main_mod._generator = original_generator

    def test_query_invalid_api_key_returns_401(self) -> None:
        from fastapi.testclient import TestClient

        import src.main as main_mod

        original_retriever = main_mod._retriever
        original_generator = main_mod._generator
        main_mod._retriever = MagicMock()
        main_mod._generator = MagicMock()

        try:
            with patch_api_keys("secret-key"):
                client = TestClient(app=main_mod.app, raise_server_exceptions=False)
                response = client.post(
                    "/v1/query",
                    json={"question": "What are Apple's risk factors?"},
                    headers={"X-API-Key": "wrong-key"},
                )
            assert response.status_code == 401
        finally:
            main_mod._retriever = original_retriever
            main_mod._generator = original_generator

    def test_query_missing_api_key_returns_401(self) -> None:
        from fastapi.testclient import TestClient

        import src.main as main_mod

        original_retriever = main_mod._retriever
        original_generator = main_mod._generator
        main_mod._retriever = MagicMock()
        main_mod._generator = MagicMock()

        try:
            with patch_api_keys("secret-key"):
                client = TestClient(app=main_mod.app, raise_server_exceptions=False)
                response = client.post(
                    "/v1/query",
                    json={"question": "What are Apple's risk factors?"},
                )
            assert response.status_code == 401
        finally:
            main_mod._retriever = original_retriever
            main_mod._generator = original_generator

    def test_query_passes_ticker_filter_and_top_k(self) -> None:
        from fastapi.testclient import TestClient

        import src.main as main_mod

        captured: dict = {}

        async def fake_answer(
            question: str, top_k: int = 5, ticker_filter: str | None = None
        ) -> QueryResponse:
            captured["top_k"] = top_k
            captured["ticker_filter"] = ticker_filter
            return _make_query_response()

        mock_generator = MagicMock()
        mock_generator.answer = fake_answer

        original_retriever = main_mod._retriever
        original_generator = main_mod._generator
        main_mod._retriever = MagicMock()
        main_mod._generator = mock_generator

        try:
            with patch_api_keys(""):
                client = TestClient(app=main_mod.app, raise_server_exceptions=False)
                response = client.post(
                    "/v1/query",
                    json={"question": "Revenue?", "ticker_filter": "AAPL", "top_k": 10},
                )
            assert response.status_code == 200
            assert captured["top_k"] == 10
            assert captured["ticker_filter"] == "AAPL"
        finally:
            main_mod._retriever = original_retriever
            main_mod._generator = original_generator

    def test_query_returns_503_when_not_initialized(self) -> None:
        from fastapi.testclient import TestClient

        import src.main as main_mod

        original_retriever = main_mod._retriever
        original_generator = main_mod._generator
        main_mod._retriever = None
        main_mod._generator = None

        try:
            with patch_api_keys(""):
                client = TestClient(app=main_mod.app, raise_server_exceptions=False)
                response = client.post(
                    "/v1/query",
                    json={"question": "What are Apple's risk factors?"},
                )
            assert response.status_code == 503
        finally:
            main_mod._retriever = original_retriever
            main_mod._generator = original_generator


# ── GET /v1/documents ────────────────────────────────────────────


class TestDocumentsEndpoint:
    def test_list_documents_success(self) -> None:
        from fastapi.testclient import TestClient

        import src.main as main_mod

        mock_retriever = MagicMock()
        mock_retriever.list_documents.return_value = (
            [
                (
                    "0001-320193-24-000123",
                    "AAPL",
                    "Apple Inc.",
                    "2024-11-01",
                    "10-K",
                    42,
                    "2024-11-02T00:00:00",
                )
            ],
            1,
        )

        original_retriever = main_mod._retriever
        original_generator = main_mod._generator
        main_mod._retriever = mock_retriever
        main_mod._generator = MagicMock()

        try:
            with patch_api_keys(""):
                client = TestClient(app=main_mod.app, raise_server_exceptions=False)
                response = client.get("/v1/documents")
            assert response.status_code == 200
            data = response.json()
            assert data["total"] == 1
            assert len(data["documents"]) == 1
            assert data["documents"][0]["ticker"] == "AAPL"
            assert data["documents"][0]["chunk_count"] == 42
        finally:
            main_mod._retriever = original_retriever
            main_mod._generator = original_generator

    def test_list_documents_with_ticker_filter(self) -> None:
        from fastapi.testclient import TestClient

        import src.main as main_mod

        mock_retriever = MagicMock()
        mock_retriever.list_documents.return_value = ([], 0)

        original_retriever = main_mod._retriever
        original_generator = main_mod._generator
        main_mod._retriever = mock_retriever
        main_mod._generator = MagicMock()

        try:
            with patch_api_keys(""):
                client = TestClient(app=main_mod.app, raise_server_exceptions=False)
                response = client.get("/v1/documents?ticker=MSFT")
            assert response.status_code == 200
            mock_retriever.list_documents.assert_called_once_with(
                ticker="MSFT", limit=20, offset=0
            )
        finally:
            main_mod._retriever = original_retriever
            main_mod._generator = original_generator

    def test_list_documents_pagination(self) -> None:
        from fastapi.testclient import TestClient

        import src.main as main_mod

        mock_retriever = MagicMock()
        mock_retriever.list_documents.return_value = ([], 0)

        original_retriever = main_mod._retriever
        original_generator = main_mod._generator
        main_mod._retriever = mock_retriever
        main_mod._generator = MagicMock()

        try:
            with patch_api_keys(""):
                client = TestClient(app=main_mod.app, raise_server_exceptions=False)
                response = client.get("/v1/documents?limit=5&offset=10")
            assert response.status_code == 200
            data = response.json()
            assert data["limit"] == 5
            assert data["offset"] == 10
            mock_retriever.list_documents.assert_called_once_with(
                ticker=None, limit=5, offset=10
            )
        finally:
            main_mod._retriever = original_retriever
            main_mod._generator = original_generator

    def test_list_documents_requires_auth(self) -> None:
        from fastapi.testclient import TestClient

        import src.main as main_mod

        mock_retriever = MagicMock()
        mock_retriever.list_documents.return_value = ([], 0)

        original_retriever = main_mod._retriever
        original_generator = main_mod._generator
        main_mod._retriever = mock_retriever
        main_mod._generator = MagicMock()

        try:
            with patch_api_keys("secret-key"):
                client = TestClient(app=main_mod.app, raise_server_exceptions=False)
                response = client.get("/v1/documents")
            assert response.status_code == 401
        finally:
            main_mod._retriever = original_retriever
            main_mod._generator = original_generator

    def test_list_documents_returns_503_when_not_initialized(self) -> None:
        from fastapi.testclient import TestClient

        import src.main as main_mod

        original_retriever = main_mod._retriever
        original_generator = main_mod._generator
        main_mod._retriever = None
        main_mod._generator = None

        try:
            with patch_api_keys(""):
                client = TestClient(app=main_mod.app, raise_server_exceptions=False)
                response = client.get("/v1/documents")
            assert response.status_code == 503
        finally:
            main_mod._retriever = original_retriever
            main_mod._generator = original_generator


# ── Helpers ──────────────────────────────────────────────────────


def patch_api_keys(keys: str):  # type: ignore[return]
    """Patch the API_KEYS environment variable for a test block."""
    from unittest.mock import patch

    return patch.dict(os.environ, {"API_KEYS": keys}, clear=False)
