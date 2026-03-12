# ============================================================
# FinDocRAG — Makefile
# See: docs/technical-design-document.md Section 10.5
# ============================================================

.PHONY: setup test lint run eval docker-build helm-deploy helm-teardown migrate stop clean

# ── Local Development ────────────────────────────────────────

setup:  ## Create virtual environments and install dependencies
	@echo "==> Setting up ingestion service..."
	cd services/ingestion && python3.12 -m venv .venv && .venv/bin/pip install -r requirements.txt
	@echo "==> Setting up embedding worker..."
	cd services/embedding-worker && python3.12 -m venv .venv && .venv/bin/pip install -r requirements.txt
	@echo "==> Setting up query API..."
	cd services/query-api && python3.12 -m venv .venv && .venv/bin/pip install -r requirements.txt
	@echo "==> Installing test dependencies..."
	cd services/ingestion && .venv/bin/pip install pytest pytest-asyncio pytest-cov
	cd services/embedding-worker && .venv/bin/pip install pytest pytest-asyncio pytest-cov numpy
	cd services/query-api && .venv/bin/pip install pytest pytest-asyncio pytest-cov numpy
	@echo "==> Setup complete."

test:  ## Run pytest for all services
	@echo "==> Testing ingestion service..."
	cd services/ingestion && PYTHONPATH=. .venv/bin/python -m pytest tests/ -v --cov=src --cov-report=term-missing
	@echo "==> Testing embedding worker..."
	cd services/embedding-worker && PYTHONPATH=. .venv/bin/python -m pytest tests/ -v --cov=src --cov-report=term-missing
	@echo "==> Testing query API..."
	cd services/query-api && PYTHONPATH=. .venv/bin/python -m pytest tests/ -v --cov=src --cov-report=term-missing

lint:  ## Run ruff + mypy for all services
	@echo "==> Linting with ruff..."
	ruff check services/
	@echo "==> Type checking with mypy..."
	cd services/ingestion && PYTHONPATH=. .venv/bin/python -m mypy src/
	cd services/embedding-worker && PYTHONPATH=. .venv/bin/python -m mypy src/
	cd services/query-api && PYTHONPATH=. .venv/bin/python -m mypy src/

run:  ## Start the full stack with Docker Compose
	docker compose up --build

stop:  ## Stop Docker Compose stack
	docker compose down

clean:  ## Stop stack and remove volumes (destroys all data)
	docker compose down -v

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

# ── Help ─────────────────────────────────────────────────────

help:  ## Show this help message
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}'

.DEFAULT_GOAL := help