"""Unit tests for the EDGAR client, config, Kafka producer, and FastAPI app.

All HTTP calls are mocked — no real network traffic.
Covers: search, fetch, skip-on-failure (FR-5), Filing dataclass,
        config loading, Kafka producer serialisation, and FastAPI endpoints.
"""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest

from src.config import Settings, load_tickers
from src.edgar_client import EdgarClient, Filing

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

# ── Helpers ──────────────────────────────────────────────────────


def _make_async_response(
    *,
    json_data: dict[str, Any] | None = None,
    text_data: str | None = None,
    raise_on_status: Exception | None = None,
) -> MagicMock:
    """Create a mock that works as an ``async with session.get(...) as resp:`` target."""

    mock_resp = MagicMock()

    if raise_on_status:
        mock_resp.raise_for_status.side_effect = raise_on_status
    else:
        mock_resp.raise_for_status = MagicMock()

    if json_data is not None:

        async def _json(**kwargs: Any) -> dict[str, Any]:
            return json_data

        mock_resp.json = _json

    if text_data is not None:
        text_bytes = text_data.encode("utf-8")

        async def _iter_chunked(size: int):  # type: ignore[no-untyped-def]
            yield text_bytes

        mock_content = MagicMock()
        mock_content.iter_chunked = _iter_chunked
        mock_resp.content = mock_content
        mock_resp.content_length = len(text_bytes)

    @asynccontextmanager
    async def _ctx_manager(*args: Any, **kwargs: Any) -> AsyncGenerator[MagicMock, None]:
        yield mock_resp

    return mock_resp, _ctx_manager


@pytest.fixture
def client() -> EdgarClient:
    return EdgarClient(user_agent="TestAgent test@example.com", rate_limit_rps=100, max_retries=1)


# ── EdgarClient.search_10k_filings ──────────────────────────────


class TestSearchFilings:
    """Tests for EdgarClient.search_10k_filings."""

    @pytest.mark.asyncio
    async def test_returns_empty_on_http_error(self, client: EdgarClient) -> None:
        """If EDGAR returns an HTTP error, search returns [] (FR-5)."""
        import aiohttp

        _, ctx = _make_async_response(raise_on_status=aiohttp.ClientResponseError(
            request_info=MagicMock(), history=(), status=500, message="Server Error"
        ))

        mock_session = MagicMock()
        mock_session.get = ctx

        result = await client.search_10k_filings("AAPL", mock_session)
        assert result == []

    @pytest.mark.asyncio
    async def test_parses_hits_correctly(self, client: EdgarClient) -> None:
        """Verify that search extracts the hits array from EDGAR JSON."""
        edgar_response = {
            "hits": {
                "hits": [
                    {"_source": {"accession_no": "0001-24-000001", "file_date": "2024-11-01"}},
                    {"_source": {"accession_no": "0001-24-000002", "file_date": "2024-11-02"}},
                ]
            }
        }

        _, ctx = _make_async_response(json_data=edgar_response)
        mock_session = MagicMock()
        mock_session.get = ctx

        result = await client.search_10k_filings("AAPL", mock_session)
        assert len(result) == 2
        assert result[0]["_source"]["accession_no"] == "0001-24-000001"

    @pytest.mark.asyncio
    async def test_returns_empty_on_unexpected_exception(self, client: EdgarClient) -> None:
        """Non-aiohttp exceptions are caught and return []."""

        @asynccontextmanager
        async def _raise_ctx(*a: Any, **kw: Any) -> AsyncGenerator[None, None]:
            raise ValueError("unexpected")
            yield  # noqa: RET504  # type: ignore[misc]

        mock_session = MagicMock()
        mock_session.get = _raise_ctx

        result = await client.search_10k_filings("AAPL", mock_session)
        assert result == []

    @pytest.mark.asyncio
    async def test_returns_empty_on_missing_hits(self, client: EdgarClient) -> None:
        """If response has no 'hits' key, return empty list."""
        _, ctx = _make_async_response(json_data={})
        mock_session = MagicMock()
        mock_session.get = ctx

        result = await client.search_10k_filings("AAPL", mock_session)
        assert result == []


# ── EdgarClient retry / backoff (search) ─────────────────────────


