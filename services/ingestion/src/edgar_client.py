"""SEC EDGAR EFTS client for fetching 10-K filings.

Uses the EDGAR full-text search API to find filings, then fetches
the filing document text from the EDGAR archives.

References:
  - TDD: FR-1 (fetch 10-K filings from EFTS API by ticker)
  - TDD: FR-5 (log and skip filings that fail to parse)
  - TDD: NFR-4 (respect SEC rate limit of 10 req/s)
  - TDD: Section 8.1.1 (EDGAR request duration histogram)
  - API docs: https://efts.sec.gov/LATEST/search-index
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any

import aiohttp
import structlog

from src.metrics import EDGAR_REQUEST_DURATION, FILINGS_FETCHED_TOTAL

logger = structlog.get_logger()

# EDGAR full-text search endpoint
EDGAR_SEARCH_URL = "https://efts.sec.gov/LATEST/search-index"

# Log a download-progress line every this many bytes
_PROGRESS_LOG_INTERVAL = 5 * 1024 * 1024  # 5 MB


@dataclass
class Filing:
    """Represents a single 10-K filing fetched from EDGAR."""

    accession_number: str
    ticker: str
    company_name: str
    filing_date: str
    filing_type: str
    source_url: str
    raw_text: str


@dataclass
class EdgarClient:
    """Async client for SEC EDGAR EFTS full-text search API.

    Rate-limited via an asyncio.Semaphore to respect SEC's 10 req/s
    policy (TDD: NFR-4).  Retry logic uses exponential backoff with
    up to 3 attempts (TDD: Section 7.1).
    """

    user_agent: str
    rate_limit_rps: int = 10
    max_retries: int = 3
    _semaphore: asyncio.Semaphore = field(init=False)

    def __post_init__(self) -> None:
        self._semaphore = asyncio.Semaphore(self.rate_limit_rps)

    # ── Search ───────────────────────────────────────────────────

    async def search_10k_filings(
        self,
        ticker: str,
        session: aiohttp.ClientSession,
    ) -> list[dict[str, Any]]:
        """Search EDGAR EFTS for 10-K filings by ticker symbol.

        Returns a list of filing metadata dicts from the EDGAR
        search API.  Returns an empty list on error (FR-5).
        """
        params = {
            "q": f'"{ticker}"',
            "dateRange": "custom",
            "forms": "10-K",
            "startdt": "2020-01-01",
            "enddt": "2026-12-31",
        }
        headers = {"User-Agent": self.user_agent, "Accept": "application/json"}

        for attempt in range(1, self.max_retries + 1):
            try:
                t0 = time.perf_counter()
                async with self._semaphore, session.get(
                    EDGAR_SEARCH_URL, params=params, headers=headers
                ) as resp:
                    resp.raise_for_status()
                    data: dict[str, Any] = await resp.json(content_type=None)
                    await asyncio.sleep(1.0 / self.rate_limit_rps)
                elapsed = time.perf_counter() - t0
                EDGAR_REQUEST_DURATION.labels(ticker=ticker).observe(elapsed)

                hits: list[dict[str, Any]] = data.get("hits", {}).get("hits", [])
                logger.info(
                    "edgar_search_complete",
                    ticker=ticker,
                    results_count=len(hits),
                    elapsed_ms=round(elapsed * 1000, 1),
                )
                return hits

            except aiohttp.ClientError as exc:
                if attempt == self.max_retries:
                    logger.error(
                        "edgar_search_failed",
                        ticker=ticker,
                        error=str(exc),
                    )
                    return []
                wait = 2**attempt
                logger.warning(
                    "edgar_search_retry",
                    ticker=ticker,
                    attempt=attempt,
                    wait_seconds=wait,
                    error=str(exc),
                )
                await asyncio.sleep(wait)

            except Exception as exc:
                logger.error(
                    "edgar_search_failed",
                    ticker=ticker,
                    error=str(exc),
                )
                return []

        return []  # unreachable but keeps mypy happy

    # ── Fetch full text ──────────────────────────────────────────

    async def fetch_filing_text(
        self,
        filing_url: str,
        session: aiohttp.ClientSession,
    ) -> str | None:
        """Fetch the full text of a filing from its EDGAR archive URL.

        Returns the raw text content, or None on failure (FR-5).
        """
        headers = {"User-Agent": self.user_agent}

        for attempt in range(1, self.max_retries + 1):
            try:
                t0 = time.perf_counter()
                chunks: list[bytes] = []
                bytes_received = 0
                last_log_bytes = 0

                async with self._semaphore, session.get(filing_url, headers=headers) as resp:
                    resp.raise_for_status()
                    content_length = resp.content_length  # may be None
                    logger.info(
                        "edgar_fetch_started_http",
                        url=filing_url,
                        content_length_bytes=content_length,
                        content_length_mb=round(content_length / 1024 / 1024, 1) if content_length else None,
                    )
                    async for chunk in resp.content.iter_chunked(1024 * 64):  # 64 KB chunks
                        chunks.append(chunk)
                        bytes_received += len(chunk)
                        if bytes_received - last_log_bytes >= _PROGRESS_LOG_INTERVAL:
                            logger.info(
                                "edgar_fetch_progress",
                                url=filing_url,
                                received_mb=round(bytes_received / 1024 / 1024, 1),
                                total_mb=round(content_length / 1024 / 1024, 1) if content_length else None,
                                elapsed_ms=round((time.perf_counter() - t0) * 1000, 1),
                            )
                            last_log_bytes = bytes_received
                    await asyncio.sleep(1.0 / self.rate_limit_rps)

                raw_bytes = b"".join(chunks)
                elapsed = time.perf_counter() - t0
                text = raw_bytes.decode("utf-8", errors="replace")
                logger.info(
                    "edgar_fetch_complete",
                    url=filing_url,
                    elapsed_ms=round(elapsed * 1000, 1),
                    size_mb=round(len(raw_bytes) / 1024 / 1024, 1),
                    throughput_mbps=round(len(raw_bytes) / 1024 / 1024 / elapsed, 2) if elapsed > 0 else None,
                )
                return text

            except aiohttp.ClientError as exc:
                if attempt == self.max_retries:
                    logger.error(
                        "edgar_fetch_failed",
                        url=filing_url,
                        error=str(exc),
                    )
                    return None
                wait = 2**attempt
                logger.warning(
                    "edgar_fetch_retry",
                    url=filing_url,
                    attempt=attempt,
                    wait_seconds=wait,
                    error=str(exc),
                )
                await asyncio.sleep(wait)

            except Exception as exc:
                logger.error(
                    "edgar_fetch_failed",
                    url=filing_url,
                    error=str(exc),
                )
                return None

        return None  # unreachable but keeps mypy happy

    # ── End-to-end per ticker ────────────────────────────────────

    async def get_filings_for_ticker(
        self,
        ticker: str,
        company_name: str,
        session: aiohttp.ClientSession,
    ) -> list[Filing]:
        """Search for 10-K filings and fetch their full text.

        Implements:
          - FR-1: Fetch 10-K filings from SEC EDGAR by ticker.
          - FR-5: Log and skip filings that fail; continue with rest.
        """
        t_ticker = time.perf_counter()
        hits = await self.search_10k_filings(ticker, session)
        filings: list[Filing] = []

        for hit in hits:
            source = hit.get("_source", {})

            # EDGAR uses "adsh" for the accession number (e.g. "0000320193-24-000123")
            accession = source.get("adsh", "")
            if not accession:
                logger.warning("edgar_missing_accession", ticker=ticker)
                FILINGS_FETCHED_TOTAL.labels(ticker=ticker, status="skipped").inc()
                continue

            filing_date = source.get("file_date", "")

            # EDGAR doesn't return a direct filing URL — construct it from the accession number
            # Format: https://www.sec.gov/Archives/edgar/data/{CIK}/{accession-no-dashes}/{accession}.txt
            ciks = source.get("ciks", [])
            cik = ciks[0] if ciks else ""
            if not cik:
                logger.warning("edgar_missing_cik", ticker=ticker, accession=accession)
                FILINGS_FETCHED_TOTAL.labels(ticker=ticker, status="skipped").inc()
                continue

            accession_no_dashes = accession.replace("-", "")
            source_url = (
                f"https://www.sec.gov/Archives/edgar/data/{cik}/{accession_no_dashes}/{accession}.txt"
            )

            logger.info(
                "edgar_fetch_started",
                ticker=ticker,
                accession=accession,
                filing_date=filing_date,
                url=source_url,
            )
            raw_text = await self.fetch_filing_text(source_url, session)
            if raw_text is None:
                logger.warning(
                    "edgar_fetch_skipped",
                    ticker=ticker,
                    accession=accession,
                )
                FILINGS_FETCHED_TOTAL.labels(ticker=ticker, status="skipped").inc()
                continue

            filings.append(
                Filing(
                    accession_number=accession,
                    ticker=ticker,
                    company_name=company_name,
                    filing_date=filing_date,
                    filing_type="10-K",
                    source_url=source_url,
                    raw_text=raw_text,
                )
            )

        logger.info(
            "edgar_ticker_complete",
            ticker=ticker,
            filings_found=len(filings),
            elapsed_ms=round((time.perf_counter() - t_ticker) * 1000, 1),
        )
        return filings
