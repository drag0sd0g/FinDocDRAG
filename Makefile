# ============================================================
# FinDocDRAG — Makefile
# See: docs/technical-design-document.md Section 10.5
# ============================================================

.PHONY: setup test lint run eval docker-build helm-deploy helm-teardown helm-test migrate stop clean

# ── Local Development ────────────────────────────────────────

setup:  ## Create virtual environments and install dependencies
	@echo "==> Setting up ingestion service..."
	cd services/ingestion && python3.12 -m venv .venv && .venv/bin/pip install -r requirements.txt -r requirements-dev.txt
	@echo "==> Setting up embedding worker..."
	cd services/embedding-worker && python3.12 -m venv .venv && .venv/bin/pip install -r requirements.txt -r requirements-dev.txt
	@echo "==> Setting up query API..."
	cd services/query-api && python3.12 -m venv .venv && .venv/bin/pip install -r requirements.txt -r requirements-dev.txt
	@echo "==> Setup complete."

test:  ## Run pytest for all services
	@echo "==> Testing ingestion service..."
	cd services/ingestion && PYTHONPATH=. .venv/bin/python -m pytest tests/ -v --cov=src --cov-report=term-missing --cov-fail-under=80
	@echo "==> Testing embedding worker..."
	cd services/embedding-worker && PYTHONPATH=. .venv/bin/python -m pytest tests/ -v --cov=src --cov-report=term-missing --cov-fail-under=80
	@echo "==> Testing query API..."
	cd services/query-api && PYTHONPATH=. .venv/bin/python -m pytest tests/ -v --cov=src --cov-report=term-missing --cov-fail-under=80

lint:  ## Run ruff + mypy for all services
	@echo "==> Linting with ruff..."
	ruff check services/
	@echo "==> Type checking with mypy..."
	cd services/ingestion && PYTHONPATH=. .venv/bin/python -m mypy src/ --ignore-missing-imports --no-error-summary
	cd services/embedding-worker && PYTHONPATH=. .venv/bin/python -m mypy src/ --ignore-missing-imports --no-error-summary
	cd services/query-api && PYTHONPATH=. .venv/bin/python -m mypy src/ --ignore-missing-imports --no-error-summary

run:  ## Start the full stack with Docker Compose (includes Ollama local LLM)
	docker compose --profile local-llm up --build

run-remote:  ## Start the stack without Ollama (use LLM_BACKEND=claude or openai)
	docker compose up --build

stop:  ## Stop Docker Compose stack
	docker compose --profile local-llm down

clean:  ## Stop stack and remove volumes (destroys all data)
	docker compose --profile local-llm down -v

migrate:  ## Run database migrations (requires running postgres)
	docker compose up db-migrate

# ── Docker ───────────────────────────────────────────────────

docker-build:  ## Build Docker images for all services
	docker compose build ingestion embedding-worker query-api

# ── Evaluation ───────────────────────────────────────────────

eval:  ## Run RAG evaluation harness (requires running stack)
	@echo "==> Running evaluation harness..."
	cd services/eval && python run_eval.py

# ── Kubernetes / Helm ────────────────────────────────────────

helm-deploy:  ## Deploy to Kubernetes via Helm
	helm upgrade --install findoc-rag helm/findoc-rag/ --values helm/findoc-rag/values.yaml

helm-teardown:  ## Remove Helm release
	helm uninstall findoc-rag

helm-test:  ## Run Helm unit tests (requires helm-unittest plugin)
	@echo "==> Running Helm unit tests..."
	helm unittest helm/findoc-rag/