class TestSearchFilingsRetry:
    """Verify exponential backoff in search_10k_filings (max_retries=3)."""

    @pytest.mark.asyncio
    async def test_retries_on_client_error_then_succeeds(self) -> None:
        """Fails twice, succeeds on the third attempt; sleep called twice."""
        import aiohttp

        client = EdgarClient(user_agent="Test", rate_limit_rps=100, max_retries=3)
        error = aiohttp.ClientResponseError(
            request_info=MagicMock(), history=(), status=503, message="Unavailable"
        )
        edgar_response = {"hits": {"hits": [{"_source": {"adsh": "0001", "file_date": "2024"}}]}}

        call_count = 0

        @asynccontextmanager
        async def _flaky_ctx(*a: Any, **kw: Any):  # type: ignore[misc]
            nonlocal call_count
            call_count += 1
            mock_resp = MagicMock()
            if call_count < 3:
                mock_resp.raise_for_status.side_effect = error
            else:
                mock_resp.raise_for_status = MagicMock()

                async def _json(**kwargs: Any) -> dict[str, Any]:
                    return edgar_response

                mock_resp.json = _json
            yield mock_resp

        mock_session = MagicMock()
        mock_session.get = _flaky_ctx

        with patch("src.edgar_client.asyncio.sleep") as mock_sleep:
            result = await client.search_10k_filings("AAPL", mock_session)

        assert len(result) == 1
        assert call_count == 3
        # Two backoff sleeps (2 s, 4 s) plus one sub-second rate-limit sleep on success.
        # Filter on >= 1 s to isolate the exponential-backoff sleeps.
        backoff_args = [c.args[0] for c in mock_sleep.call_args_list if c.args[0] >= 1]
        assert backoff_args == [2, 4]

    @pytest.mark.asyncio
    async def test_returns_empty_after_all_retries_exhausted(self) -> None:
        """All attempts fail — returns [] without raising; sleep called max_retries-1 times."""
        import aiohttp

        client = EdgarClient(user_agent="Test", rate_limit_rps=100, max_retries=3)
        error = aiohttp.ClientResponseError(
            request_info=MagicMock(), history=(), status=503, message="Unavailable"
        )

        @asynccontextmanager
        async def _always_fail(*a: Any, **kw: Any):  # type: ignore[misc]
            mock_resp = MagicMock()
            mock_resp.raise_for_status.side_effect = error
            yield mock_resp

        mock_session = MagicMock()
        mock_session.get = _always_fail

        with patch("src.edgar_client.asyncio.sleep") as mock_sleep:
            result = await client.search_10k_filings("AAPL", mock_session)

        assert result == []
        # 3 attempts → 2 sleeps (no sleep after the final failure)
        assert mock_sleep.call_count == 2
        sleep_args = [c.args[0] for c in mock_sleep.call_args_list]
        assert sleep_args == [2, 4]

    @pytest.mark.asyncio
    async def test_backoff_values_follow_exponential_schedule(self) -> None:
        """Verify exact sleep durations: 2^1, 2^2 seconds for max_retries=3."""
        import aiohttp

        client = EdgarClient(user_agent="Test", rate_limit_rps=100, max_retries=3)
        error = aiohttp.ClientResponseError(
            request_info=MagicMock(), history=(), status=500, message="Error"
        )

        @asynccontextmanager
        async def _fail(*a: Any, **kw: Any):  # type: ignore[misc]
            mock_resp = MagicMock()
            mock_resp.raise_for_status.side_effect = error
            yield mock_resp

        mock_session = MagicMock()
        mock_session.get = _fail

        with patch("src.edgar_client.asyncio.sleep") as mock_sleep:
            await client.search_10k_filings("AAPL", mock_session)

        assert mock_sleep.call_args_list == [call(2), call(4)]


# ── EdgarClient retry / backoff (fetch) ──────────────────────────


