"""Unit tests for the EDGAR client, config, and Kafka producer.

All HTTP calls are mocked — no real network traffic.
Covers: search, fetch, skip-on-failure (FR-5), Filing dataclass,
        config loading, and Kafka producer serialisation.
"""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from typing import Any, AsyncGenerator
from unittest.mock import MagicMock, patch

import pytest

from src.config import Settings, load_tickers
from src.edgar_client import EdgarClient, Filing


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

        async def _text(**kwargs: Any) -> str:
            return text_data

        mock_resp.text = _text

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
            yield  # noqa: unreachable  # type: ignore[misc]

        mock_session = MagicMock()
        mock_session.get = _raise_ctx

        result = await client.search_10k_filings("AAPL", mock_session)
        assert result == []


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
            yield  # noqa: unreachable  # type: ignore[misc]

        mock_session = MagicMock()
        mock_session.get = _raise_ctx

        result = await client.fetch_filing_text("https://example.com/f", mock_session)
        assert result is None


# ── EdgarClient.get_filings_for_ticker ───────────────────────────

class TestGetFilingsForTicker:
    """Tests for the end-to-end per-ticker method."""

    @pytest.mark.asyncio
    async def test_skips_hits_without_accession(self, client: EdgarClient) -> None:
        """Hits missing accession_no should be skipped (FR-5)."""
        with patch.object(client, "search_10k_filings", return_value=[
            {"_source": {"file_date": "2024-01-01", "file_url": "https://sec.gov/x"}},
        ]):
            result = await client.get_filings_for_ticker("AAPL", "Apple Inc.", MagicMock())
        assert result == []

    @pytest.mark.asyncio
    async def test_skips_hits_without_url(self, client: EdgarClient) -> None:
        """Hits missing file_url should be skipped."""
        with patch.object(client, "search_10k_filings", return_value=[
            {"_source": {"accession_no": "0001", "file_date": "2024-01-01"}},
        ]):
            result = await client.get_filings_for_ticker("AAPL", "Apple Inc.", MagicMock())
        assert result == []

    @pytest.mark.asyncio
    async def test_skips_when_fetch_returns_none(self, client: EdgarClient) -> None:
        """If fetch_filing_text returns None, the filing is skipped."""
        with patch.object(client, "search_10k_filings", return_value=[
            {"_source": {"accession_no": "0001", "file_date": "2024-01-01", "file_url": "https://sec.gov/x"}},
        ]), patch.object(client, "fetch_filing_text", return_value=None):
            result = await client.get_filings_for_ticker("AAPL", "Apple Inc.", MagicMock())
        assert result == []

    @pytest.mark.asyncio
    async def test_returns_filing_on_success(self, client: EdgarClient) -> None:
        """Happy path: a hit with all fields produces a Filing."""
        with patch.object(client, "search_10k_filings", return_value=[
            {"_source": {
                "accession_no": "0001-24-000001",
                "file_date": "2024-11-01",
                "file_url": "https://sec.gov/filing.htm",
            }},
        ]), patch.object(client, "fetch_filing_text", return_value="Item 1. Business..."):
            result = await client.get_filings_for_ticker("AAPL", "Apple Inc.", MagicMock())
        assert len(result) == 1
        assert result[0].ticker == "AAPL"
        assert result[0].accession_number == "0001-24-000001"
        assert result[0].raw_text == "Item 1. Business..."


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
        assert s.postgres_db == "findocrag"
        assert s.kafka_bootstrap_servers == "kafka:9092"
        assert s.edgar_rate_limit_rps == 10

    def test_postgres_dsn(self) -> None:
        s = Settings()
        assert s.postgres_dsn.startswith("postgresql://")
        assert "findocrag" in s.postgres_dsn

    def test_load_tickers_missing_file(self) -> None:
        result = load_tickers("/nonexistent/path.yml")
        assert result == []

    def test_load_tickers_valid_file(self, tmp_path: Any) -> None:
        f = tmp_path / "tickers.yml"
        f.write_text('tickers:\n  - symbol: "AAPL"\n    name: "Apple Inc."\n')
        result = load_tickers(str(f))
        assert len(result) == 1
        assert result[0]["symbol"] == "AAPL"


# ── Kafka Producer ───────────────────────────────────────────────

class TestKafkaProducer:
    """Tests for kafka_producer.py (serialisation logic)."""

    def test_publish_filing_calls_produce(self) -> None:
        from src.kafka_producer import FilingProducer

        with patch("src.kafka_producer.Producer") as MockProducer:
            mock_instance = MagicMock()
            MockProducer.return_value = mock_instance

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

    def test_flush_delegates_to_producer(self) -> None:
        from src.kafka_producer import FilingProducer

        with patch("src.kafka_producer.Producer") as MockProducer:
            mock_instance = MagicMock()
            mock_instance.flush.return_value = 0
            MockProducer.return_value = mock_instance

            producer = FilingProducer(bootstrap_servers="localhost:9092")
            remaining = producer.flush()

            assert remaining == 0
            mock_instance.flush.assert_called_once_with(10.0)