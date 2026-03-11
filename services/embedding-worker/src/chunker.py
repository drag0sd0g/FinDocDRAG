"""Section-aware chunking for 10-K filings.

Implements the three-stage chunking strategy from TDD Section 5.2.2:
  1. Section split — by 10-K item headers (Item 1, 1A, 7, etc.)
  2. Paragraph split — by double newlines within each section
  3. Token-based windowing — 512-token windows with 64-token overlap

References:
  - TDD: FR-7 (section-aware splitting with 512/64 token window)
  - TDD: FR-8 (chunk metadata: ticker, filing_date, accession, section, index)
  - TDD: Section 5.2.2 Chunking Strategy
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

import tiktoken

# ── Constants ────────────────────────────────────────────────────

# 10-K section headers we recognise (TDD Section 5.2.2 step 1)
_ITEM_PATTERN = re.compile(
    r"(?:^|\n)"                   # start of string or newline
    r"(?:ITEM|Item)\s+"           # "Item" or "ITEM"
    r"(1A|1B|1|2|3|4|5|6|7A|7|8|9A|9B|9|10|11|12|13|14|15)"
    r"[.\s:—\-]",                 # followed by punctuation/space
    re.MULTILINE,
)

# Canonical ordering so we can sort matches deterministically
_ITEM_ORDER = [
    "1", "1A", "1B", "2", "3", "4", "5", "6",
    "7", "7A", "8", "9", "9A", "9B", "10", "11",
    "12", "13", "14", "15",
]

DEFAULT_CHUNK_SIZE = 512   # tokens
DEFAULT_OVERLAP = 64       # tokens
TIKTOKEN_ENCODING = "cl100k_base"


@dataclass
class Chunk:
    """A single text chunk with metadata."""

    chunk_id: str          # SHA256(accession_number + section_name + chunk_index)
    accession_number: str
    ticker: str
    filing_date: str
    section_name: str
    chunk_index: int
    text: str
    token_count: int


# ── Tokeniser (cached) ──────────────────────────────────────────

_encoder: tiktoken.Encoding | None = None


def _get_encoder() -> tiktoken.Encoding:
    """Return the cl100k_base encoder (lazily initialised)."""
    global _encoder  # noqa: PLW0603
    if _encoder is None:
        _encoder = tiktoken.get_encoding(TIKTOKEN_ENCODING)
    return _encoder


def count_tokens(text: str) -> int:
    """Count tokens using cl100k_base encoding (TDD Section 5.4)."""
    return len(_get_encoder().encode(text))


# ── Chunk ID generation ─────────────────────────────────────────

def make_chunk_id(accession_number: str, section_name: str, chunk_index: int) -> str:
    """Deterministic chunk ID: SHA256(accession + section + index).

    TDD Section 5.2.2 step 4.
    """
    raw = f"{accession_number}{section_name}{chunk_index}"
    return hashlib.sha256(raw.encode()).hexdigest()


# ── Stage 1: Section split ──────────────────────────────────────

def split_into_sections(text: str) -> list[tuple[str, str]]:
    """Split a 10-K filing into (section_name, section_text) pairs.

    Uses regex to find Item headers. Text before the first item is
    labelled "Preamble". If no items are found, the entire document
    is returned as a single "Full Document" section.
    """
    matches = list(_ITEM_PATTERN.finditer(text))

    if not matches:
        return [("Full Document", text)]

    sections: list[tuple[str, str]] = []

    # Text before the first match → Preamble
    if matches[0].start() > 0:
        preamble = text[: matches[0].start()].strip()
        if preamble:
            sections.append(("Preamble", preamble))

    for i, match in enumerate(matches):
        item_number = match.group(1)
        section_name = f"Item {item_number}"
        start = match.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        section_text = text[start:end].strip()
        if section_text:
            sections.append((section_name, section_text))

    return sections


# ── Stage 2: Paragraph split ────────────────────────────────────

def split_into_paragraphs(text: str) -> list[str]:
    """Split section text by double newlines (TDD step 2).

    Returns non-empty paragraphs only.
    """
    paragraphs = re.split(r"\n\s*\n", text)
    return [p.strip() for p in paragraphs if p.strip()]


# ── Stage 3: Token-based windowing ──────────────────────────────

def split_by_token_window(
    text: str,
    max_tokens: int = DEFAULT_CHUNK_SIZE,
    overlap: int = DEFAULT_OVERLAP,
) -> list[str]:
    """Split text into token-count windows with overlap (TDD step 3).

    If the text fits within max_tokens, returns it as a single window.
    """
    encoder = _get_encoder()
    tokens = encoder.encode(text)

    if len(tokens) <= max_tokens:
        return [text]

    windows: list[str] = []
    start = 0
    while start < len(tokens):
        end = start + max_tokens
        window_tokens = tokens[start:end]
        windows.append(encoder.decode(window_tokens))
        # Advance by (max_tokens - overlap) so the next window overlaps
        start += max_tokens - overlap

    return windows


# ── Public API: full chunking pipeline ───────────────────────────

def chunk_filing(
    raw_text: str,
    accession_number: str,
    ticker: str,
    filing_date: str,
    max_tokens: int = DEFAULT_CHUNK_SIZE,
    overlap: int = DEFAULT_OVERLAP,
) -> list[Chunk]:
    """Run the full three-stage chunking pipeline on a filing.

    Returns a list of Chunk objects ready for embedding and storage.
    Implements FR-7, FR-8.
    """
    chunks: list[Chunk] = []
    global_index = 0

    sections = split_into_sections(raw_text)

    for section_name, section_text in sections:
        paragraphs = split_into_paragraphs(section_text)

        for paragraph in paragraphs:
            windows = split_by_token_window(paragraph, max_tokens, overlap)

            for window_text in windows:
                token_count = count_tokens(window_text)
                chunk_id = make_chunk_id(accession_number, section_name, global_index)
                chunks.append(
                    Chunk(
                        chunk_id=chunk_id,
                        accession_number=accession_number,
                        ticker=ticker,
                        filing_date=filing_date,
                        section_name=section_name,
                        chunk_index=global_index,
                        text=window_text,
                        token_count=token_count,
                    )
                )
                global_index += 1

    return chunks