class TestFetchFilingTextRetry:
    """Verify exponential backoff in fetch_filing_text (max_retries=3)."""

    @pytest.mark.asyncio
    async def test_retries_on_client_error_then_succeeds(self) -> None:
        """Fails twice, succeeds on the third attempt; sleep called twice."""
        import aiohttp

        client = EdgarClient(user_agent="Test", rate_limit_rps=100, max_retries=3)
        error = aiohttp.ClientResponseError(
            request_info=MagicMock(), history=(), status=503, message="Unavailable"
        )
        text_bytes = b"Item 1. Business..."

        call_count = 0

        @asynccontextmanager
        async def _flaky_ctx(*a: Any, **kw: Any):  # type: ignore[misc]
            nonlocal call_count
            call_count += 1
            mock_resp = MagicMock()
            if call_count < 3:
                mock_resp.raise_for_status.side_effect = error
            else:
                mock_resp.raise_for_status = MagicMock()
                mock_resp.content_length = len(text_bytes)

                async def _iter_chunked(size: int):  # type: ignore[no-untyped-def]
                    yield text_bytes

                mock_content = MagicMock()
                mock_content.iter_chunked = _iter_chunked
                mock_resp.content = mock_content
            yield mock_resp

        mock_session = MagicMock()
        mock_session.get = _flaky_ctx

        with patch("src.edgar_client.asyncio.sleep") as mock_sleep:
            result = await client.fetch_filing_text("https://example.com/f", mock_session)

        assert result == "Item 1. Business..."
        assert call_count == 3
        # Two backoff sleeps (2 s, 4 s) plus one sub-second rate-limit sleep on success.
        backoff_args = [c.args[0] for c in mock_sleep.call_args_list if c.args[0] >= 1]
        assert backoff_args == [2, 4]

    @pytest.mark.asyncio
    async def test_returns_none_after_all_retries_exhausted(self) -> None:
        """All attempts fail — returns None; sleep called max_retries-1 times."""
        import aiohttp

        client = EdgarClient(user_agent="Test", rate_limit_rps=100, max_retries=3)
        error = aiohttp.ClientResponseError(
            request_info=MagicMock(), history=(), status=503, message="Unavailable"
        )

        @asynccontextmanager
        async def _always_fail(*a: Any, **kw: Any):  # type: ignore[misc]
            mock_resp = MagicMock()
            mock_resp.raise_for_status.side_effect = error
            yield mock_resp

        mock_session = MagicMock()
        mock_session.get = _always_fail

        with patch("src.edgar_client.asyncio.sleep") as mock_sleep:
            result = await client.fetch_filing_text("https://example.com/f", mock_session)

        assert result is None
        assert mock_sleep.call_count == 2
        sleep_args = [c.args[0] for c in mock_sleep.call_args_list]
        assert sleep_args == [2, 4]


# ── EdgarClient.fetch_filing_text ────────────────────────────────


class TestFetchFilingText:
    """Tests for EdgarClient.fetch_filing_text."""

    @pytest.mark.asyncio
    async def test_returns_none_on_error(self, client: EdgarClient) -> None:
        """If fetch fails, return None instead of raising (FR-5)."""
        import aiohttp

        _, ctx = _make_async_response(raise_on_status=aiohttp.ClientResponseError(
            request_info=MagicMock(), history=(), status=404, message="Not Found"
        ))
        mock_session = MagicMock()
        mock_session.get = ctx

        result = await client.fetch_filing_text("https://example.com/filing", mock_session)
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_text_on_success(self, client: EdgarClient) -> None:
        """On success, return the response body as a string."""
        _, ctx = _make_async_response(text_data="Item 1. Business...")
        mock_session = MagicMock()
        mock_session.get = ctx

        result = await client.fetch_filing_text("https://example.com/filing", mock_session)
        assert result == "Item 1. Business..."

    @pytest.mark.asyncio
    async def test_returns_none_on_unexpected_exception(self, client: EdgarClient) -> None:
        """Non-aiohttp exceptions are caught and return None."""

        @asynccontextmanager
        async def _raise_ctx(*a: Any, **kw: Any) -> AsyncGenerator[None, None]:
            raise RuntimeError("boom")
            yield  # noqa: RET504  # type: ignore[misc]

        mock_session = MagicMock()
        mock_session.get = _raise_ctx

        result = await client.fetch_filing_text("https://example.com/f", mock_session)
        assert result is None


# ── EdgarClient.get_filings_for_ticker ───────────────────────────


