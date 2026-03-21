# Architectural Design Decisions

This document records the key architectural decisions made during the design and implementation of FinDoc RAG. Each decision follows a lightweight ADR (Architectural Decision Record) format: context, options considered, decision, and consequences.

These records complement Section 5.4 of the [Technical Design Document](technical-design-document.md) by providing the full trade-off analysis behind each choice.

---

## Table of Contents

1. [ADR-001: Vector Store](#adr-001-vector-store)
2. [ADR-002: Embedding Model](#adr-002-embedding-model)
3. [ADR-003: LLM Backend Strategy](#adr-003-llm-backend-strategy)
4. [ADR-004: Message Queue](#adr-004-message-queue)
5. [ADR-005: Chunking Strategy](#adr-005-chunking-strategy)
6. [ADR-006: API Framework](#adr-006-api-framework)
7. [ADR-007: Deployment Packaging](#adr-007-deployment-packaging)
8. [ADR-008: Observability Stack](#adr-008-observability-stack)
9. [ADR-009: Evaluation Framework](#adr-009-evaluation-framework)
10. [ADR-010: Kafka Topology and Consumer Design](#adr-010-kafka-topology-and-consumer-design)
11. [ADR-011: Retrieval Reranking Strategy (MMR)](#adr-011-retrieval-reranking-strategy-mmr)

---

## ADR-001: Vector Store

**Status:** Accepted

### Context

The system needs to store and query 384-dimensional embedding vectors for up to 500,000 document chunks. Retrieval must complete within 200ms at p99. The store must also hold the associated chunk text and metadata (ticker, filing date, section name).

### Options Considered

| Option                    | Description                       | Pros                                                                                                                                   | Cons                                                                                                                           |
| ------------------------- | --------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------ |
| **Pinecone**              | Managed vector database SaaS      | Fully managed, auto-scaling, high performance at scale                                                                                 | External dependency, recurring cost, data leaves the cluster, vendor lock-in                                                   |
| **Weaviate**              | Open-source vector database       | Purpose-built for vectors, rich filtering, GraphQL API                                                                                 | Additional infrastructure component, separate operational burden, learning curve                                               |
| **Milvus**                | Open-source vector database       | High performance, GPU support, mature ecosystem                                                                                        | Heavy footprint (etcd, MinIO dependencies), complex to operate, overkill for our scale                                         |
| **ChromaDB**              | Lightweight embedded vector store | Simple API, easy to embed in Python process                                                                                            | Not suitable for multi-service access, no production clustering, limited indexing options                                      |
| **PostgreSQL + pgvector** | Vector extension for PostgreSQL   | Single database for vectors and relational data, battle-tested PostgreSQL operations, HNSW index support, no additional infrastructure | Less optimized than purpose-built vector DBs at very large scale (millions of vectors), newer extension with smaller community |

### Decision

**PostgreSQL with the pgvector extension.**

### Rationale

At our target scale (up to 500,000 vectors), pgvector's HNSW index provides sub-200ms retrieval performance without the operational overhead of a separate vector database. Using PostgreSQL means the chunk metadata, ingestion log, and embeddings all live in one database, which simplifies transactions (a chunk insert is atomic with its embedding), backup, and disaster recovery. The team (and most hiring managers reviewing this project) already knows PostgreSQL, which reduces the cognitive overhead of evaluating the architecture.

Purpose-built vector databases such as Pinecone, Weaviate, or Milvus become advantageous at scales beyond our requirements (millions of vectors, multi-tenant workloads, or GPU-accelerated retrieval). If FinDoc RAG needed to scale beyond 500,000 vectors, the retriever module's interface is narrow enough that swapping pgvector for a dedicated store would be a contained change.

### Consequences

- Positive: Single database to operate, monitor, and back up.
- Positive: Standard SQL for metadata queries; no need to learn a vector-DB-specific query language.
- Positive: Atomic writes -- chunk text, metadata, and embedding are inserted in one transaction.
- Negative: HNSW index build time increases with corpus size; a full reindex at 500K vectors takes several minutes.
- Negative: No built-in sharding. Scaling beyond a single node would require manual partitioning or a move to a dedicated vector database.

---

## ADR-002: Embedding Model

**Status:** Accepted

### Context

The system needs an embedding model to convert both document chunks and user queries into dense vectors for similarity search. The model must run on CPU (no GPU requirement), produce consistent embeddings across the ingestion and query paths, and be small enough to load in each service replica without excessive memory consumption.

### Options Considered

| Option                              | Description                      | Dimensions | Model Size | Pros                                                                                   | Cons                                                                                           |
| ----------------------------------- | -------------------------------- | ---------- | ---------- | -------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------- |
| **all-MiniLM-L6-v2**                | Lightweight sentence-transformer | 384        | ~80 MB     | Fast inference on CPU, well-benchmarked (MTEB), small memory footprint, widely adopted | Lower retrieval quality than larger models on nuanced financial language                       |
| **all-mpnet-base-v2**               | Larger sentence-transformer      | 768        | ~420 MB    | Higher quality embeddings, better semantic capture                                     | 5x larger, 2-3x slower inference, doubles vector storage requirements                          |
| **text-embedding-3-small** (OpenAI) | OpenAI hosted embedding API      | 1536       | N/A (API)  | High quality, no local compute needed                                                  | Per-token cost, external dependency, latency from network round-trip, data sent to third party |
| **BGE-base-en-v1.5**                | BAAI general embedding model     | 768        | ~440 MB    | Strong retrieval benchmarks, open-source                                               | Similar size/speed trade-offs as mpnet, less community adoption in production RAG examples     |
| **E5-small-v2**                     | Microsoft embedding model        | 384        | ~130 MB    | Competitive quality at small size                                                      | Requires query prefix formatting ("query: ..."), slightly larger than MiniLM                   |

### Decision

**sentence-transformers/all-MiniLM-L6-v2.**

### Rationale

The primary constraint is CPU-only inference within containers that share memory with other services. At 80 MB and 384 dimensions, MiniLM-L6-v2 loads in under 5 seconds, embeds a query in under 20ms, and processes batch embeddings at over 50 chunks/second on a single CPU core. The 384-dimensional vectors also keep pgvector storage and index size manageable (384 floats = 1.5 KB per vector; 500K vectors = ~750 MB of vector data).

Larger models (mpnet, BGE) would improve retrieval quality marginally but double the storage requirements and slow down both ingestion and query-time embedding. For a financial QA system where the chunking strategy already provides section-level precision (Item 1A maps to Risk Factors), the marginal quality gain does not justify the resource cost.

OpenAI's embedding API was rejected because it introduces a hard external dependency on the embedding path -- if the API is down, neither ingestion nor querying works. It also means every query incurs network latency and per-token cost.

### Consequences

- Positive: Low memory footprint allows the model to be loaded in every service replica without resource contention.
- Positive: 384 dimensions keep vector storage and HNSW index compact.
- Positive: No external API dependency on the embedding path.
- Negative: Lower semantic resolution than 768- or 1536-dimensional models, particularly for subtle financial terminology.
- Negative: If retrieval quality proves insufficient in evaluation, switching to a larger model requires re-embedding the entire corpus and rebuilding the HNSW index.

---

## ADR-003: LLM Backend Strategy

**Status:** Accepted

### Context

The Query API needs an LLM to generate cited answers from retrieved context. The system must work fully offline (no external API requirement) for local development and demonstration, but should also support a higher-quality cloud LLM for evaluation and production use.

### Options Considered

| Option                                      | Description                                  | Pros                                                             | Cons                                                                         |
| ------------------------------------------- | -------------------------------------------- | ---------------------------------------------------------------- | ---------------------------------------------------------------------------- |
| **Ollama only**                             | Local LLM server, single backend             | Fully offline, no cost, simple                                   | Lower quality than cloud models, high memory usage, slow on CPU              |
| **OpenAI only**                             | Cloud LLM API, single backend                | High quality, fast, scalable                                     | Requires API key, cost per token, external dependency, not self-contained    |
| **Triple backend (Ollama + OpenAI + Claude)** | Configurable via environment variable      | Offline default + two cloud options for quality and flexibility  | Three code paths to maintain, need an abstraction layer                      |
| **LangChain abstraction**                   | Use LangChain's LLM abstraction layer        | Supports many backends, community ecosystem                      | Heavy dependency, abstractions can obscure behavior, version churn           |
| **vLLM / TGI**                              | Self-hosted inference servers                | Better throughput than Ollama, production-grade                  | Complex setup, GPU-oriented, overkill for single-user demo                   |

### Decision

**Triple backend with a custom Protocol abstraction: Ollama as default, OpenAI and Claude (Anthropic) as remote alternatives.** Backend is selected via the `LLM_BACKEND` environment variable (`ollama`, `openai`, or `claude`).

### Rationale

The triple-backend approach satisfies multiple deployment scenarios. For local development and portfolio demonstration, the system must start with `docker compose --profile local-llm up` and work without any API key -- Ollama provides this. For evaluation and quality comparison, both OpenAI's GPT-4o-mini and Anthropic's Claude produce measurably better answers than a locally-run 7B model. Claude is particularly useful for developers who have an Anthropic subscription but not an OpenAI key, and vice versa.

Ollama is made opt-in via Docker Compose profiles (`--profile local-llm`). Running without the profile skips the Ollama container entirely, reducing the stack's RAM footprint by ~6 GB and avoiding a multi-GB model download -- this makes remote-backend workflows significantly faster to start.

A LangChain-based abstraction was rejected because it introduces a large transitive dependency tree for what is ultimately a single `generate(prompt) -> text` call. The custom `LLMBackend` Protocol is 10 lines of code and does exactly what we need without framework coupling.

vLLM and TGI are production inference servers designed for GPU-backed, high-throughput serving. They are not suitable for a CPU-only Docker Compose demo environment.

### Consequences

- Positive: System works fully offline out of the box (Ollama profile) and with remote APIs when Ollama is impractical.
- Positive: Evaluation harness can compare Ollama, OpenAI, and Claude quality side by side.
- Positive: Minimal abstraction layer (Protocol + three implementations) is easy to understand and test.
- Positive: Ollama is opt-in via Docker Compose profiles; remote-backend setups skip the ~6 GB model download.
- Negative: Ollama with Mistral 7B requires approximately 6 GB of RAM, which is significant in memory-constrained environments (TinyLlama at 637 MB is a documented fallback).
- Negative: Three code paths means all three backends need test coverage.

---

## ADR-004: Message Queue

**Status:** Accepted

### Context

The ingestion pipeline needs to decouple the document fetching step (calling SEC EDGAR) from the embedding step (chunking, vectorizing, and storing). This decoupling provides backpressure, durability, and the ability to scale consumers independently of producers.

### Options Considered

| Option                       | Description                                       | Pros                                                                                                           | Cons                                                                                                        |
| ---------------------------- | ------------------------------------------------- | -------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------- |
| **Apache Kafka**             | Distributed event streaming platform              | Durable, replayable, consumer groups for parallel processing, industry standard for event-driven architectures | Operationally heavy, high memory footprint (~400 MB), overkill for single-node demo                         |
| **RabbitMQ**                 | Traditional message broker                        | Lightweight, simple to operate, built-in retry/DLQ support, lower memory usage                                 | No message replay, messages deleted after consumption, less common in event-driven architecture discussions |
| **Redis Streams**            | Redis-based streaming                             | Very lightweight, already a common infra component, low latency                                                | No built-in consumer group rebalancing, persistence depends on Redis configuration, less durable than Kafka |
| **Direct HTTP call**         | Ingestion service calls embedding worker directly | No infrastructure overhead, simplest possible design                                                           | Tight coupling, no backpressure, no retry/replay, ingestion blocked by embedding speed                      |
| **PostgreSQL LISTEN/NOTIFY** | Database-native pub/sub                           | No additional infrastructure                                                                                   | Not durable (notifications lost if no listener), no consumer groups, not designed for large payloads        |

### Decision

**Apache Kafka (KRaft mode, no ZooKeeper).**

### Rationale

Kafka was chosen primarily because the project goals (G-2) explicitly call for an event-driven architecture. Beyond the requirement, Kafka provides three properties that are operationally valuable:

1. **Replayability**: If the embedding worker has a bug, we can fix it and replay all messages from a specific offset without re-fetching from SEC EDGAR.
2. **Consumer groups**: Adding more embedding worker replicas automatically distributes partitions across them -- no application-level coordination needed.
3. **Dead letter queue pattern**: Failed messages can be routed to a DLQ topic for later inspection without blocking the main consumer.

RabbitMQ would have been a reasonable alternative with a lighter footprint, but it lacks replay capability (messages are removed after acknowledgment), which is critical during development when the embedding logic is iterated on frequently.

KRaft mode (Kafka without ZooKeeper) reduces the operational footprint from two services to one.

### Consequences

- Positive: Durable, replayable message log enables safe iteration on the embedding pipeline.
- Positive: Consumer group semantics provide a clear horizontal scaling path.
- Positive: DLQ topic provides a structured error recovery mechanism.
- Negative: Kafka consumes approximately 400 MB of memory, which is significant in a constrained local environment.
- Negative: Large filing texts (10-K filings can exceed 150 MB of raw text; the largest observed filing was ~157 MB) require increasing `message.max.bytes` significantly beyond Kafka's 1 MB default.
- Negative: Single-broker deployment has no replication; not suitable for production durability guarantees.

---

## ADR-005: Chunking Strategy

**Status:** Accepted

### Context

10-K filings are long-form documents (50-200 pages, 50,000-200,000 tokens). They must be split into chunks small enough to fit in the embedding model's context window and provide focused retrieval results, while preserving enough context for the LLM to generate coherent answers.

### Options Considered

| Option                              | Description                                                                  | Pros                                                                                               | Cons                                                                                |
| ----------------------------------- | ---------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------- |
| **Fixed-size token windows**        | Split text into fixed 512-token chunks with overlap                          | Simple, predictable chunk sizes, no parsing logic                                                  | Splits mid-sentence and mid-section, loses structural context                       |
| **Recursive character splitting**   | LangChain-style recursive split by separators                                | Respects paragraph boundaries, widely used                                                         | No awareness of 10-K document structure, section metadata is lost                   |
| **Section-aware + token windowing** | First split by 10-K item sections, then by paragraphs, then by token windows | Preserves document structure, section metadata enables filtering, chunks are contextually coherent | Requires 10-K section parsing logic, parsing can fail on non-standard formats       |
| **Semantic chunking**               | Use embeddings to detect topic boundaries                                    | Theoretically optimal chunk boundaries                                                             | Computationally expensive, non-deterministic, adds complexity for uncertain benefit |

### Decision

**Section-aware hierarchical chunking: 10-K items, then paragraphs, then 512-token windows with 64-token overlap.**

### Rationale

SEC 10-K filings have a well-defined structure mandated by regulation (Item 1 through Item 15). Splitting first by these sections preserves this structure as metadata, which enables two features:

1. **Section-name metadata**: Each chunk carries its section name (e.g., "Item 1A - Risk Factors"), which is included in the LLM prompt context. This helps the LLM attribute information correctly.
2. **Ticker + section filtering**: The Query API can optionally filter retrieval by ticker and section, reducing noise in the results.

The 512-token target with 64-token overlap (12.5%) ensures that no chunk exceeds the embedding model's effective context window while maintaining continuity at chunk boundaries. Token counting uses `tiktoken` with the `cl100k_base` encoding for consistency with OpenAI models.

Fixed-size windows were rejected because they frequently split mid-section (e.g., half of a risk factor in one chunk, half in the next), producing chunks that are less coherent and harder for the LLM to cite accurately.

Semantic chunking was considered but rejected: it requires embedding every sentence to detect topic boundaries (effectively doubling embedding compute), produces non-deterministic splits, and adds significant complexity for a benefit that is difficult to measure without extensive A/B testing.

### Consequences

- Positive: Chunks carry structural metadata that improves both retrieval precision and LLM prompt grounding.
- Positive: Deterministic chunking -- the same filing always produces the same chunks (via SHA256 chunk IDs).
- Positive: 512-token chunks with overlap balance retrieval granularity and context completeness.
- Negative: Section parsing uses regex patterns for 10-K item headers; filings with non-standard formatting may produce a single large "unknown section" chunk.
- Negative: The overlap means approximately 12.5% more chunks (and embeddings) than a non-overlapping strategy.

---

## ADR-006: API Framework

**Status:** Accepted

### Context

All three services need HTTP endpoints (at minimum for health checks). The Query API additionally needs request validation, OpenAPI documentation, async support (for non-blocking LLM calls), and dependency injection (for auth).

### Options Considered

| Option                    | Description                              | Pros                                                                                                              | Cons                                                                                                                        |
| ------------------------- | ---------------------------------------- | ----------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------- |
| **FastAPI**               | Modern async Python web framework        | Auto-generated OpenAPI docs, Pydantic validation, async-native, dependency injection, dominant in ML/AI ecosystem | Relatively new (though mature at this point), Pydantic v2 migration can cause library conflicts                             |
| **Flask**                 | Traditional synchronous Python framework | Simple, mature, large ecosystem                                                                                   | No native async, no built-in validation or OpenAPI generation, requires extensions for features FastAPI includes by default |
| **Django REST Framework** | Full-featured Python web framework       | Mature ORM, admin panel, authentication system                                                                    | Very heavy for microservices, ORM is unnecessary (we use raw SQL with pgvector), significant overhead                       |
| **Starlette**             | ASGI framework (FastAPI is built on it)  | Lightweight, async-native                                                                                         | No built-in validation, no auto-generated docs -- FastAPI adds exactly these features on top of Starlette                   |

### Decision

**FastAPI for all three services.**

### Rationale

FastAPI provides the exact feature set the Query API requires (async endpoints, Pydantic request/response models, automatic OpenAPI/Swagger docs, dependency injection for auth) with no unnecessary weight. Using it for the Ingestion Service and Embedding Worker as well (even though they have simpler needs) provides consistency across the codebase -- one framework to learn, one set of patterns, one test style.

The automatic OpenAPI documentation is particularly valuable for a portfolio project: it provides an interactive API explorer without any additional code.

Flask was the main alternative. It would require `flask-restx` or `flask-apispec` for OpenAPI docs, `marshmallow` or manual validation for request schemas, and `asyncio` wrappers for non-blocking LLM calls. FastAPI includes all of this natively.

### Consequences

- Positive: Automatic Swagger UI at `/docs` for all services.
- Positive: Pydantic models enforce type contracts at the API boundary and generate JSON Schema automatically.
- Positive: Async support allows the Query API to handle concurrent requests while waiting for the LLM.
- Negative: FastAPI's dependency injection can be unintuitive for developers coming from Flask/Django.
- Negative: Some third-party libraries (e.g., `slowapi` for rate limiting) have edge cases with FastAPI's body parsing when combined with middleware.

---

## ADR-007: Deployment Packaging

**Status:** Accepted

### Context

The system must be runnable locally with a single command (NFR-10) and deployable to a Kubernetes cluster for production-style demonstration. The deployment configuration needs to be versioned, templated, and reproducible.

### Options Considered

| Option                    | Description                                | Pros                                                                             | Cons                                                                                                              |
| ------------------------- | ------------------------------------------ | -------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------- |
| **Docker Compose only**   | Local orchestration, no Kubernetes         | Simple, fast iteration, low barrier to entry                                     | Not representative of production deployment, no autoscaling, no rolling updates                                   |
| **Helm chart only**       | Kubernetes-native packaging                | Production-grade, templated, versioned, supports autoscaling and rolling updates | Requires a Kubernetes cluster even for local development, slower iteration cycle                                  |
| **Docker Compose + Helm** | Compose for local, Helm for Kubernetes     | Best of both: fast local development and production-grade deployment             | Two configurations to maintain, potential for drift between local and K8s behavior                                |
| **Kustomize**             | Kubernetes-native configuration management | No templating engine, plain YAML overlays, built into kubectl                    | Less flexible than Helm for complex parametrization, no built-in chart versioning or dependency management        |
| **Terraform**             | Infrastructure-as-code                     | Can manage both infrastructure and application deployment                        | Too broad in scope, not specialized for Kubernetes application packaging, steep learning curve for app deployment |

### Decision

**Docker Compose for local development, Helm for Kubernetes deployment.**

### Rationale

The dual approach matches two distinct user journeys. A reviewer cloning the repo wants `docker compose up` to work immediately -- Helm requires a Kubernetes cluster, which is a significant setup barrier. A production deployment needs templated configuration, rolling updates, HPA, health probes, and resource limits -- Docker Compose does not support any of these.

Maintaining both configurations is a trade-off. The risk of drift is mitigated by using the same Docker images, environment variables, and health check endpoints in both configurations. The Helm `values.yaml` defaults mirror the Docker Compose `.env` defaults.

Kustomize was considered as a lighter alternative to Helm but lacks chart-level versioning and dependency management (e.g., pulling a Kafka chart as a subchart). For a project that bundles its own Kafka and PostgreSQL, Helm's subchart model is a better fit.

### Consequences

- Positive: `docker compose up` works immediately for any reviewer with Docker installed.
- Positive: Helm chart demonstrates production Kubernetes knowledge (HPA, StatefulSets, Ingress, Secrets, CronJobs).
- Positive: Same Docker images are used in both environments.
- Negative: Two deployment configurations to maintain.
- Negative: Environment variable defaults must be kept in sync between `.env` / `docker-compose.yml` and `values.yaml`.

---

## ADR-008: Observability Stack

**Status:** Accepted

### Context

The system needs metrics collection, dashboarding, and structured logging to demonstrate production-grade observability. The observability stack must run within the same Docker Compose and Kubernetes deployments without external dependencies.

### Options Considered

| Option                                            | Description                           | Pros                                                                                                 | Cons                                                                                          |
| ------------------------------------------------- | ------------------------------------- | ---------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------- |
| **Prometheus + Grafana**                          | Pull-based metrics with dashboarding  | Industry standard, native Kubernetes integration, large ecosystem of exporters, free and open-source | Pull-based model requires service discovery, Prometheus is not designed for long-term storage |
| **Datadog**                                       | Commercial observability platform     | All-in-one (metrics, logs, traces), excellent dashboards, easy setup                                 | Commercial/costly, external dependency, not self-contained for a demo project                 |
| **OpenTelemetry + Jaeger**                        | Distributed tracing focused           | Vendor-neutral, traces provide request-level visibility                                              | Primarily tracing, not metrics; would still need Prometheus for metrics                       |
| **ELK Stack (Elasticsearch + Logstash + Kibana)** | Log-centric observability             | Powerful log search and analysis                                                                     | Very heavy footprint (Elasticsearch alone needs 2+ GB RAM), overkill for structured JSON logs |
| **Structured logging only**                       | JSON logs to stdout, no metrics infra | Zero infrastructure overhead, logs are queryable with `jq` or any log aggregator                     | No time-series metrics, no dashboards, no alerting                                            |

### Decision

**Prometheus for metrics collection, Grafana for dashboards, structlog for JSON logging.**

### Rationale

Prometheus + Grafana is the de facto standard for Kubernetes-native observability. Every service exposes a `/metrics` endpoint in Prometheus exposition format; Prometheus scrapes them at a configured interval; Grafana queries Prometheus and renders dashboards. This pull-based model requires no client-side push configuration and works identically in Docker Compose (Prometheus scrapes container hostnames) and Kubernetes (Prometheus uses service discovery).

Structured logging with `structlog` outputs machine-parseable JSON to stdout, which is the standard for containerized applications. In Kubernetes, these logs are automatically collected by the node's logging agent and can be forwarded to any log aggregator. For local development, the JSON logs are human-readable with `docker logs` and `jq`.

Datadog and the ELK stack were rejected for being either commercial or too heavy for a self-contained demo. OpenTelemetry tracing would be a valuable addition but was deprioritized -- the system's request flow is short (embed, retrieve, generate) and the timing breakdown in the API response provides equivalent visibility for debugging.

### Consequences

- Positive: Industry-standard stack that runs self-contained in both Docker Compose and Kubernetes.
- Positive: Pre-built Grafana dashboards ship with the project and load automatically.
- Positive: JSON structured logs are compatible with any future log aggregation system.
- Negative: Prometheus is pull-based, so short-lived batch jobs (e.g., the evaluation harness) need a Pushgateway or alternative mechanism to expose metrics.
- Negative: No distributed tracing. Request correlation relies on the `request_id` field in structured logs.

---

## ADR-009: Evaluation Framework

**Status:** Accepted

### Context

The project needs quantified evaluation of RAG quality to demonstrate that the pipeline produces useful, grounded answers (NFR-7, NFR-8). The evaluation must be automated, reproducible, and produce human-readable reports.

### Options Considered

| Option                    | Description                                   | Pros                                                                                                         | Cons                                                                                                            |
| ------------------------- | --------------------------------------------- | ------------------------------------------------------------------------------------------------------------ | --------------------------------------------------------------------------------------------------------------- |
| **ragas**                 | Open-source RAG evaluation library            | Purpose-built for RAG (context precision, faithfulness, answer relevancy), active development, Python-native | Requires an LLM for faithfulness scoring (uses the LLM to judge the LLM), relatively new library                |
| **LangSmith**             | LangChain's evaluation platform               | Integrated with LangChain, hosted tracing and evaluation                                                     | Commercial, external dependency, tightly coupled to LangChain ecosystem                                         |
| **Custom metrics script** | Hand-written precision/recall/F1 calculations | Full control, no external dependencies                                                                       | Significant development effort, reinventing well-established metrics, less credible than a recognized framework |
| **ARES**                  | Automated RAG evaluation framework            | Strong academic grounding, detailed metrics                                                                  | Less mature library, smaller community, more complex setup                                                      |
| **Human evaluation only** | Manual review of answers                      | Gold standard for quality assessment                                                                         | Not reproducible, not automatable, does not scale, subjective                                                   |

### Decision

**ragas library for automated evaluation, with results persisted as JSON and summarized in Markdown.**

### Rationale

ragas provides exactly the three metrics that matter for a RAG system:

- **Context Precision**: Are we retrieving the right chunks? (Evaluates the retriever)
- **Faithfulness**: Is the answer grounded in the retrieved context? (Evaluates hallucination)
- **Answer Relevancy**: Does the answer actually address the question? (Evaluates end-to-end quality)

These metrics are computed automatically against a curated dataset, making the evaluation reproducible and runnable in CI. The library is Python-native and integrates with both Ollama and OpenAI, so we can evaluate both LLM backends.

A custom metrics script was rejected because it would require implementing the same metric definitions that ragas already provides, with less credibility and more maintenance burden. LangSmith was rejected because it introduces a commercial dependency and couples the project to the LangChain ecosystem.

### Consequences

- Positive: Automated, reproducible evaluation that can run in CI.
- Positive: Industry-recognized metrics that hiring managers and reviewers understand.
- Positive: Evaluation can compare Ollama vs. OpenAI vs. Claude quality quantitatively.
- Positive: The eval harness selects its judge LLM automatically: Anthropic API key → OpenAI API key → Ollama local fallback, so it works with any available credential.
- Negative: Faithfulness scoring uses an LLM to judge LLM output, introducing circularity (mitigated by using a different model for evaluation than for generation).
- Negative: ragas is a relatively young library; API changes between versions may require updates.

---

## ADR-010: Kafka Topology and Consumer Design

**Status:** Accepted

### Context

The embedding worker consumes filing messages from Kafka and must handle failures gracefully without losing messages or blocking the pipeline. The consumer must support horizontal scaling (multiple worker replicas) and provide visibility into processing failures.

### Options Considered

| Option                                | Description                                           | Pros                                                                | Cons                                                                                         |
| ------------------------------------- | ----------------------------------------------------- | ------------------------------------------------------------------- | -------------------------------------------------------------------------------------------- |
| **Auto-commit, no DLQ**               | Kafka auto-commits offsets after poll                 | Simplest consumer implementation                                    | Lost messages on failure (offset committed before processing completes), no error visibility |
| **Manual commit, no DLQ**             | Commit offset only after successful processing        | No message loss                                                     | A single poison message blocks the consumer indefinitely (infinite retry loop)               |
| **Manual commit + DLQ topic**         | Commit after processing or after DLQ publish          | No message loss, poison messages are isolated, processing continues | Requires DLQ topic management and monitoring, slightly more complex consumer logic           |
| **Manual commit + DLQ + retry topic** | Separate retry topic with delay before re-consumption | Transient failures get automatic retry with backoff                 | Significantly more complex topology, harder to reason about message flow                     |

### Decision

**Manual offset commit with a dead letter queue (DLQ) topic (`filings.dlq`).** Failed messages are retried 3 times in-process with exponential backoff. After 3 failures, the message is published to the DLQ and the offset is committed.

### Rationale

The manual-commit + DLQ pattern balances reliability with simplicity. Offsets are committed only after the chunk has been successfully stored in pgvector or published to the DLQ, ensuring no message is silently lost. The DLQ provides a clear audit trail of failures and enables manual replay after fixes.

A separate retry topic (with delayed re-consumption) was considered but adds significant complexity: it requires tracking retry counts, managing delay semantics (Kafka does not natively support delayed delivery), and reasoning about message ordering across topics. In-process retry with exponential backoff (up to 3 attempts) handles transient failures (database connection blips, temporary network issues) without this complexity.

Auto-commit was rejected because it creates a window where a message is marked as consumed before processing completes. If the worker crashes in that window, the message is lost.

### Consequences

- Positive: No message loss -- every message is either successfully processed or routed to the DLQ.
- Positive: Poison messages do not block the consumer pipeline.
- Positive: DLQ messages retain the original payload and error details for debugging.
- Positive: Consumer group rebalancing works correctly because offsets are committed at well-defined points.
- Negative: DLQ messages require manual inspection and replay (no automated retry from DLQ).
- Negative: 3 in-process retries add latency for persistently failing messages (up to ~14 seconds with exponential backoff before DLQ routing).

---

## ADR-011: Retrieval Reranking Strategy (MMR)

**Status:** Accepted

### Context

The vector similarity search in pgvector returns the top-k chunks ranked by cosine distance. When a filing section is split into many closely overlapping chunks (e.g., a long Risk Factors section), the top-k results can be dominated by near-duplicate chunks from the same passage. The LLM then receives repetitive context, wasting its context window and producing answers that over-cite a single passage while ignoring equally relevant content elsewhere.

### Options Considered

| Option                            | Description                                                     | Pros                                                                                                     | Cons                                                                                                     |
| --------------------------------- | --------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------- |
| **No reranking (pure similarity)**| Return top-k by cosine distance directly                        | Simplest implementation, lowest latency                                                                  | Near-duplicate chunks dominate when sections are densely tokenised; LLM sees repetitive context          |
| **Cosine-distance deduplication** | Remove chunks whose cosine similarity to any already-selected chunk exceeds a threshold | Simple to implement, easy to reason about | Threshold is a magic number; binary accept/reject loses relevance signal; threshold requires manual tuning |
| **Maximal Marginal Relevance (MMR)** | Iteratively select chunks maximising λ × relevance − (1−λ) × max_similarity_to_selected | Principled trade-off, industry standard (LangChain, LlamaIndex), single λ parameter, no hard threshold | Slightly more code; requires chunk embeddings to be returned from the DB query                           |

### Decision

**Maximal Marginal Relevance (MMR) with λ=0.5 (equal weight on relevance and diversity).**

Fetch `min(top_k × 4, 100)` candidates from pgvector, then apply MMR to select the final `top_k`.

### Rationale

MMR (Carbonell & Goldstein, 1998, SIGIR) is the de facto standard for diversity-aware retrieval reranking. The iterative selection formula:

```
score = λ × relevance_score − (1 − λ) × max_cosine_sim(chunk, already_selected)
```

subsumes cosine deduplication as a special case (λ→0) while also encoding the original relevance signal. With λ=0.5 the algorithm naturally trades off — a chunk with slightly lower relevance but high novelty beats a near-duplicate with higher relevance. This is exactly the right behaviour when the top-20 candidates from pgvector all come from the same filing section.

The 4× candidate multiplier gives MMR enough diversity headroom without meaningful latency cost, since pgvector's HNSW lookup is O(log n) regardless of LIMIT.

Cosine-distance deduplication was rejected because its threshold is a brittle magic number: too low and duplicates slip through; too high and legitimate near-paraphrases are discarded. MMR avoids the need for a hard threshold entirely.

Returning chunk embeddings from the DB query is necessary for MMR but was already the correct design — pgvector's `register_vector` makes this zero-copy via numpy arrays.

### Consequences

- Positive: LLM receives diverse, non-redundant context — particularly valuable for long filings with many overlapping chunks.
- Positive: Single λ parameter with a sensible default (0.5); callers can override to λ=1.0 to recover pure relevance order.
- Positive: Algorithm is well-understood and directly testable (unit tests verify diversity preference and λ=1.0 fallback behaviour).
- Negative: Requires fetching `4 × top_k` candidates from pgvector instead of `top_k`; HNSW lookup cost is the same but row transfer is larger.
- Negative: Adds a dependency on `numpy` for the dot-product computation (already a transitive dependency via `sentence-transformers`, so no new package).
