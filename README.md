[日本語](README.ja.md)

# FinDoc RAG

A Retrieval-Augmented Generation platform for financial document intelligence. FinDoc RAG ingests SEC 10-K filings, chunks and embeds them into a vector store, and exposes a REST API that answers natural language questions with cited sources.

## Overview

The system is composed of three independently deployable services connected by Apache Kafka and a shared PostgreSQL database with the pgvector extension:

- **Ingestion Service** -- Fetches 10-K filings from the SEC EDGAR full-text search API and publishes raw filing text to Kafka.
- **Embedding Worker** -- Consumes raw filings from Kafka, splits them into section-aware chunks, generates dense vector embeddings, and stores the results in pgvector. Chunking is hierarchical: 10-K items are split by section (Item 1A, Item 7, etc.), then by paragraph, then into 512-token windows with 64-token overlap. Each chunk retains the section name, ticker, filing date, and a deterministic SHA256 chunk ID.
- **Query API** -- Accepts natural language questions, embeds the query and retrieves candidate chunks via cosine distance, applies Maximal Marginal Relevance (MMR) reranking to balance relevance and diversity, constructs a grounded prompt, and returns an LLM-generated answer with source citations.

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

**Hardware (Ollama / local LLM path only):** `make run` pulls `mistral:7b` (~4 GB download) and requires at least **8 GB of free RAM** at runtime (model weights + Docker overhead). On machines with less RAM the Ollama container will be OOM-killed. The remote-LLM path (`make run-remote` with `LLM_BACKEND=claude` or `openai`) has no GPU or RAM requirements beyond the base stack (~2 GB).

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

On the first run this will pull container images, build the three services, and run database migrations. The schema is applied automatically from `db/migrations/001_initial_schema.sql` (creates the `pgvector` extension, `ingestion_log` and `document_chunks` tables, and the HNSW vector index). To run migrations manually at any time: `make migrate`.

If using Ollama (`make run`), the `mistral:7b` model (~4 GB) is downloaded automatically on first start. Subsequent starts reuse cached layers and volumes.

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

`top_k` is optional (default `5`, range `1`–`20`). Include an `X-Request-ID` header to correlate requests across log entries; if omitted, one is generated automatically and returned as `request_id` in the response.

The response shape:

```json
{
  "answer": "Apple's 2024 10-K identifies supply chain concentration as a key risk...",
  "sources": [
    {
      "chunk_id": "a1b2c3d4e5f6...",
      "ticker": "AAPL",
      "filing_date": "2024-11-01",
      "section": "Item 1A - Risk Factors",
      "relevance_score": 0.87,
      "text_preview": "The Company's operations and performance depend significantly on..."
    }
  ],
  "model": "mistral:7b",
  "timing": {
    "embedding_ms": 12.4,
    "retrieval_ms": 38.1,
    "generation_ms": 4821.0,
    "total_ms": 4871.5
  },
  "degraded": false,
  "request_id": "550e8400-e29b-41d4-a716-446655440000"
}
```

