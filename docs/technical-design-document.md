# Financial Document Intelligence Platform — Technical Design Document

## Table of Contents

1. [Glossary](#1-glossary)
2. [Background](#2-background)
3. [Functional Requirements](#3-functional-requirements)
4. [Non-Functional Requirements](#4-non-functional-requirements)
5. [Main Proposal](#5-main-proposal)
6. [Scalability](#6-scalability)
7. [Resilience](#7-resilience)
8. [Observability](#8-observability)
9. [Security](#9-security)
10. [Deployment](#10-deployment)

---

## 1. Glossary

| Term                        | Definition                                                                                                                                                      |
| --------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **RAG**                     | Retrieval-Augmented Generation — a technique that grounds LLM responses in retrieved source documents to reduce hallucination and provide citations.            |
| **Embedding**               | A fixed-length dense vector representation of a text chunk, produced by a neural encoder model, used for semantic similarity search.                            |
| **Vector Store**            | A database or index optimized for storing and querying high-dimensional embedding vectors via approximate nearest-neighbor (ANN) search.                        |
| **pgvector**                | An open-source PostgreSQL extension that adds vector column types and ANN index support (IVFFlat, HNSW) to standard PostgreSQL.                                 |
| **Chunk**                   | A segment of a source document produced by splitting the original text at logical boundaries (sections, paragraphs) to fit within model context limits.         |
| **10-K Filing**             | An annual comprehensive report filed by a publicly traded company with the SEC, containing audited financial statements and business disclosures.               |
| **SEC EDGAR**               | The U.S. Securities and Exchange Commission's Electronic Data Gathering, Analysis, and Retrieval system — a free public API for accessing company filings.      |
| **LLM**                     | Large Language Model — a neural network trained on large text corpora capable of generating human-like text given a prompt.                                     |
| **Ollama**                  | An open-source tool for running LLMs locally as a self-contained server with a REST API.                                                                        |
| **HNSW**                    | Hierarchical Navigable Small World — a graph-based approximate nearest-neighbor index algorithm used by pgvector for fast vector search.                        |
| **Cosine Similarity**       | A metric measuring the cosine of the angle between two vectors; used to rank document chunks by relevance to a query embedding.                                 |
| **Token**                   | The smallest unit of text processed by an LLM (roughly ¾ of a word in English). Models have fixed token limits for input + output.                              |
| **Context Window**          | The maximum number of tokens an LLM can process in a single request (prompt + completion combined).                                                             |
| **Hallucination**           | When an LLM generates plausible-sounding but factually incorrect information not grounded in source data.                                                       |
| **CDC**                     | Change Data Capture — a pattern for detecting and propagating data changes; used here conceptually for the ingestion pipeline's event-driven design.            |
| **Drift (Embedding Drift)** | A shift in the statistical distribution of embeddings over time, which can degrade retrieval quality if the embedding model or document characteristics change. |
| **ragas**                   | An open-source framework for evaluating RAG pipelines, providing metrics like faithfulness, answer relevancy, and context precision.                            |
| **Helm**                    | A package manager for Kubernetes that uses templated YAML charts to define, version, and deploy application stacks.                                             |
| **HPA**                     | Horizontal Pod Autoscaler — a Kubernetes resource that automatically scales the number of pod replicas based on observed metrics.                               |
| **Pydantic**                | A Python data validation library that enforces type contracts on request/response models, used by FastAPI for schema generation.                                |
| **FastAPI**                 | A modern, high-performance Python web framework for building APIs, with automatic OpenAPI documentation and async support.                                      |
| **sentence-transformers**   | A Python library providing pre-trained models for generating sentence and text embeddings, built on top of HuggingFace Transformers.                            |

---

## 2. Background

### 2.1 Problem Statement

Financial professionals — analysts, compliance officers, portfolio managers — routinely need to extract specific information from large volumes of regulatory filings and earnings documents. Today this involves:

- **Manual search**: downloading PDFs or HTML filings from SEC EDGAR and using Ctrl+F or reading linearly.
- **Keyword-based search**: traditional full-text search indexes return documents but not answers; users still read pages of context.
- **Analyst summaries**: produced by humans with significant lag, expensive, and limited in coverage.

None of these approaches let a user ask a natural language question — such as _"What were Apple's main risk factors related to supply chain in their 2024 10-K?"_ — and receive a direct, cited answer grounded in the source filings.

### 2.2 Proposed Solution

**FinDoc RAG** is an end-to-end Retrieval-Augmented Generation platform that:

1. Ingests public financial documents (SEC 10-K filings) via an event-driven pipeline.
2. Chunks, embeds, and indexes documents in a vector store.
3. Exposes a REST API that accepts natural language queries, retrieves relevant document chunks via semantic similarity, and generates grounded answers with source citations using a Large Language Model.
4. Provides production-grade observability, evaluation, and deployment infrastructure.

### 2.3 Goals

| ID  | Goal                                                                                     |
| --- | ---------------------------------------------------------------------------------------- |
| G-1 | Demonstrate an end-to-end RAG system from document ingestion to cited answer generation. |
| G-2 | Use an event-driven architecture (Kafka) for the ingestion pipeline.                     |
| G-3 | Serve the platform as a production-style REST API with OpenAPI documentation.            |
| G-4 | Provide quantified evaluation of retrieval and answer quality.                           |
| G-5 | Deploy the full stack on Kubernetes with Helm, including observability.                  |
| G-6 | Document all architectural decisions with explicit rationale and trade-offs.             |

### 2.4 Non-Goals

| ID   | Non-Goal                                                                                                  |
| ---- | --------------------------------------------------------------------------------------------------------- |
| NG-1 | Building a production multi-tenant SaaS product — this is a portfolio/demonstration project.              |
| NG-2 | Fine-tuning or training custom LLMs — we use pre-trained models via Ollama, OpenAI API, or Anthropic API. |
| NG-3 | Supporting real-time streaming queries (WebSocket/SSE) — the API is request/response.                     |
| NG-4 | Ingesting non-SEC document sources (e.g., news, proprietary research) in the initial version.             |
| NG-5 | Building a frontend UI — the interface is the REST API (with auto-generated Swagger docs).                |

### 2.5 System Context Diagram

```
┌──────────────────────────────────────────────────────────────────────┐
│                         FinDoc RAG System                            │
│                                                                      │
│  ┌────────────┐   ┌──────────────┐   ┌────────────┐   ┌──────────┐ │
│  │ Ingestion  │──▶│  Embedding   │──▶│  Query     │──▶│   LLM    │ │
│  │ Service    │   │  Worker      │   │  API       │   │  (LLM)   │ │
│  └─────┬──────┘   └──────┬───────┘   └─────┬──────┘   └──────────┘ │
│        │                 │                  │                         │
│        │           ┌─────▼──────┐           │                        │
│        │           │ PostgreSQL │◀──────────┘                        │
│        │           │ + pgvector │                                    │
│   ┌────▼────┐      └────────────┘                                   │
│   │  Kafka  │                                                        │
│   └─────────┘                                                        │
└──────────────────────────────────────────────────────────────────────┘
       ▲                                            ▲
       │                                            │
┌──────┴──────┐                              ┌──────┴──────┐
│  SEC EDGAR  │                              │  API Client │
│  (Public)   │                              │  (User)     │
└─────────────┘                              └─────────────┘
```

---

## 3. Functional Requirements

### 3.1 Document Ingestion

| ID   | Requirement                                                                                                                                                                |
| ---- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| FR-1 | The system SHALL fetch 10-K filings from SEC EDGAR's EFTS full-text search API (`efts.sec.gov/LATEST/search-index`) by company ticker symbol.                              |
| FR-2 | The system SHALL accept a list of ticker symbols via a configuration file (`config/tickers.yml`) that defines which companies to ingest.                                   |
| FR-3 | The system SHALL publish each fetched filing as a message to a Kafka topic (`filings.raw`) with metadata: ticker, filing date, accession number, and the raw text content. |
| FR-4 | The system SHALL be idempotent on re-ingestion: if a filing with the same accession number already exists in the vector store, it SHALL be skipped.                        |
| FR-5 | The system SHALL log and skip filings that fail to parse, without halting the ingestion of remaining filings.                                                              |

### 3.2 Chunking and Embedding

| ID    | Requirement                                                                                                                                                                                                                                                            |
| ----- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| FR-6  | An embedding worker service SHALL consume messages from the `filings.raw` Kafka topic.                                                                                                                                                                                 |
| FR-7  | The worker SHALL split each filing into chunks using section-aware splitting: first by 10-K item sections (Item 1, Item 1A, Item 7, etc.), then by paragraphs within sections, with a target chunk size of 512 tokens and 64-token overlap between consecutive chunks. |
| FR-8  | Each chunk SHALL retain metadata: source ticker, filing date, accession number, section name, and chunk sequence index.                                                                                                                                                |
| FR-9  | The worker SHALL generate a 384-dimensional embedding vector for each chunk using the `sentence-transformers/all-MiniLM-L6-v2` model.                                                                                                                                  |
| FR-10 | The worker SHALL store each chunk (text + metadata + embedding vector) in the `document_chunks` table in PostgreSQL with pgvector.                                                                                                                                     |
| FR-11 | The worker SHALL publish successfully processed chunk IDs to a `filings.embedded` Kafka topic for downstream observability.                                                                                                                                            |

### 3.3 Query and Answer Generation

| ID    | Requirement                                                                                                                                                                                                               |
| ----- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| FR-12 | The Query API SHALL expose a `POST /v1/query` endpoint that accepts a JSON body with fields: `question` (string, required), `ticker_filter` (string, optional), `top_k` (integer, optional, default 5, max 20).           |
| FR-13 | The Query API SHALL embed the user's question using the same embedding model (`all-MiniLM-L6-v2`) and retrieve the `top_k` most similar chunks from pgvector using cosine distance, optionally filtered by ticker symbol. |
| FR-14 | The Query API SHALL construct a prompt that includes the retrieved chunks as context, the user's question, and an instruction to answer only from the provided context and cite sources.                                  |
| FR-15 | The prompt template SHALL follow this structure:                                                                                                                                                                          |

```
You are a financial document analyst. Answer the user's question using ONLY
the provided context from SEC filings. If the context does not contain enough
information to answer, say "I don't have enough information to answer this."

For every claim in your answer, cite the source using [Source N] notation,
where N corresponds to the context chunk number.

Context:
{numbered_chunks_with_metadata}

Question: {user_question}

Answer:
```

| ID    | Requirement                                                                                                                                                                                                                                                                                                           |
| ----- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| FR-16 | The Query API SHALL send the constructed prompt to the configured LLM backend (Ollama local or OpenAI API) and return the generated answer.                                                                                                                                                                           |
| FR-17 | The API response SHALL include: `answer` (string), `sources` (array of objects with `chunk_id`, `ticker`, `filing_date`, `section`, `relevance_score`, and `text_preview` — first 200 characters of the chunk), `model` (string — which LLM was used), and `timing` (object with `retrieval_ms` and `generation_ms`). |
| FR-18 | The Query API SHALL support three LLM backends, selectable via environment variable (`LLM_BACKEND`): `ollama` (default, using `mistral:7b` model), `openai` (using `gpt-4o-mini` model), and `claude` (using `claude-opus-4-6` model, overridable via `CLAUDE_MODEL`).                                                |

### 3.4 Document Listing

| ID    | Requirement                                                                                                                                                                                       |
| ----- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| FR-19 | The Query API SHALL expose a `GET /v1/documents` endpoint that returns a list of all ingested filings with metadata: ticker, filing date, accession number, chunk count, and ingestion timestamp. |
| FR-20 | The `GET /v1/documents` endpoint SHALL support optional query parameters: `ticker` (filter by ticker symbol) and `limit`/`offset` (pagination, default limit 20, max 100).                        |

### 3.5 Health and Readiness

| ID    | Requirement                                                                                                                                                                                                                        |
| ----- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| FR-21 | Each service (Ingestion, Embedding Worker, Query API) SHALL expose a `GET /health` endpoint returning `{"status": "healthy"}` with HTTP 200 when the service is running and its dependencies (database, Kafka, LLM) are reachable. |
| FR-22 | Each service SHALL expose a `GET /ready` endpoint returning HTTP 200 only when the service is fully initialized and ready to process requests (e.g., embedding model loaded, database connection pool established).                |

---

## 4. Non-Functional Requirements

### 4.1 Performance

| ID    | Requirement                                                                                                                                                             |
| ----- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| NFR-1 | Vector similarity search (retrieval only, excluding LLM generation) SHALL complete within 200ms at p99 for a corpus of up to 500,000 chunks.                            |
| NFR-2 | End-to-end query latency (retrieval + LLM generation) SHALL complete within 10 seconds at p95 when using Ollama with `mistral:7b` on a machine with at least 16 GB RAM. |
| NFR-3 | The embedding worker SHALL process and store at least 50 chunks per second on a single worker instance.                                                                 |
| NFR-4 | The ingestion service SHALL fetch and publish at least 10 filings per minute from SEC EDGAR, respecting the SEC's rate limit of 10 requests per second.                 |

### 4.2 Capacity

| ID    | Requirement                                                                                                 |
| ----- | ----------------------------------------------------------------------------------------------------------- |
| NFR-5 | The system SHALL support ingestion and querying of up to 1,000 10-K filings (approximately 500,000 chunks). |
| NFR-6 | The vector store SHALL support up to 500,000 384-dimensional vectors with HNSW indexing.                    |

### 4.3 Quality and Evaluation

| ID    | Requirement                                                                                                                                                                                                                                                                                 |
| ----- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| NFR-7 | The project SHALL include an evaluation harness that runs against a curated dataset of at least 30 question-answer pairs across at least 5 different companies.                                                                                                                             |
| NFR-8 | The evaluation harness SHALL compute and report the following metrics using the `ragas` library: **Context Precision** (are the retrieved chunks relevant?), **Faithfulness** (is the answer grounded in retrieved context?), **Answer Relevancy** (does the answer address the question?). |
| NFR-9 | Evaluation results SHALL be persisted as a JSON report and summarized in `docs/evaluation-results.md` with a table of per-question scores and aggregate means.                                                                                                                              |

### 4.4 Developer Experience

| ID     | Requirement                                                                                                                 |
| ------ | --------------------------------------------------------------------------------------------------------------------------- |
| NFR-10 | The entire stack SHALL be runnable locally with a single `docker compose up` command.                                       |
| NFR-11 | The project SHALL include a `Makefile` with targets: `setup`, `test`, `lint`, `run`, `eval`, `docker-build`, `helm-deploy`. |
| NFR-12 | All Python code SHALL pass `ruff` linting and `mypy` type checking with zero errors.                                        |
| NFR-13 | Each service SHALL have unit tests with at least 80% line coverage, measured by `pytest-cov`.                               |

---

## 5. Main Proposal

### 5.1 High-Level Architecture

The system consists of four independently deployable services connected by Kafka and a shared PostgreSQL database:

```
┌──────────────┐     ┌─────────┐     ┌───────────────────┐     ┌─────────────┐
│  SEC EDGAR   │────▶│Ingestion│────▶│   Kafka            │────▶│  Embedding  │
│  (External)  │     │ Service │     │                     │     │  Worker     │
└──────────────┘     └─────────┘     │  filings.raw        │     └──────┬──────┘
                                     │  filings.embedded    │            │
                                     │  filings.dlq         │            │
                                     └───────────────────────┘            │
                                                                         ▼
┌──────────────┐     ┌─────────┐                            ┌───────────────────┐
│  API Client  │◀───▶│ Query   │◀──────────────────────────▶│   PostgreSQL      │
│  (User)      │     │ API     │                            │   + pgvector      │
└──────────────┘     └────┬────┘                            │                   │
                          │                                 │  document_chunks  │
                          ▼                                 │  ingestion_log    │
                     ┌─────────┐                            └───────────────────┘
                     │  Ollama │
                     │  (LLM)  │
                     └─────────┘
```

### 5.2 Service Descriptions

#### 5.2.1 Ingestion Service

| Property      | Value                                                                  |
| ------------- | ---------------------------------------------------------------------- |
| **Language**  | Python 3.12                                                            |
| **Framework** | FastAPI (exposes health/ready endpoints + a `POST /v1/ingest` trigger) |
| **Role**      | Fetches 10-K filings from SEC EDGAR and publishes raw text to Kafka    |

**Ingest Flow:**

```
┌──────────┐      ┌──────────────────┐      ┌──────────────┐      ┌────────┐
│  Trigger │─────▶│  Fetch filing    │─────▶│  Deduplicate │─────▶│Publish │
│ POST /v1 │      │  from EDGAR API  │      │  (check by   │      │to Kafka│
│ /ingest  │      │                  │      │  accession#) │      │        │
└──────────┘      └──────────────────┘      └──────────────┘      └────────┘
```

- Accepts `POST /v1/ingest` with body `{"tickers": ["AAPL", "MSFT"]}` or reads from `config/tickers.yml` if no body is provided.
- For each ticker, queries SEC EDGAR EFTS API for 10-K filings.
- For each filing, checks the `ingestion_log` table; skips if the accession number exists.
- Publishes the raw filing text + metadata to `filings.raw` Kafka topic.
- Records the accession number in `ingestion_log` with status `PUBLISHED`.

**Kafka Message Schema (`filings.raw`):**

```json
{
  "accession_number": "0000320193-24-000123",
  "ticker": "AAPL",
  "filing_date": "2024-11-01",
  "filing_type": "10-K",
  "company_name": "Apple Inc.",
  "raw_text": "...",
  "source_url": "https://www.sec.gov/Archives/edgar/data/...",
  "published_at": "2026-03-10T12:00:00Z"
}
```

#### 5.2.2 Embedding Worker

| Property      | Value                                                                            |
| ------------- | -------------------------------------------------------------------------------- |
| **Language**  | Python 3.12                                                                      |
| **Framework** | Standalone Kafka consumer (using `confluent-kafka`) + FastAPI sidecar for health |
| **Role**      | Consumes raw filings, chunks text, generates embeddings, stores in pgvector      |

**Processing Flow:**

```
┌──────────┐    ┌───────────┐    ┌──────────┐    ┌──────────┐    ┌──────────┐
│ Consume  │───▶│  Section  │───▶│  Chunk   │───▶│ Embed    │───▶│  Store   │
│ from     │    │  Split    │    │  by size │    │ via      │    │  in      │
│ Kafka    │    │ (10-K     │    │ (512 tok │    │ sentence │    │ pgvector │
│          │    │  items)   │    │  64 over)│    │ transf.  │    │          │
└──────────┘    └───────────┘    └──────────┘    └──────────┘    └──────────┘
                                                                       │
                                                                       ▼
                                                                 ┌──────────┐
                                                                 │ Publish  │
                                                                 │ to       │
                                                                 │filings.  │
                                                                 │embedded  │
                                                                 └──────────┘
```

**Chunking Strategy:**

1. **Section split**: Parse 10-K structure and split by known items (Item 1, 1A, 1B, 2, 3, 4, 5, 6, 7, 7A, 8, 9, 9A, 9B, 10, 11, 12, 13, 14, 15). The section name is stored as metadata.
2. **Paragraph split**: Within each section, split by double newlines.
3. **Token-based windowing**: If a paragraph exceeds 512 tokens, split it into 512-token windows with 64-token overlap. Token counting uses the `tiktoken` library with the `cl100k_base` encoding.
4. Each resulting chunk is assigned a deterministic `chunk_id`: `SHA256(accession_number + section_name + chunk_sequence_index)`.

**Embedding:**

- Model: `sentence-transformers/all-MiniLM-L6-v2` (384 dimensions, ~80 MB, runs on CPU).
- Batch embedding: chunks are embedded in batches of 64 for throughput.
- The model is loaded once at worker startup and held in memory.

#### 5.2.3 Query API

| Property      | Value                                                                    |
| ------------- | ------------------------------------------------------------------------ |
| **Language**  | Python 3.12                                                              |
| **Framework** | FastAPI                                                                  |
| **Role**      | Accepts user queries, retrieves relevant chunks, generates cited answers |

**Query Flow:**

```
┌──────────┐   ┌──────────┐   ┌───────────┐   ┌──────────┐   ┌──────────┐
│ Receive  │──▶│  Embed   │──▶│  Retrieve │──▶│ Build    │──▶│  Call    │
│ POST     │   │  query   │   │  top-k    │   │ prompt   │   │  LLM    │
│ /v1/query│   │  vector  │   │  chunks   │   │ with     │   │         │
│          │   │          │   │  from     │   │ context  │   │         │
└──────────┘   └──────────┘   │  pgvector │   └──────────┘   └────┬─────┘
                              └───────────┘                       │
                                                                  ▼
                                                            ┌──────────┐
                                                            │ Return   │
                                                            │ answer + │
                                                            │ sources  │
                                                            │ + timing │
                                                            └──────────┘
```

**API Contract:**

Request (`POST /v1/query`):

```json
{
  "question": "What were Apple's main risk factors related to supply chain in their 2024 10-K?",
  "ticker_filter": "AAPL",
  "top_k": 5
}
```

Response:

```json
{
  "answer": "According to Apple's 2024 10-K filing, the main supply chain risk factors include... [Source 1] ... concentration of suppliers in specific regions... [Source 2] ...",
  "sources": [
    {
      "chunk_id": "a1b2c3d4...",
      "ticker": "AAPL",
      "filing_date": "2024-11-01",
      "section": "Item 1A - Risk Factors",
      "relevance_score": 0.87,
      "text_preview": "The Company's business, reputation, results of operations, financial condition..."
    }
  ],
  "model": "mistral:7b",
  "timing": {
    "embedding_ms": 12,
    "retrieval_ms": 45,
    "generation_ms": 3200,
    "total_ms": 3257
  }
}
```

**LLM Backend Abstraction:**

```python
# Simplified interface — all backends implement this
class LLMBackend(Protocol):
    async def generate(self, prompt: str, max_tokens: int = 1024) -> LLMResponse: ...

class OllamaBackend(LLMBackend):
    # Calls local Ollama server at http://ollama:11434/api/generate
    # Model: mistral:7b (default); started via --profile local-llm
    ...

class OpenAIBackend(LLMBackend):
    # Calls OpenAI API with model: gpt-4o-mini
    # Requires OPENAI_API_KEY environment variable
    ...

class AnthropicBackend(LLMBackend):
    # Calls Anthropic Messages API with model: claude-opus-4-6 (default)
    # Requires ANTHROPIC_API_KEY environment variable
    # Model overridable via CLAUDE_MODEL environment variable
    ...
```

The active backend is selected by the `LLM_BACKEND` environment variable (`ollama`, `openai`, or `claude`).

#### 5.2.4 Evaluation Harness

| Property      | Value                                                                    |
| ------------- | ------------------------------------------------------------------------ |
| **Language**  | Python 3.12                                                              |
| **Framework** | Standalone script using `ragas` library                                  |
| **Role**      | Measures retrieval and generation quality against a curated test dataset |

**Evaluation Dataset** (`services/eval/eval_dataset.json`):

A manually curated JSON file containing at least 30 question-answer-context triples:

```json
[
  {
    "question": "What was Apple's total revenue in fiscal year 2024?",
    "ground_truth": "Apple's total net revenue for fiscal year 2024 was $391.0 billion.",
    "ticker": "AAPL"
  }
]
```

**Metrics Computed:**

| Metric            | Library | Description                                                                              |
| ----------------- | ------- | ---------------------------------------------------------------------------------------- |
| Context Precision | `ragas` | Proportion of retrieved chunks that are relevant to answering the question               |
| Faithfulness      | `ragas` | Proportion of claims in the generated answer that are supported by the retrieved context |
| Answer Relevancy  | `ragas` | Semantic similarity between the question and the generated answer                        |

**Output:**

- JSON report at `services/eval/results/eval_report_{timestamp}.json`
- Summary table appended to `docs/evaluation-results.md`

### 5.3 Data Model

#### PostgreSQL Schema

```sql
-- Enable pgvector extension
CREATE EXTENSION IF NOT EXISTS vector;

-- Ingestion tracking
CREATE TABLE ingestion_log (
    accession_number VARCHAR(30) PRIMARY KEY,
    ticker           VARCHAR(10)  NOT NULL,
    company_name     VARCHAR(200) NOT NULL,
    filing_date      DATE         NOT NULL,
    filing_type      VARCHAR(10)  NOT NULL DEFAULT '10-K',
    source_url       TEXT         NOT NULL,
    status           VARCHAR(20)  NOT NULL DEFAULT 'PUBLISHED',
    chunk_count      INTEGER,
    ingested_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    completed_at     TIMESTAMPTZ
);

CREATE INDEX idx_ingestion_ticker ON ingestion_log(ticker);
CREATE INDEX idx_ingestion_status ON ingestion_log(status);

-- Document chunks with embeddings
CREATE TABLE document_chunks (
    chunk_id         VARCHAR(64)  PRIMARY KEY,  -- SHA256 hash
    accession_number VARCHAR(30)  NOT NULL REFERENCES ingestion_log(accession_number),
    ticker           VARCHAR(10)  NOT NULL,
    filing_date      DATE         NOT NULL,
    section_name     VARCHAR(100) NOT NULL,
    chunk_index      INTEGER      NOT NULL,
    chunk_text       TEXT         NOT NULL,
    token_count      INTEGER      NOT NULL,
    embedding        vector(384)  NOT NULL,     -- all-MiniLM-L6-v2 output dimension
    created_at       TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

-- HNSW index for fast approximate nearest neighbor search
CREATE INDEX idx_chunks_embedding ON document_chunks
    USING hnsw (embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 200);

CREATE INDEX idx_chunks_ticker ON document_chunks(ticker);
CREATE INDEX idx_chunks_accession ON document_chunks(accession_number);
```

#### Kafka Topics

| Topic              | Key              | Value Schema     | Partitions | Retention |
| ------------------ | ---------------- | ---------------- | ---------- | --------- |
| `filings.raw`      | accession_number | Filing JSON      | 6          | 7 days    |
| `filings.embedded` | accession_number | Chunk ID array   | 6          | 7 days    |
| `filings.dlq`      | accession_number | Error + original | 3          | 30 days   |

### 5.4 Technology Choices and Rationale

| Decision                | Choice                 | Rationale                                                                                                                                                                                                             |
| ----------------------- | ---------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Vector store            | PostgreSQL + pgvector  | Avoids introducing a separate vector database (Pinecone, Weaviate). PostgreSQL is battle-tested, and pgvector's HNSW index is sufficient for our scale (500K vectors). One fewer infrastructure component to operate. |
| Embedding model         | all-MiniLM-L6-v2       | 384 dimensions, ~80 MB model, runs on CPU, good quality for retrieval tasks. No GPU required, no API cost. Well-benchmarked on MTEB leaderboard.                                                                      |
| LLM (default)           | Ollama with mistral:7b | Runs locally, no API cost, sufficient quality for document QA with good instruction following. Ollama provides a simple REST API, easy to containerize. Started via Docker Compose `--profile local-llm`.             |
| LLM (alternative)       | OpenAI gpt-4o-mini     | Higher quality answers, useful for evaluation comparison, but requires API key and incurs cost. Offered as an alternative backend, not the default.                                                                   |
| LLM (alternative)       | Claude (Anthropic)     | High quality answers via Anthropic Messages API. No local GPU or RAM beyond base stack. Model defaults to `claude-opus-4-6`, overridable via `CLAUDE_MODEL`. Requires `ANTHROPIC_API_KEY`.                            |
| Message queue           | Apache Kafka           | Event-driven decoupling between ingestion and embedding. Provides durability, replayability, and consumer group semantics for parallel processing.                                                                    |
| API framework           | FastAPI                | Async-native, automatic OpenAPI doc generation, Pydantic validation, high performance. Dominant in Python ML/AI ecosystem.                                                                                            |
| Programming language    | Python 3.12            | Required for ML/AI library ecosystem (sentence-transformers, ragas, LangChain). Type hints + mypy for safety.                                                                                                         |
| Chunking token counting | tiktoken (cl100k_base) | Fast, deterministic token counting. cl100k_base is the encoding used by modern OpenAI models and is a reasonable proxy for other models' tokenization.                                                                |

---

## 6. Scalability

### 6.1 Scaling Strategy Overview

```
                    ┌─────────────────────────────────────────┐
                    │           Load Balancer (K8s Service)    │
                    └───────────────┬─────────────────────────┘
                                    │
                    ┌───────────────▼─────────────────────────┐
                    │         Query API (HPA: 2-8 replicas)    │
                    │    Scales on: CPU > 70% or                │
                    │    request latency p95 > 5s               │
                    └───────────────┬─────────────────────────┘
                                    │
            ┌───────────────────────┼───────────────────────┐
            ▼                       ▼                       ▼
   ┌────────────────┐    ┌──────────────────┐    ┌─────────────────┐
   │  PostgreSQL    │    │  Ollama LLM      │    │  Embedding      │
   │  + pgvector    │    │  (1 replica,     │    │  Model          │
   │  (1 primary)   │    │   GPU optional)  │    │  (in-process)   │
   └────────────────┘    └──────────────────┘    └─────────────────┘
```

### 6.2 Bottleneck Analysis

| Component        | Bottleneck                        | Mitigation                                                                                                        |
| ---------------- | --------------------------------- | ----------------------------------------------------------------------------------------------------------------- |
| Query API        | CPU-bound embedding generation    | Embedding model is loaded per replica; HPA scales replicas horizontally.                                          |
| Query API        | LLM generation latency            | LLM call is async and non-blocking; multiple concurrent queries can be in-flight.                                 |
| Embedding Worker | CPU-bound batch embedding         | Kafka consumer group allows adding more worker replicas; partitions distribute load.                              |
| PostgreSQL       | Vector search on large tables     | HNSW index with tuned `ef_search` parameter. At 500K vectors, a single PostgreSQL instance is sufficient.         |
| Ollama           | Sequential generation on CPU      | Single-request bottleneck. Mitigation: use GPU if available, or fall back to OpenAI API for concurrent workloads. |
| Kafka            | Unlikely bottleneck at this scale | 6 partitions per topic supports up to 6 parallel consumers.                                                       |

### 6.3 Scaling Limits and Thresholds

| Scenario                | Expected Limit            | Scaling Action                                                                        |
| ----------------------- | ------------------------- | ------------------------------------------------------------------------------------- |
| < 100K chunks           | Single Query API replica  | No scaling needed                                                                     |
| 100K – 500K chunks      | 2-4 Query API replicas    | HPA activates; increase `ef_search` for HNSW index                                    |
| > 500K chunks           | Beyond project scope      | Would require: pgvector partitioning by ticker, read replicas, or dedicated vector DB |
| > 10 concurrent queries | Ollama becomes bottleneck | Switch to OpenAI backend or add GPU for Ollama                                        |

---

## 7. Resilience

### 7.1 Failure Modes and Mitigations

```
┌─────────────────────────────────────────────────────────────────────┐
│                     Failure Handling Overview                        │
│                                                                     │
│  SEC EDGAR ──X──▶ Ingestion Service                                │
│                   └─▶ Retry with exponential backoff (3 attempts)   │
│                   └─▶ Log error, skip filing, continue              │
│                                                                     │
│  Kafka ──X──▶ Ingestion Service / Embedding Worker                 │
│               └─▶ confluent-kafka auto-reconnect                    │
│               └─▶ Health endpoint returns unhealthy                 │
│               └─▶ K8s liveness probe restarts pod                   │
│                                                                     │
│  Embedding Worker ──X──▶ Processing fails                          │
│                         └─▶ Publish to filings.dlq (dead letter)   │
│                         └─▶ Log error with accession number        │
│                         └─▶ Commit offset, continue                │
│                                                                     │
│  PostgreSQL ──X──▶ Query API / Embedding Worker                    │
│                    └─▶ Connection pool retries (3 attempts)         │
│                    └─▶ Health endpoint returns unhealthy            │
│                    └─▶ Query API returns HTTP 503                   │
│                                                                     │
│  Ollama ──X──▶ Query API                                           │
│               └─▶ Timeout after 30 seconds                          │
│               └─▶ Return HTTP 200 with degraded: true               │
│                   (retrieved sources without LLM answer)            │
│                                                                     │
│  OpenAI / Claude API ──X──▶ Query API                              │
│                    └─▶ Retry with exponential backoff               │
│                        (max_retries=2 → 3 total attempts)           │
│                    └─▶ If still failing, return HTTP 200 degraded   │
└─────────────────────────────────────────────────────────────────────┘
```

### 7.2 Idempotency

| Component         | Idempotency Mechanism                                                                                                                                        |
| ----------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| Ingestion Service | Checks `ingestion_log` for existing accession number before publishing to Kafka. Duplicate ingestion triggers are safe.                                      |
| Embedding Worker  | `chunk_id` is a deterministic SHA256 hash. Upserting (`INSERT ... ON CONFLICT DO NOTHING`) ensures reprocessing the same message produces no duplicates.     |
| Kafka Consumer    | Manual offset commit after successful database write. If the worker crashes mid-processing, the message is redelivered and the upsert handles deduplication. |

### 7.3 Dead Letter Queue

Messages that fail processing in the embedding worker after 3 retry attempts are published to the `filings.dlq` topic with:

```json
{
  "original_message": { ... },
  "error": "Section parsing failed: unexpected format in Item 7A",
  "failed_at": "2026-03-10T14:30:00Z",
  "retry_count": 3
}
```

The DLQ topic has 30-day retention, allowing manual inspection and replay.

### 7.4 Graceful Degradation

| Failure                       | Degraded Behavior                                                                                                                             |
| ----------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------- |
| LLM unavailable               | Query API returns retrieved sources with relevance scores but no generated answer. HTTP 200 with `"answer": null` and `"degraded": true`.     |
| Embedding model fails to load | Service health endpoint returns unhealthy; K8s does not route traffic. Pod restarts.                                                          |
| Kafka unavailable             | Ingestion service returns HTTP 503. Embedding worker pauses (Kafka consumer poll loop blocks). Both recover automatically when Kafka returns. |

---

## 8. Observability

### 8.1 Metrics

All services expose Prometheus metrics on `/metrics` (port 9090).

#### 8.1.1 Ingestion Service Metrics

| Metric Name                               | Type      | Labels             | Description                                   |
| ----------------------------------------- | --------- | ------------------ | --------------------------------------------- |
| `findoc_ingestion_filings_fetched_total`  | Counter   | `ticker`, `status` | Total filings fetched (success/error/skipped) |
| `findoc_ingestion_edgar_request_duration` | Histogram | `ticker`           | SEC EDGAR API request latency                 |
| `findoc_ingestion_kafka_publish_total`    | Counter   | `topic`, `status`  | Messages published to Kafka (success/error)   |

#### 8.1.2 Embedding Worker Metrics

| Metric Name                               | Type      | Labels             | Description                                |
| ----------------------------------------- | --------- | ------------------ | ------------------------------------------ |
| `findoc_embedding_chunks_processed_total` | Counter   | `ticker`, `status` | Chunks embedded and stored (success/error) |
| `findoc_embedding_batch_duration`         | Histogram | —                  | Time to embed a batch of chunks            |
| `findoc_embedding_chunk_tokens`           | Histogram | —                  | Token count distribution per chunk         |
| `findoc_embedding_dlq_messages_total`     | Counter   | —                  | Messages sent to dead letter queue         |
| `findoc_embedding_kafka_lag`              | Gauge     | `partition`        | Consumer lag per partition                 |

#### 8.1.3 Query API Metrics

| Metric Name                             | Type      | Labels              | Description                                    |
| --------------------------------------- | --------- | ------------------- | ---------------------------------------------- |
| `findoc_query_requests_total`           | Counter   | `endpoint`,`status` | Total API requests by endpoint and HTTP status |
| `findoc_query_embedding_duration`       | Histogram | —                   | Time to embed the user's query                 |
| `findoc_query_retrieval_duration`       | Histogram | —                   | Time for pgvector similarity search            |
| `findoc_query_llm_duration`             | Histogram | `backend`           | Time for LLM generation (ollama/openai)        |
| `findoc_query_total_duration`           | Histogram | —                   | Total end-to-end query latency                 |
| `findoc_query_retrieval_score`          | Histogram | —                   | Distribution of top-1 relevance scores         |
| `findoc_query_llm_tokens_used`          | Counter   | `backend`, `type`   | Tokens consumed (prompt/completion)            |
| `findoc_query_degraded_responses_total` | Counter   | `reason`            | Responses returned in degraded mode            |

### 8.2 Grafana Dashboards

The project includes two pre-built Grafana dashboards as JSON files in `monitoring/grafana/dashboards/`:

**Dashboard 1: Ingestion & Embedding Pipeline**

- Filing ingestion rate (filings/minute) by ticker
- Embedding worker throughput (chunks/second)
- Kafka consumer lag by partition
- DLQ message rate
- Chunk token count distribution

**Dashboard 2: Query Performance**

- Query request rate and error rate
- Latency breakdown: embedding → retrieval → LLM generation (stacked)
- p50 / p95 / p99 total query latency
- Top-1 retrieval relevance score distribution
- LLM token consumption rate
- Degraded response rate

### 8.3 Structured Logging

All services use Python's `structlog` library with JSON output:

```json
{
  "timestamp": "2026-03-10T12:00:00Z",
  "level": "info",
  "service": "query-api",
  "event": "query_completed",
  "question_length": 82,
  "top_k": 5,
  "retrieval_ms": 45,
  "generation_ms": 3200,
  "total_ms": 3257,
  "model": "mistral:7b",
  "top_relevance_score": 0.87,
  "request_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
}
```

Every log entry includes a `request_id` (UUID) for tracing a request across log entries. The request ID is set from the `X-Request-ID` header if provided, or generated by the API.

### 8.4 Embedding Drift Detection

A scheduled job (Kubernetes CronJob, weekly) computes the mean embedding vector across all chunks ingested in the past 7 days and compares it (cosine distance) against the overall corpus mean. If the drift exceeds a configurable threshold (default: 0.15), a warning metric is emitted:

| Metric Name                    | Type  | Description                                               |
| ------------------------------ | ----- | --------------------------------------------------------- |
| `findoc_embedding_drift_score` | Gauge | Cosine distance between recent and corpus mean embeddings |
| `findoc_embedding_drift_alert` | Gauge | 1 if drift exceeds threshold, 0 otherwise                 |

---

## 9. Security

### 9.1 Authentication and Authorization

| Mechanism         | Implementation                                                                                                                                                             |
| ----------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| API Key Auth      | The Query API requires an `X-API-Key` header. Valid keys are stored as a comma-separated environment variable (`API_KEYS`). Requests without a valid key receive HTTP 401. |
| Internal Services | The Ingestion Service and Embedding Worker are not exposed externally. They communicate only via Kafka and PostgreSQL within the Kubernetes cluster network.               |
| Ollama            | Runs as a cluster-internal service with no external exposure. Accessed by the Query API over the internal K8s network.                                                     |

### 9.2 Data Security

| Concern              | Mitigation                                                                                                                                                                                                                                                                    |
| -------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Data at rest         | All data (filings, embeddings) is public SEC data. No PII or proprietary information.                                                                                                                                                                                         |
| Data in transit      | All Kubernetes internal communication uses cluster networking. External API access uses HTTPS (via K8s Ingress TLS termination).                                                                                                                                              |
| API Keys             | Stored as Kubernetes Secrets, mounted as environment variables. Not logged or included in API responses.                                                                                                                                                                      |
| OpenAI API Key       | Stored as a Kubernetes Secret. Only the Query API pod has access. Not logged.                                                                                                                                                                                                 |
| LLM Prompt Injection | The system prompt is hardcoded (not user-modifiable). User input is placed only in the `Question:` field of the prompt template. Retrieved context is clearly delineated. This is a defense-in-depth measure, not a guarantee — prompt injection is an open research problem. |

### 9.3 Rate Limiting

| Limit     | Value                  | Implementation                                              |
| --------- | ---------------------- | ----------------------------------------------------------- |
| Query API | 30 requests/minute/key | `slowapi` library (token bucket per API key)                |
| SEC EDGAR | 10 requests/second     | `aiohttp` with a semaphore limiter in the ingestion service |

### 9.4 Dependency Management

- All Python dependencies are pinned with exact versions in `requirements.txt` (generated from `pip-compile`).
- Docker images use specific version tags, not `latest`.
- GitHub Actions workflow includes a `pip-audit` step to check for known vulnerabilities in dependencies.

---

## 10. Deployment

### 10.1 Repository Structure

```
findoc-rag/
├── README.md
├── Makefile
├── docker-compose.yml
├── config/
│   └── tickers.yml
├── .github/
│   └── workflows/
│       ├── ci.yml
│       └── docker-publish.yml
├── services/
│   ├── ingestion/
│   │   ├── Dockerfile
│   │   ├── requirements.txt
│   │   ├── src/
│   │   │   ├── main.py
│   │   │   ├── edgar_client.py
│   │   │   ├── kafka_producer.py
│   │   │   └── config.py
│   │   └── tests/
│   │       └── test_edgar_client.py
│   ├── embedding-worker/
│   │   ├── Dockerfile
│   │   ├── requirements.txt
│   │   ├── src/
│   │   │   ├── main.py
│   │   │   ├── chunker.py
│   │   │   ├── embedder.py
│   │   │   └── store.py
│   │   └── tests/
│   │       ├── test_chunker.py
│   │       └── test_embedder.py
│   ├── query-api/
│   │   ├── Dockerfile
│   │   ├── requirements.txt
│   │   ├── src/
│   │   │   ├── main.py
│   │   │   ├── rag/
│   │   │   │   ├── retriever.py
│   │   │   │   ├── generator.py
│   │   │   │   └── prompts.py
│   │   │   ├── llm/
│   │   │   │   ├── backend.py
│   │   │   │   ├── ollama_backend.py
│   │   │   │   └── openai_backend.py
│   │   │   ├── auth.py
│   │   │   └── models.py
│   │   └── tests/
│   │       ├── test_retriever.py
│   │       └── test_generator.py
│   └── eval/
│       ├── requirements.txt
│       ├── eval_dataset.json
│       └── run_eval.py
├── helm/
│   └── findoc-rag/
│       ├── Chart.yaml
│       ├── values.yaml
│       └── templates/
│           ├── ingestion-deployment.yaml
│           ├── embedding-worker-deployment.yaml
│           ├── query-api-deployment.yaml
│           ├── ollama-deployment.yaml
│           ├── postgresql-statefulset.yaml
│           ├── kafka-statefulset.yaml
│           ├── prometheus-deployment.yaml
│           ├── grafana-deployment.yaml
│           ├── configmaps.yaml
│           ├── secrets.yaml
│           ├── services.yaml
│           ├── hpa.yaml
│           ├── ingress.yaml
│           └── cronjob-drift-detection.yaml
├── monitoring/
│   ├── prometheus.yml
│   └── grafana/
│       └── dashboards/
│           ├── pipeline-dashboard.json
│           └── query-dashboard.json
├── db/
│   └── migrations/
│       └── 001_initial_schema.sql
└── docs/
    ├── technical-design-document.md
    ├── design-decisions.md
    └── evaluation-results.md
```

### 10.2 Local Development (Docker Compose)

A single `docker compose up` brings up the full stack locally:

```yaml
# Simplified overview of docker-compose.yml services
services:
  postgres: # PostgreSQL 16 with pgvector extension
  kafka: # Apache Kafka (KRaft mode, no Zookeeper)
  ollama: # Ollama with mistral:7b model pre-pulled
  ingestion: # Ingestion Service
  embedding-worker: # Embedding Worker
  query-api: # Query API (exposed on port 8000)
  prometheus: # Prometheus (scrapes all services)
  grafana: # Grafana (pre-configured dashboards, port 3000)
```

Startup order is managed with `depends_on` and health checks:

1. PostgreSQL + Kafka start first
2. Database migrations run (init container)
3. Ollama starts and pulls the model
4. Ingestion Service, Embedding Worker, and Query API start
5. Prometheus and Grafana start last

### 10.3 CI/CD Pipeline (GitHub Actions)

#### Workflow 1: `ci.yml` (on every push and PR)

```
┌──────────┐    ┌──────────┐    ┌──────────┐    ┌──────────┐
│  Lint    │───▶│  Type    │───▶│  Unit    │───▶│  Audit   │
│  (ruff)  │    │  Check   │    │  Tests   │    │  (pip-   │
│          │    │  (mypy)  │    │ (pytest) │    │  audit)  │
└──────────┘    └──────────┘    └──────────┘    └──────────┘
```

- Runs for each service in a matrix: `[ingestion, embedding-worker, query-api]`
- Python 3.12
- Coverage report uploaded as artifact

#### Workflow 2: `docker-publish.yml` (on push to `main`)

```
┌──────────────┐    ┌──────────────┐    ┌──────────────┐
│  Build       │───▶│  Push to     │───▶│  Update      │
│  Docker      │    │  GitHub      │    │  Helm        │
│  Images      │    │  Container   │    │  image tags  │
│              │    │  Registry    │    │              │
└──────────────┘    └──────────────┘    └──────────────┘
```

- Builds multi-arch images (amd64/arm64) for each service
- Pushes to GitHub Container Registry (`ghcr.io/drag0sd0g/findoc-rag/*`)
- Tags: `latest` + git SHA

### 10.4 Kubernetes Deployment (Helm)

#### Resource Allocation

| Component         | Replicas     | CPU Request | CPU Limit | Memory Request | Memory Limit |
| ----------------- | ------------ | ----------- | --------- | -------------- | ------------ |
| Query API         | 2 (HPA: 2-8) | 500m        | 2000m     | 1Gi            | 2Gi          |
| Embedding Worker  | 2            | 1000m       | 2000m     | 2Gi            | 4Gi          |
| Ingestion Service | 1            | 250m        | 500m      | 512Mi          | 1Gi          |
| Ollama            | 1            | 2000m       | 4000m     | 8Gi            | 12Gi         |
| PostgreSQL        | 1            | 500m        | 2000m     | 1Gi            | 4Gi          |
| Kafka (KRaft)     | 1            | 500m        | 1000m     | 1Gi            | 2Gi          |
| Prometheus        | 1            | 250m        | 500m      | 512Mi          | 1Gi          |
| Grafana           | 1            | 100m        | 250m      | 256Mi          | 512Mi        |

#### Horizontal Pod Autoscaler (Query API)

```yaml
apiVersion: autoscaling/v2
kind: HorizontalPodAutoscaler
metadata:
  name: query-api-hpa
spec:
  scaleTargetRef:
    apiVersion: apps/v1
    kind: Deployment
    name: query-api
  minReplicas: 2
  maxReplicas: 8
  metrics:
    - type: Resource
      resource:
        name: cpu
        target:
          type: Utilization
          averageUtilization: 70
```

#### Kubernetes Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                        Kubernetes Cluster                           │
│                                                                     │
│  ┌──────────────────────────────────────────────────────────────┐   │
│  │  Namespace: findoc-rag                                       │   │
│  │                                                              │   │
│  │  ┌────────────┐  ┌────────────┐  ┌────────────────────────┐ │   │
│  │  │ Ingress    │─▶│ Query API  │─▶│ Ollama                 │ │   │
│  │  │ (TLS)     │  │ (2-8 pods) │  │ (1 pod, 8Gi+ RAM)     │ │   │
│  │  └────────────┘  └─────┬──────┘  └────────────────────────┘ │   │
│  │                        │                                     │   │
│  │                        ▼                                     │   │
│  │               ┌────────────────┐                             │   │
│  │               │  PostgreSQL    │                             │   │
│  │               │  + pgvector    │                             │   │
│  │               │  (StatefulSet) │                             │   │
│  │               └────────────────┘                             │   │
│  │                        ▲                                     │   │
│  │                        │                                     │   │
│  │  ┌────────────┐  ┌────┴───────┐  ┌────────────────────────┐ │   │
│  │  │ Ingestion  │─▶│   Kafka    │◀─│ Embedding Worker       │ │   │
│  │  │ (1 pod)    │  │  (KRaft)   │  │ (2 pods)               │ │   │
│  │  └────────────┘  └────────────┘  └────────────────────────┘ │   │
│  │                                                              │   │
│  │  ┌────────────┐  ┌────────────┐  ┌────────────────────────┐ │   │
│  │  │ Prometheus │─▶│  Grafana   │  │ Drift CronJob (weekly) │ │   │
│  │  │            │  │            │  │                         │ │   │
│  │  └────────────┘  └────────────┘  └────────────────────────┘ │   │
│  └──────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────┘
```

### 10.5 Makefile Targets

```makefile
setup:          # Create virtual environments, install dependencies
test:           # Run pytest for all services
lint:           # Run ruff + mypy for all services
run:            # docker compose up (full local stack)
eval:           # Run RAG evaluation harness
docker-build:   # Build Docker images for all services
helm-deploy:    # Deploy to Kubernetes via Helm
helm-teardown:  # Remove Helm release
migrate:        # Run database migrations
```