class TestGetFilingsForTicker:
    """Tests for the end-to-end per-ticker method.

    NOTE: The real edgar_client.py uses the EDGAR field names:
      - 'adsh' for accession number
      - 'ciks' (list) for CIK
    The search_10k_filings mock must return dicts matching this schema.
    """

    @pytest.mark.asyncio
    async def test_skips_hits_without_accession(self, client: EdgarClient) -> None:
        """Hits missing 'adsh' should be skipped (FR-5)."""
        with patch.object(client, "search_10k_filings", return_value=[
            {"_source": {"file_date": "2024-01-01", "ciks": ["1234567890"]}},
        ]):
            result = await client.get_filings_for_ticker("AAPL", "Apple Inc.", MagicMock())
        assert result == []

    @pytest.mark.asyncio
    async def test_skips_hits_without_cik(self, client: EdgarClient) -> None:
        """Hits with 'adsh' but missing/empty 'ciks' should be skipped."""
        with patch.object(client, "search_10k_filings", return_value=[
            {"_source": {"adsh": "0001-24-000001", "file_date": "2024-01-01", "ciks": []}},
        ]):
            result = await client.get_filings_for_ticker("AAPL", "Apple Inc.", MagicMock())
        assert result == []

    @pytest.mark.asyncio
    async def test_skips_when_fetch_returns_none(self, client: EdgarClient) -> None:
        """If fetch_filing_text returns None, the filing is skipped."""
        with patch.object(client, "search_10k_filings", return_value=[
            {"_source": {"adsh": "0001-24-000001", "file_date": "2024-01-01", "ciks": ["1234567890"]}},
        ]), patch.object(client, "fetch_filing_text", return_value=None):
            result = await client.get_filings_for_ticker("AAPL", "Apple Inc.", MagicMock())
        assert result == []

    @pytest.mark.asyncio
    async def test_returns_filing_on_success(self, client: EdgarClient) -> None:
        """Happy path: a hit with adsh + ciks produces a Filing."""
        with patch.object(client, "search_10k_filings", return_value=[
            {"_source": {
                "adsh": "0001-24-000001",
                "file_date": "2024-11-01",
                "ciks": ["1234567890"],
            }},
        ]), patch.object(client, "fetch_filing_text", return_value="Item 1. Business..."):
            result = await client.get_filings_for_ticker("AAPL", "Apple Inc.", MagicMock())
        assert len(result) == 1
        assert result[0].ticker == "AAPL"
        assert result[0].accession_number == "0001-24-000001"
        assert result[0].raw_text == "Item 1. Business..."
        assert result[0].company_name == "Apple Inc."
        assert result[0].filing_type == "10-K"
        assert "sec.gov" in result[0].source_url

    @pytest.mark.asyncio
    async def test_constructs_correct_url(self, client: EdgarClient) -> None:
        """Verify the SEC archive URL is constructed correctly from adsh + CIK."""
        with patch.object(client, "search_10k_filings", return_value=[
            {"_source": {
                "adsh": "0001-24-000001",
                "file_date": "2024-11-01",
                "ciks": ["1234567890"],
            }},
        ]), patch.object(client, "fetch_filing_text", return_value="text") as mock_fetch:
            await client.get_filings_for_ticker("AAPL", "Apple Inc.", MagicMock())

        # Verify the URL passed to fetch_filing_text
        call_args = mock_fetch.call_args
        url = call_args[0][0]
        assert "1234567890" in url
        assert "000124000001" in url  # dashes removed from accession
        assert url.endswith("0001-24-000001.txt")

    @pytest.mark.asyncio
    async def test_returns_empty_when_search_returns_empty(self, client: EdgarClient) -> None:
        """If search returns no hits, get_filings_for_ticker returns []."""
        with patch.object(client, "search_10k_filings", return_value=[]):
            result = await client.get_filings_for_ticker("AAPL", "Apple Inc.", MagicMock())
        assert result == []

    @pytest.mark.asyncio
    async def test_multiple_filings(self, client: EdgarClient) -> None:
        """Multiple hits with valid data produce multiple Filings."""
        with patch.object(client, "search_10k_filings", return_value=[
            {"_source": {"adsh": "0001-24-000001", "file_date": "2024-11-01", "ciks": ["123"]}},
            {"_source": {"adsh": "0001-24-000002", "file_date": "2024-11-02", "ciks": ["123"]}},
        ]), patch.object(client, "fetch_filing_text", return_value="filing text"):
            result = await client.get_filings_for_ticker("AAPL", "Apple Inc.", MagicMock())
        assert len(result) == 2
        assert result[0].accession_number == "0001-24-000001"
        assert result[1].accession_number == "0001-24-000002"


