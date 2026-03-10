"""SEC EDGAR EFTS client for fetching 10-K filings.

Uses the EDGAR full-text search API to find filings, then fetches
the filing document text from the EDGAR archives.

References:
  - TDD: FR-1 (fetch 10-K filings from EFTS API by ticker)
  - TDD: FR-5 (log and skip filings that fail to parse)
  - TDD: NFR-4 (respect SEC rate limit of 10 req/s)
  - API docs: https://efts.sec.gov/LATEST/search-index
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

import aiohttp
import structlog

logger = structlog.get_logger()

# EDGAR full-text search endpoint
EDGAR_SEARCH_URL = "https://efts.sec.gov/LATEST/search-index"


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
                async with self._semaphore:
                    async with session.get(
                        EDGAR_SEARCH_URL, params=params, headers=headers
                    ) as resp:
                        resp.raise_for_status()
                        data: dict[str, Any] = await resp.json(content_type=None)
                        await asyncio.sleep(1.0 / self.rate_limit_rps)

                hits: list[dict[str, Any]] = data.get("hits", {}).get("hits", [])
                logger.info(
                    "edgar_search_complete",
                    ticker=ticker,
                    results_count=len(hits),
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
                async with self._semaphore:
                    async with session.get(filing_url, headers=headers) as resp:
                        resp.raise_for_status()
                        text: str = await resp.text()
                        await asyncio.sleep(1.0 / self.rate_limit_rps)
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
        hits = await self.search_10k_filings(ticker, session)
        filings: list[Filing] = []

        for hit in hits:
            source = hit.get("_source", {})
            accession = source.get("accession_no", "") or source.get("file_num", "")
            accession = accession.strip()
            if not accession:
                logger.warning("edgar_missing_accession", ticker=ticker)
                continue

            filing_date = source.get("file_date", source.get("period_of_report", ""))
            source_url = source.get("file_url", "")

            if not source_url:
                logger.warning(
                    "edgar_missing_url",
                    ticker=ticker,
                    accession=accession,
                )
                continue

            raw_text = await self.fetch_filing_text(source_url, session)
            if raw_text is None:
                logger.warning(
                    "edgar_fetch_skipped",
                    ticker=ticker,
                    accession=accession,
                )
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
        )
        return filings