# FinDoc RAG

A Retrieval-Augmented Generation platform for financial document intelligence. FinDoc RAG ingests SEC 10-K filings, chunks and embeds them into a vector store, and exposes a REST API that answers natural language questions with cited sources.

## Overview

The system is composed of three independently deployable services connected by Apache Kafka and a shared PostgreSQL database with the pgvector extension:

- **Ingestion Service** -- Fetches 10-K filings from the SEC EDGAR full-text search API and publishes raw filing text to Kafka.
- **Embedding Worker** -- Consumes raw filings from Kafka, splits them into section-aware chunks, generates dense vector embeddings, and stores the results in pgvector.
- **Query API** -- Accepts natural language questions, retrieves the most relevant document chunks via cosine similarity, constructs a grounded prompt, and returns an LLM-generated answer with source citations.

```
SEC EDGAR  -->  Ingestion  -->  Kafka  -->  Embedding Worker  -->  PostgreSQL + pgvector
                                                                         ^
API Client  <-->  Query API  <-->  Ollama / OpenAI / Claude              |
                                      |                                  |
                                      +----------------------------------+
```

## Prerequisites

- [Docker](https://docs.docker.com/get-docker/) and [Docker Compose](https://docs.docker.com/compose/install/) (v2+)
- Python 3.12+ (for local development and running tests outside of Docker)
- GNU Make (optional, for convenience targets)

## Quick Start

1. Clone the repository and copy the environment template:

```bash
git clone https://github.com/drag0sd0g/FinDocRAG.git
cd FinDocRAG
cp .env.example .env
```

2. Review `.env` and set `EDGAR_USER_AGENT` to a value that identifies you (the SEC requires a name and contact email):

```
EDGAR_USER_AGENT=YourName your.email@example.com
```

3. Start the full stack:

```bash
# With Ollama (local LLM, no API key needed):
make run
# or: docker compose --profile local-llm up --build

# With a remote LLM (Claude or OpenAI, no GPU/RAM required):
export LLM_BACKEND=claude          # or: export LLM_BACKEND=openai
export ANTHROPIC_API_KEY=sk-ant-…  # or: export OPENAI_API_KEY=sk-…
make run-remote
# or: docker compose up --build
```

On the first run this will pull container images, build the three services, and run database migrations. If using Ollama (`make run`), the LLM model is downloaded automatically on first start (~4 GB). Subsequent starts reuse cached layers and volumes.

4. Verify the services are running:

```bash
curl http://localhost:8001/health   # Ingestion Service
curl http://localhost:8002/health   # Embedding Worker
curl http://localhost:8000/health   # Query API
```

## Usage

### Ingest filings

Trigger ingestion for specific tickers:

```bash
curl -X POST http://localhost:8001/v1/ingest \
  -H "Content-Type: application/json" \
  -d '{"tickers": ["AAPL", "MSFT"]}'
```

If no body is provided, the service reads from `config/tickers.yml`.

### Query the knowledge base

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

The response includes the generated answer, source citations with relevance scores, and a latency breakdown.

### List ingested documents

```bash
curl http://localhost:8000/v1/documents \
  -H "X-API-Key: dev-key-1"
```

Supports optional query parameters `ticker`, `limit`, and `offset`.

### Interactive API documentation

Once the Query API is running, auto-generated OpenAPI (Swagger) documentation is available at:

```
http://localhost:8000/docs
```

## Project Structure

```
findoc-rag/
  config/tickers.yml              Tickers to ingest (FR-2)
  db/migrations/                  PostgreSQL schema (pgvector)
  services/
    ingestion/                    SEC EDGAR fetcher + Kafka producer
    embedding-worker/             Chunker + sentence-transformers + pgvector writer
    query-api/                    RAG pipeline: retriever, prompt builder, LLM backends
    eval/                         Evaluation harness (ragas)
  helm/findoc-rag/                Kubernetes Helm chart
  monitoring/                     Prometheus config + Grafana dashboards
  docs/
    technical-design-document.md  Full TDD (architecture, requirements, decisions)
    design-decisions.md           ADRs and trade-off analysis
    evaluation-results.md         RAG quality metrics
  docker-compose.yml              Local full-stack orchestration
  Makefile                        Development workflow targets
```

## Development

### Local setup (without Docker)

```bash
make setup    # Creates venvs and installs dependencies for all services
```

### Running tests

```bash
make test     # Runs pytest with coverage for all three services
```

Tests mock all external dependencies (Kafka, PostgreSQL, EDGAR, Ollama) and run without any infrastructure.

### Linting and type checking

```bash
make lint     # Runs ruff (linter) and mypy (type checker)
```

### Makefile targets

| Target               | Description                                                       |
| -------------------- | ----------------------------------------------------------------- |
| `make setup`         | Create virtual environments, install dependencies                 |
| `make test`          | Run pytest for all services                                       |
| `make lint`          | Run ruff and mypy                                                 |
| `make run`           | Start full stack including Ollama (`--profile local-llm`)         |
| `make run-remote`    | Start stack without Ollama (for `LLM_BACKEND=claude` or `openai`) |
| `make stop`          | Stop Docker Compose stack                                         |
| `make clean`         | Stop stack and remove all volumes                                 |
| `make migrate`       | Run database migrations                                           |
| `make docker-build`  | Build Docker images only                                          |
| `make eval`          | Run the RAG evaluation harness                                    |
| `make helm-deploy`   | Deploy to Kubernetes via Helm                                     |
| `make helm-test`     | Run Helm unit tests (requires helm-unittest plugin)               |
| `make helm-teardown` | Remove the Helm release                                           |

## Configuration

All configuration is driven by environment variables. See `.env.example` for the full list with descriptions. Key variables:

| Variable            | Default                                  | Description                                   |
| ------------------- | ---------------------------------------- | --------------------------------------------- |
| `LLM_BACKEND`       | `ollama`                                 | LLM provider: `ollama`, `openai`, or `claude` |
| `OLLAMA_MODEL`      | `mistral:7b`                             | Model to use when backend is Ollama           |
| `OPENAI_API_KEY`    | (empty)                                  | Required when `LLM_BACKEND=openai`            |
| `ANTHROPIC_API_KEY` | (empty)                                  | Required when `LLM_BACKEND=claude`            |
| `CLAUDE_MODEL`      | `claude-opus-4-6`                        | Claude model to use when backend is claude    |
| `API_KEYS`          | `dev-key-1,dev-key-2`                    | Comma-separated valid API keys                |
| `EDGAR_USER_AGENT`  | `FinDocRAG findocrag@example.com`        | SEC-required identification string            |
| `EMBEDDING_MODEL`   | `sentence-transformers/all-MiniLM-L6-v2` | Sentence-transformers model name              |

## LLM Backends

The Query API supports two LLM backends, selectable via the `LLM_BACKEND` environment variable:

**Ollama (default)** -- Runs locally inside the Docker Compose stack. No API key required. The model is pulled automatically on first start. Start with `make run` (or `docker compose --profile local-llm up`). Suitable for development and self-hosted deployments. Requires ~6 GB RAM for `mistral:7b`.

**OpenAI** -- Calls the OpenAI chat completions API. Set `LLM_BACKEND=openai` and provide a valid `OPENAI_API_KEY`. Uses `gpt-4o-mini` by default. Start with `make run-remote`. Useful for higher-quality answers and evaluation comparisons.

**Claude (Anthropic)** -- Calls the Anthropic messages API. Set `LLM_BACKEND=claude` and provide a valid `ANTHROPIC_API_KEY`. Uses `claude-opus-4-6` by default (override with `CLAUDE_MODEL`). Start with `make run-remote`. No local GPU or RAM requirements beyond the base stack.

## API Authentication

The Query API requires an `X-API-Key` header. Valid keys are configured via the `API_KEYS` environment variable. If `API_KEYS` is empty or unset, authentication is disabled (development mode).

Rate limiting is enforced at 30 requests per minute per API key.

## Graceful Degradation

If the LLM backend is unavailable, the Query API returns the retrieved source chunks with relevance scores but without a generated answer. The response includes `"degraded": true` to signal this state. This allows downstream consumers to still present relevant context to users.

## Documentation

| Document                                                       | Description                                                                                                                                                   |
| -------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| [Technical Design Document](docs/technical-design-document.md) | Architecture, functional and non-functional requirements, data model, technology rationale, scalability, resilience, observability, security, and deployment. |
| [Design Decisions](docs/design-decisions.md)                   | Architectural decision records with trade-off analysis.                                                                                                       |
| [Evaluation Results](docs/evaluation-results.md)               | RAG quality metrics (context precision, faithfulness, answer relevancy).                                                                                      |

## License

This project is a portfolio/demonstration project and is not intended for production multi-tenant use. See the [Technical Design Document](docs/technical-design-document.md) Section 2.4 for scope boundaries.