# ── Filing dataclass ─────────────────────────────────────────────


class TestFiling:
    """Tests for the Filing dataclass."""

    def test_stores_all_fields(self) -> None:
        f = Filing(
            accession_number="0001-24-000001",
            ticker="AAPL",
            company_name="Apple Inc.",
            filing_date="2024-11-01",
            filing_type="10-K",
            source_url="https://sec.gov/...",
            raw_text="Item 1. Business...",
        )
        assert f.ticker == "AAPL"
        assert f.filing_type == "10-K"
        assert f.accession_number == "0001-24-000001"
        assert len(f.raw_text) > 0


# ── Config ───────────────────────────────────────────────────────


class TestConfig:
    """Tests for config.py."""

    def test_settings_defaults(self) -> None:
        s = Settings()
        assert s.postgres_db == "findocdrag"
        assert s.kafka_bootstrap_servers == "kafka:9092"
        assert s.edgar_rate_limit_rps == 10

    def test_postgres_dsn(self) -> None:
        s = Settings()
        assert s.postgres_dsn.startswith("postgresql://")
        assert "findocdrag" in s.postgres_dsn

    def test_settings_all_defaults(self) -> None:
        s = Settings()
        assert s.postgres_host == "postgres"
        assert s.postgres_port == 5432
        assert s.postgres_user == "findocdrag"
        assert s.postgres_password == "changeme"
        assert s.edgar_user_agent == "FinDocDRAG findocdrag@example.com"
        assert s.log_level == "INFO"
        assert s.tickers_config_path == "config/tickers.yml"

    def test_load_tickers_missing_file(self) -> None:
        result = load_tickers("/nonexistent/path.yml")
        assert result == []

    def test_load_tickers_valid_file(self, tmp_path: Any) -> None:
        f = tmp_path / "tickers.yml"
        f.write_text('tickers:\n  - symbol: "AAPL"\n    name: "Apple Inc."\n')
        result = load_tickers(str(f))
        assert len(result) == 1
        assert result[0]["symbol"] == "AAPL"

    def test_load_tickers_empty_file(self, tmp_path: Any) -> None:
        f = tmp_path / "tickers.yml"
        f.write_text("{}")
        result = load_tickers(str(f))
        assert result == []


# ── Kafka Producer ───────────────────────────────────────────────


class TestKafkaProducer:
    """Tests for kafka_producer.py (serialisation logic)."""

    def test_publish_filing_calls_produce(self) -> None:
        from src.kafka_producer import FilingProducer

        with patch("src.kafka_producer.Producer") as mock_producer:
            mock_instance = MagicMock()
            mock_producer.return_value = mock_instance

            producer = FilingProducer(bootstrap_servers="localhost:9092")

            filing = Filing(
                accession_number="0001-24-000001",
                ticker="AAPL",
                company_name="Apple Inc.",
                filing_date="2024-11-01",
                filing_type="10-K",
                source_url="https://sec.gov/...",
                raw_text="Item 1. Business...",
            )
            producer.publish_filing(filing)

            mock_instance.produce.assert_called_once()
            call_kwargs = mock_instance.produce.call_args
            assert call_kwargs.kwargs["topic"] == "filings.raw"
            assert call_kwargs.kwargs["key"] == "0001-24-000001"

            # Verify the serialised message has all required fields
            msg = json.loads(call_kwargs.kwargs["value"])
            assert msg["ticker"] == "AAPL"
            assert msg["filing_type"] == "10-K"
            assert "published_at" in msg
            assert msg["company_name"] == "Apple Inc."
            assert msg["raw_text"] == "Item 1. Business..."
            assert msg["source_url"] == "https://sec.gov/..."
            assert msg["accession_number"] == "0001-24-000001"

    def test_flush_delegates_to_producer(self) -> None:
        from src.kafka_producer import FilingProducer

        with patch("src.kafka_producer.Producer") as mock_producer:
            mock_instance = MagicMock()
            mock_instance.flush.return_value = 0
            mock_producer.return_value = mock_instance

            producer = FilingProducer(bootstrap_servers="localhost:9092")
            remaining = producer.flush()

            assert remaining == 0
            mock_instance.flush.assert_called_once_with(10.0)

    def test_delivery_callback_on_error(self) -> None:
        """Test _delivery_callback logs error on failure."""
        from src.kafka_producer import _delivery_callback

        mock_msg = MagicMock()
        mock_msg.topic.return_value = "filings.raw"
        mock_err = MagicMock()
        mock_err.__str__ = lambda self: "Broker unavailable"

        # Should not raise
        _delivery_callback(mock_err, mock_msg)

    def test_delivery_callback_on_success(self) -> None:
        """Test _delivery_callback logs success on delivery."""
        from src.kafka_producer import _delivery_callback

        mock_msg = MagicMock()
        mock_msg.topic.return_value = "filings.raw"
        mock_msg.partition.return_value = 0
        mock_msg.offset.return_value = 42

        # Should not raise
        _delivery_callback(None, mock_msg)


