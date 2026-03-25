[日本語](README.ja.md)

# FinDocDRAG — API Examples

This directory contains a [Bruno](https://www.usebruno.com/) collection for exploring the FinDocDRAG API and a concrete annotated example showing a real end-to-end query response.

## Opening the collection in Bruno

1. Install Bruno: https://www.usebruno.com/downloads
2. **Open Collection** → select the `examples/bruno/` folder
3. In the top-right environment picker, select **local**
4. Start the stack (`make run` or `make run-remote`) before sending requests

---

## End-to-end walkthrough

### Step 1 — Ingest a filing

```bash
curl -X POST http://localhost:8001/v1/ingest \
  -H "Content-Type: application/json" \
  -d '{"tickers": ["AAPL"]}'
```

```json
{
  "status": "completed",
  "tickers_processed": ["AAPL"],
  "filings_published": 3,
  "filings_skipped": 0,
  "errors": []
}
```

The embedding worker picks up the Kafka messages, chunks each filing into ~512-token windows, embeds them with `sentence-transformers/all-MiniLM-L6-v2`, and writes the vectors to pgvector. This takes a few minutes on CPU.

---

### Step 2 — Query the knowledge base

```bash
curl -X POST http://localhost:8000/v1/query \
  -H "Content-Type: application/json" \
  -H "X-API-Key: dev-key-1" \
  -d '{
    "question": "What were Apple'\''s main risk factors related to supply chain in their 2024 10-K?",
    "ticker_filter": "AAPL",
    "top_k": 5
  }'
```

**Annotated response:**

```jsonc
{
  // LLM-generated answer grounded in the retrieved chunks.
  // Null when the LLM is unavailable (see "degraded" below).
  "answer": "Apple's 2024 10-K identifies supply chain concentration as a primary risk. The Company sources components including advanced semiconductors, displays, and rare earth materials from a small number of suppliers, many located in Asia. A disruption — whether from geopolitical tension, natural disaster, or a single-supplier failure — could delay product launches and reduce gross margins [Source 1]. Apple also cites logistical dependency on third-party carriers for last-mile delivery, which creates exposure during peak demand periods [Source 2]. Additionally, the Company acknowledges that its contract manufacturers, primarily in China, are subject to local regulatory changes and labour cost inflation that it cannot fully control [Source 3].",

  // Ranked list of the chunks that were injected into the LLM prompt.
  // Ordered by MMR score (balances relevance and diversity; see ADR-011).
  "sources": [
    {
      "chunk_id": "a3f8c2d1e9b047f6a2c5d8e1f3b4a7c9",  // SHA-256 of (accession || section || index), no separator
      "ticker": "AAPL",
      "filing_date": "2024-11-01",
      "section": "Item 1A",
      "relevance_score": 0.91,  // cosine distance score in [0, 1]; higher = more relevant
      "text_preview": "The Company's operations and performance depend significantly on its ability to source components from a limited number of suppliers. The loss of a significant supplier could adversely affect..."
    },
    {
      "chunk_id": "b7e1d4c2a9f053e8c1d6a3f2e8b5c4d0",
      "ticker": "AAPL",
      "filing_date": "2024-11-01",
      "section": "Item 1A",
      "relevance_score": 0.87,
      "text_preview": "The Company relies on third-party carriers for product delivery. Disruptions in logistics networks, including during peak demand periods, could impact product availability..."
    },
    {
      "chunk_id": "c9a5f3e7b2d018c4e7a2b9f1d3c6e8a1",
      "ticker": "AAPL",
      "filing_date": "2024-11-01",
      "section": "Item 1A",
      "relevance_score": 0.83,
      "text_preview": "The Company's contract manufacturers are primarily located in China and are subject to local laws and regulations. Changes in labour costs, environmental regulations, or..."
    },
    {
      "chunk_id": "d2b8e4a6c1f079d5b3e8c2a4f7d1b9e3",
      "ticker": "AAPL",
      "filing_date": "2023-11-03",
      "section": "Item 1A",
      "relevance_score": 0.79,
      "text_preview": "Global supply chain disruptions, including those resulting from geopolitical tensions, trade restrictions, or natural disasters, could adversely affect the availability..."
    },
    {
      "chunk_id": "e5c1a7d3b9f026e4c8b1a5d2f4e7c3b6",
      "ticker": "AAPL",
      "filing_date": "2024-11-01",
      "section": "Item 7",
      "relevance_score": 0.74,
      "text_preview": "Net sales decreased 1% or $3.6 billion during 2024 compared to 2023, reflecting supply constraints on certain products in the first half of the year..."
    }
  ],

  // Which LLM backend produced this answer.
  "model": "mistral:7b",

  // Per-stage latency breakdown in milliseconds.
  "timing": {
    "embedding_ms": 11.3,    // time to embed the question with sentence-transformers
    "retrieval_ms": 42.7,    // pgvector cosine distance search + MMR reranking over ~12 K chunks
    "generation_ms": 5840.0, // LLM inference (Ollama on CPU is typically 3–15 s)
    "total_ms": 5894.0
  },

  // false = full answer returned.
  // true  = LLM was unavailable; "answer" is null but "sources" are still populated
  //         so callers can display retrieved context even without a generated answer.
  "degraded": false,

  // Echoed back in X-Request-ID response header; use for log correlation.
  "request_id": "550e8400-e29b-41d4-a716-446655440000"
}
```

---

### Step 3 — Inspect ingested filings

```bash
curl http://localhost:8000/v1/documents?ticker=AAPL \
  -H "X-API-Key: dev-key-1"
```

```json
{
  "documents": [
    {
      "accession_number": "0000320193-24-000123",
      "ticker": "AAPL",
      "company_name": "Apple Inc.",
      "filing_date": "2024-11-01",
      "filing_type": "10-K",
      "chunk_count": 312,
      "ingested_at": "2024-11-15T09:32:14.000000"
    }
  ],
  "total": 1,
  "limit": 20,
  "offset": 0
}
```

---

## Collection structure

```
bruno/
  bruno.json                        Collection root (required by Bruno)
  environments/
    local.bru                       Vars: query_api_url, ingestion_url,
                                          embedding_worker_url, api_key
  01-health/
    query-api-health.bru            GET /health
    ingestion-health.bru            GET /health
    embedding-worker-health.bru     GET /health
  02-ingest/
    ingest-tickers.bru              POST /v1/ingest  {"tickers": ["AAPL","MSFT"]}
    ingest-from-config.bru          POST /v1/ingest  (reads config/tickers.yml)
  03-query/
    supply-chain-risk-aapl.bru      Annotated example above (with assertions)
    revenue-comparison-msft-aapl.bru Cross-ticker question, no ticker_filter
    graceful-degradation.bru        Shows degraded=true shape when LLM is down
  04-documents/
    list-all-documents.bru          GET /v1/documents
    list-documents-by-ticker.bru    GET /v1/documents?ticker=AAPL
```

## Tips

- **Auth:** all Query API requests require `X-API-Key: dev-key-1` (set in the `local` environment). Ingestion has no auth.
- **Degraded mode:** stop the Ollama container (`docker stop findoc-ollama`) then send any query — you will receive HTTP 200 with `"degraded": true` and `"answer": null` but the `sources` array will still be populated.
- **Rate limit:** the Query API enforces 30 requests/minute per key. Bruno will receive HTTP 429 if you run the collection in bulk too fast.
- **`top_k` range:** 1–20. At 512 tokens/chunk, 20 chunks ≈ 10 K tokens, fitting within a 32 K-token context window.