When the LLM is unavailable, `answer` is `null` and `degraded` is `true` (HTTP 200 — see [Graceful Degradation](#graceful-degradation)).

### List ingested documents

```bash
curl http://localhost:8000/v1/documents \
  -H "X-API-Key: dev-key-1"
```

Supports optional query parameters `ticker`, `limit`, and `offset`.

### Interactive API documentation

FastAPI generates OpenAPI 3.0 documentation automatically. All three services expose it while running:

| Service           | Swagger UI                           | ReDoc                                 | JSON spec                                  |
| ----------------- | ------------------------------------ | ------------------------------------- | ------------------------------------------ |
| Query API         | http://localhost:8000/docs           | http://localhost:8000/redoc           | http://localhost:8000/openapi.json         |
| Ingestion Service | http://localhost:8001/docs           | http://localhost:8001/redoc           | http://localhost:8001/openapi.json         |
| Embedding Worker  | http://localhost:8002/docs           | http://localhost:8002/redoc           | http://localhost:8002/openapi.json         |

To export a static spec without running the stack:

```bash
cd services/query-api
PYTHONPATH=. python -c "import json; from src.main import app; print(json.dumps(app.openapi(), indent=2))"
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
make helm-test # Runs the helm unit-tests
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

## Kubernetes Deployment

The `helm/findoc-rag/` chart deploys the full stack to any Kubernetes cluster. The five steps below cover a typical first-time deployment.

### Prerequisites

- `kubectl` configured against a running cluster (local: [kind](https://kind.sigs.k8s.io/) or [minikube](https://minikube.sigs.k8s.io/); cloud: GKE, EKS, AKS)
- [Helm](https://helm.sh/docs/intro/install/) v3.10+
- The `helm-unittest` plugin (only required for `make helm-test`):
  ```bash
  helm plugin install https://github.com/helm-unittest/helm-unittest
  ```

### Step 1 — Create the namespace

```bash
kubectl create namespace findoc-rag
```

### Step 2 — Create a production values override

Copy the defaults and change the values that must differ in production:

```bash
cp helm/findoc-rag/values.yaml helm/findoc-rag/values.prod.yaml
```

Key overrides for a production deployment:

```yaml
# helm/findoc-rag/values.prod.yaml

postgresql:
  credentials:
    password: "<strong-password>"   # never commit this; use --set or a sealed-secret

grafana:
  adminPassword: "<strong-password>"

ingress:
  enabled: true
  host: findoc-rag.example.com     # your domain
  tls:
    enabled: true
    secretName: findoc-rag-tls     # pre-created TLS secret

queryApi:
  apiKeys: "<key1>,<key2>"
  llmBackend: claude               # or: openai
```

### Step 3 — Push sensitive values as Kubernetes Secrets

```bash
# PostgreSQL credentials
kubectl create secret generic findoc-prod-postgresql \
  --namespace findoc-rag \
  --from-literal=POSTGRES_DB=findocrag \
  --from-literal=POSTGRES_USER=findocrag \
  --from-literal=POSTGRES_PASSWORD="<strong-password>"

# LLM API keys (only the key you need)
kubectl create secret generic findoc-prod-api-keys \
  --namespace findoc-rag \
  --from-literal=ANTHROPIC_API_KEY="sk-ant-..."
```

### Step 4 — Install with Helm

```bash
helm upgrade --install findoc-rag helm/findoc-rag \
  --namespace findoc-rag \
  --values helm/findoc-rag/values.prod.yaml \
  --set postgresql.credentials.password="<strong-password>" \
  --wait
```

To deploy **without Ollama** (remote LLM only, saves ~12 GB RAM):

```bash
helm upgrade --install findoc-rag helm/findoc-rag \
  --namespace findoc-rag \
  --values helm/findoc-rag/values.prod.yaml \
  --set queryApi.llmBackend=claude \
  --set ollama.replicas=0 \
  --wait
```

### Step 5 — Verify

```bash
# Check all pods reach Running / Completed state
kubectl get pods -n findoc-rag

# Tail logs for any service
kubectl logs -n findoc-rag -l app.kubernetes.io/component=query-api -f

# Port-forward the Query API for a quick smoke test
kubectl port-forward -n findoc-rag svc/findoc-rag-query-api 8000:8000
curl http://localhost:8000/health

# Port-forward Grafana dashboards
kubectl port-forward -n findoc-rag svc/findoc-rag-grafana 3000:3000
# open http://localhost:3000  (admin / <adminPassword>)
```

### Teardown

```bash
make helm-teardown
# or: helm uninstall findoc-rag --namespace findoc-rag
```

---

## Configuration

All configuration is driven by environment variables. See `.env.example` for the full list with descriptions. Key variables:

**LLM & model selection**

| Variable            | Default                                  | Description                                          |
| ------------------- | ---------------------------------------- | ---------------------------------------------------- |
| `LLM_BACKEND`       | `ollama`                                 | LLM provider: `ollama`, `openai`, or `claude`        |
| `OLLAMA_URL`        | `http://ollama:11434`                    | Ollama server URL (used when `LLM_BACKEND=ollama`)   |
| `OLLAMA_MODEL`      | `mistral:7b`                             | Ollama model name                                    |
| `OPENAI_API_KEY`    | (empty)                                  | Required when `LLM_BACKEND=openai`                   |
| `OPENAI_MODEL`      | `gpt-4o-mini`                            | OpenAI model name                                    |
| `ANTHROPIC_API_KEY` | (empty)                                  | Required when `LLM_BACKEND=claude`                   |
| `CLAUDE_MODEL`      | `claude-opus-4-6`                        | Claude model name                                    |
| `EMBEDDING_MODEL`   | `sentence-transformers/all-MiniLM-L6-v2` | Sentence-transformers model for embedding            |

**Security & API**

| Variable        | Default               | Description                                                             |
| --------------- | --------------------- | ----------------------------------------------------------------------- |
| `API_KEYS`      | `dev-key-1,dev-key-2` | Comma-separated valid API keys for the Query API                        |
| `RATE_LIMIT`    | `30/minute`           | Query API rate limit per API key (slowapi format, e.g. `60/minute`)     |
| `CORS_ORIGINS`  | `*`                   | Comma-separated allowed CORS origins (e.g. `https://myapp.example.com`) |

**Ingestion**

| Variable                   | Default                           | Description                                              |
| -------------------------- | --------------------------------- | -------------------------------------------------------- |
| `EDGAR_USER_AGENT`         | `FinDocRAG findocrag@example.com` | SEC-required identification string                       |
| `EDGAR_RATE_LIMIT_RPS`     | `10`                              | Max EDGAR API requests per second (SEC limit is 10 r/s)  |
| `KAFKA_BOOTSTRAP_SERVERS`  | `kafka:9092`                      | Kafka broker address used by the embedding worker        |

**Database**

| Variable            | Default      | Description                              |
| ------------------- | ------------ | ---------------------------------------- |
| `POSTGRES_HOST`     | `postgres`   | PostgreSQL hostname                      |
| `POSTGRES_PORT`     | `5432`       | PostgreSQL port                          |
| `POSTGRES_DB`       | `findocrag`  | Database name                            |
| `POSTGRES_USER`     | `findocrag`  | Database user                            |
| `POSTGRES_PASSWORD` | `changeme`   | Database password (override in production) |

**Observability**

| Variable        | Default | Description                                         |
| --------------- | ------- | --------------------------------------------------- |
| `LOG_LEVEL`     | `INFO`  | Log verbosity for all services (`DEBUG`, `INFO`, `WARNING`) |
| `QUERY_API_PORT` | `8000` | HTTP port for the Query API                         |

## LLM Backends

The Query API supports 3 LLM backends, selectable via the `LLM_BACKEND` environment variable:

**Ollama (default)** -- Runs locally inside the Docker Compose stack. No API key required. The model is pulled automatically on first start. Start with `make run` (or `docker compose --profile local-llm up`). Suitable for development and self-hosted deployments. Requires ~6 GB RAM for `mistral:7b`.

**OpenAI** -- Calls the OpenAI chat completions API. Set `LLM_BACKEND=openai` and provide a valid `OPENAI_API_KEY`. Uses `gpt-4o-mini` by default. Start with `make run-remote`. Useful for higher-quality answers and evaluation comparisons.

**Claude (Anthropic)** -- Calls the Anthropic messages API. Set `LLM_BACKEND=claude` and provide a valid `ANTHROPIC_API_KEY`. Uses `claude-opus-4-6` by default (override with `CLAUDE_MODEL`). Start with `make run-remote`. No local GPU or RAM requirements beyond the base stack.

## API Authentication

The Query API requires an `X-API-Key` header. Valid keys are configured via the `API_KEYS` environment variable. If `API_KEYS` is empty or unset, authentication is disabled (development mode).

Rate limiting is enforced at 30 requests per minute per API key.

## Graceful Degradation

If the LLM backend is unavailable (timeout, network error, or API failure), the Query API still returns **HTTP 200** with the retrieved source chunks and relevance scores, but `answer` is `null` and `degraded` is `true`:

```json
{
  "answer": null,
  "sources": [ ... ],
  "model": "mistral:7b",
  "timing": { ... },
  "degraded": true,
  "request_id": "..."
}
```

This allows downstream consumers to present relevant context to users even when generation is unavailable. The `degraded` field is the canonical signal — do not rely on `answer` being absent, as future versions may return a partial answer with `degraded: true`.

## Documentation

| Document                                                       | Description                                                                                                                                                   |
| -------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| [Technical Design Document](docs/technical-design-document.md) | Architecture, functional and non-functional requirements, data model, technology rationale, scalability, resilience, observability, security, and deployment. |
| [Design Decisions](docs/design-decisions.md)                   | Architectural decision records with trade-off analysis.                                                                                                       |
| [Evaluation Results](docs/evaluation-results.md)               | RAG quality metrics (context precision, faithfulness, answer relevancy).                                                                                      |

## License

This project is a portfolio/demonstration project and is not intended for production multi-tenant use. See the [Technical Design Document](docs/technical-design-document.md) Section 2.4 for scope boundaries.