# ── FastAPI App (main.py) ────────────────────────────────────────


class TestFastAPIApp:
    """Tests for the FastAPI endpoints in main.py.

    Uses FastAPI TestClient to test endpoints without a running server.
    """

    def test_health_endpoint(self) -> None:
        """GET /health returns healthy when DB is reachable."""
        from fastapi.testclient import TestClient

        import src.main as main_module

        mock_db = MagicMock()
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_conn.cursor.return_value = mock_cur
        mock_db._get_conn.return_value = mock_conn

        original_db = main_module._db
        main_module._db = mock_db

        try:
            client = TestClient(app=main_module.app, raise_server_exceptions=False)
            response = client.get("/health")
            assert response.status_code == 200
            assert response.json() == {"status": "healthy"}
            assert "X-Request-ID" in response.headers
        finally:
            main_module._db = original_db

    def test_health_endpoint_not_initialized(self) -> None:
        """GET /health returns 503 when not initialized."""
        from fastapi.testclient import TestClient

        import src.main as main_module

        original_db = main_module._db
        main_module._db = None

        try:
            client = TestClient(app=main_module.app, raise_server_exceptions=False)
            response = client.get("/health")
            assert response.status_code == 503
        finally:
            main_module._db = original_db

    def test_ready_endpoint_not_initialized(self) -> None:
        """GET /ready returns 503 when service is not initialized."""
        from fastapi.testclient import TestClient

        import src.main as main_module

        # Ensure globals are None
        original_edgar = main_module._edgar_client
        original_kafka = main_module._kafka_producer
        main_module._edgar_client = None
        main_module._kafka_producer = None

        try:
            client = TestClient(app=main_module.app, raise_server_exceptions=False)
            response = client.get("/ready")
            assert response.status_code == 503
        finally:
            main_module._edgar_client = original_edgar
            main_module._kafka_producer = original_kafka

    def test_ready_endpoint_initialized(self) -> None:
        """GET /ready returns ready when all dependencies are set."""
        from fastapi.testclient import TestClient

        import src.main as main_module

        original_edgar = main_module._edgar_client
        original_kafka = main_module._kafka_producer
        original_db = main_module._db
        main_module._edgar_client = MagicMock()
        main_module._kafka_producer = MagicMock()
        main_module._db = MagicMock()

        try:
            client = TestClient(app=main_module.app, raise_server_exceptions=False)
            response = client.get("/ready")
            assert response.status_code == 200
            assert response.json() == {"status": "ready"}
        finally:
            main_module._edgar_client = original_edgar
            main_module._kafka_producer = original_kafka
            main_module._db = original_db

    def test_ingest_not_initialized(self) -> None:
        """POST /v1/ingest returns 503 when not initialized."""
        from fastapi.testclient import TestClient

        import src.main as main_module

        original_edgar = main_module._edgar_client
        original_kafka = main_module._kafka_producer
        main_module._edgar_client = None
        main_module._kafka_producer = None

        try:
            client = TestClient(app=main_module.app, raise_server_exceptions=False)
            response = client.post("/v1/ingest", json={"tickers": ["AAPL"]})
            assert response.status_code == 503
        finally:
            main_module._edgar_client = original_edgar
            main_module._kafka_producer = original_kafka

    def test_ingest_no_tickers_and_no_config(self) -> None:
        """POST /v1/ingest with no tickers and no config file returns 400."""
        from fastapi.testclient import TestClient

        import src.main as main_module

        original_edgar = main_module._edgar_client
        original_kafka = main_module._kafka_producer
        original_db = main_module._db
        main_module._edgar_client = MagicMock()
        main_module._kafka_producer = MagicMock()
        main_module._db = MagicMock()

        try:
            with patch("src.main.load_tickers", return_value=[]):
                client = TestClient(app=main_module.app, raise_server_exceptions=False)
                response = client.post("/v1/ingest", json={})
                assert response.status_code == 400
        finally:
            main_module._edgar_client = original_edgar
            main_module._kafka_producer = original_kafka
            main_module._db = original_db

    def test_ingest_success_with_tickers(self) -> None:
        """POST /v1/ingest publishes new filings and records them in ingestion_log."""
        from fastapi.testclient import TestClient

        import src.main as main_module

        mock_edgar = MagicMock()
        mock_kafka = MagicMock()
        mock_db = MagicMock()

        filing = Filing(
            accession_number="0001-24-000001",
            ticker="AAPL",
            company_name="AAPL",
            filing_date="2024-11-01",
            filing_type="10-K",
            source_url="https://sec.gov/...",
            raw_text="Item 1...",
        )

        mock_edgar.get_filings_for_ticker = AsyncMock(return_value=[filing])
        mock_kafka.publish_filing = MagicMock()
        mock_kafka.flush = MagicMock()
        mock_db.is_already_ingested.return_value = False

        original_edgar = main_module._edgar_client
        original_kafka = main_module._kafka_producer
        original_db = main_module._db
        main_module._edgar_client = mock_edgar
        main_module._kafka_producer = mock_kafka
        main_module._db = mock_db

        try:
            client = TestClient(app=main_module.app, raise_server_exceptions=False)
            response = client.post("/v1/ingest", json={"tickers": ["AAPL"]})
            assert response.status_code == 200
            data = response.json()
            assert data["status"] == "completed"
            assert data["tickers_processed"] == ["AAPL"]
            assert data["filings_published"] == 1
            assert data["filings_skipped"] == 0
            assert data["errors"] == []
            mock_kafka.publish_filing.assert_called_once_with(filing)
            mock_db.record_ingestion.assert_called_once_with(filing)
        finally:
            main_module._edgar_client = original_edgar
            main_module._kafka_producer = original_kafka
            main_module._db = original_db

    def test_ingest_handles_ticker_error(self) -> None:
        """POST /v1/ingest captures per-ticker errors without crashing."""
        from fastapi.testclient import TestClient

        import src.main as main_module

        mock_edgar = MagicMock()
        mock_kafka = MagicMock()
        mock_db = MagicMock()

        mock_edgar.get_filings_for_ticker = AsyncMock(side_effect=RuntimeError("EDGAR down"))
        mock_kafka.flush = MagicMock()

        original_edgar = main_module._edgar_client
        original_kafka = main_module._kafka_producer
        original_db = main_module._db
        main_module._edgar_client = mock_edgar
        main_module._kafka_producer = mock_kafka
        main_module._db = mock_db

        try:
            client = TestClient(app=main_module.app, raise_server_exceptions=False)
            response = client.post("/v1/ingest", json={"tickers": ["AAPL"]})
            assert response.status_code == 200
            data = response.json()
            assert data["status"] == "completed"
            assert len(data["errors"]) == 1
            assert "AAPL" in data["errors"][0]
            assert data["filings_published"] == 0
        finally:
            main_module._edgar_client = original_edgar
            main_module._kafka_producer = original_kafka
            main_module._db = original_db

    def test_ingest_with_config_file_tickers(self) -> None:
        """POST /v1/ingest falls back to config/tickers.yml when no tickers in body."""
        from fastapi.testclient import TestClient

        import src.main as main_module

        mock_edgar = MagicMock()
        mock_kafka = MagicMock()
        mock_db = MagicMock()
        mock_edgar.get_filings_for_ticker = AsyncMock(return_value=[])
        mock_kafka.flush = MagicMock()

        original_edgar = main_module._edgar_client
        original_kafka = main_module._kafka_producer
        original_db = main_module._db
        main_module._edgar_client = mock_edgar
        main_module._kafka_producer = mock_kafka
        main_module._db = mock_db

        try:
            with patch("src.main.load_tickers", return_value=[
                {"symbol": "MSFT", "name": "Microsoft Corp."},
            ]):
                client = TestClient(app=main_module.app, raise_server_exceptions=False)
                response = client.post("/v1/ingest")
                assert response.status_code == 200
                data = response.json()
                assert data["tickers_processed"] == ["MSFT"]
        finally:
            main_module._edgar_client = original_edgar
            main_module._kafka_producer = original_kafka
            main_module._db = original_db

    def test_ingest_skips_already_ingested_filing(self) -> None:
        """POST /v1/ingest skips filings already present in ingestion_log (FR-4)."""
        from fastapi.testclient import TestClient

        import src.main as main_module

        mock_edgar = MagicMock()
        mock_kafka = MagicMock()
        mock_db = MagicMock()

        filing = Filing(
            accession_number="0001-24-000001",
            ticker="AAPL",
            company_name="AAPL",
            filing_date="2024-11-01",
            filing_type="10-K",
            source_url="https://sec.gov/...",
            raw_text="Item 1...",
        )

        mock_edgar.get_filings_for_ticker = AsyncMock(return_value=[filing])
        mock_kafka.flush = MagicMock()
        # Simulate filing already present in ingestion_log
        mock_db.is_already_ingested.return_value = True

        original_edgar = main_module._edgar_client
        original_kafka = main_module._kafka_producer
        original_db = main_module._db
        main_module._edgar_client = mock_edgar
        main_module._kafka_producer = mock_kafka
        main_module._db = mock_db

        try:
            client = TestClient(app=main_module.app, raise_server_exceptions=False)
            response = client.post("/v1/ingest", json={"tickers": ["AAPL"]})
            assert response.status_code == 200
            data = response.json()
            assert data["filings_published"] == 0
            assert data["filings_skipped"] == 1
            # Kafka and DB record must NOT be called for duplicate filings
            mock_kafka.publish_filing.assert_not_called()
            mock_db.record_ingestion.assert_not_called()
        finally:
            main_module._edgar_client = original_edgar
            main_module._kafka_producer = original_kafka
            main_module._db = original_db

    def test_ingest_mixed_new_and_duplicate_filings(self) -> None:
        """POST /v1/ingest publishes new filings and skips duplicates in the same run."""
        from fastapi.testclient import TestClient

        import src.main as main_module

        mock_edgar = MagicMock()
        mock_kafka = MagicMock()
        mock_db = MagicMock()

        new_filing = Filing(
            accession_number="0001-24-000001",
            ticker="AAPL",
            company_name="AAPL",
            filing_date="2024-11-01",
            filing_type="10-K",
            source_url="https://sec.gov/1",
            raw_text="Item 1...",
        )
        dup_filing = Filing(
            accession_number="0001-23-000001",
            ticker="AAPL",
            company_name="AAPL",
            filing_date="2023-11-01",
            filing_type="10-K",
            source_url="https://sec.gov/2",
            raw_text="Item 1 old...",
        )

        mock_edgar.get_filings_for_ticker = AsyncMock(return_value=[new_filing, dup_filing])
        mock_kafka.flush = MagicMock()
        # new_filing is new, dup_filing is already ingested
        mock_db.is_already_ingested.side_effect = lambda acc: acc == dup_filing.accession_number

        original_edgar = main_module._edgar_client
        original_kafka = main_module._kafka_producer
        original_db = main_module._db
        main_module._edgar_client = mock_edgar
        main_module._kafka_producer = mock_kafka
        main_module._db = mock_db

        try:
            client = TestClient(app=main_module.app, raise_server_exceptions=False)
            response = client.post("/v1/ingest", json={"tickers": ["AAPL"]})
            assert response.status_code == 200
            data = response.json()
            assert data["filings_published"] == 1
            assert data["filings_skipped"] == 1
            mock_kafka.publish_filing.assert_called_once_with(new_filing)
            mock_db.record_ingestion.assert_called_once_with(new_filing)
        finally:
            main_module._edgar_client = original_edgar
            main_module._kafka_producer = original_kafka
            main_module._db = original_db
