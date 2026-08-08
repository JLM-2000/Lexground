.DEFAULT_GOAL := help
BACKEND := backend
PY := $(BACKEND)/.venv/bin
DATA := data

FIXTURE_MANIFEST := $(DATA)/fixtures/corpus.json
FIXTURE_CORPUS   := $(DATA)/fixtures/corpus
FIXTURE_GOLDEN   := $(DATA)/fixtures/golden.jsonl

.PHONY: help
help: ## Show this help
	@grep -hE '^[a-z-]+:.*##' $(MAKEFILE_LIST) | sort | \
	  awk 'BEGIN {FS = ":.*## "}; {printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

.PHONY: install
install: ## Create the venv and install backend + frontend dependencies
	cd $(BACKEND) && uv venv --python 3.12 .venv && uv pip install -e ".[dev,embeddings]"
	cd frontend && npm install

.PHONY: start
start: ## Build and run the whole stack in Docker, index included (API :8000, UI :3000)
	docker compose up -d --build

.PHONY: logs
logs: ## Follow the stack's logs
	docker compose logs -f

.PHONY: up
up: ## Start Postgres only, for local development against the venv
	docker compose up -d db

.PHONY: down
down: ## Stop the stack and drop its volumes
	docker compose down -v

.PHONY: eval-docker
eval-docker: ## Run the gate inside the stack, recording the run for the dashboard
	docker compose run --rm --no-deps \
	  -e LEXGROUND_DATABASE_URL=postgresql+asyncpg://lexground:lexground@db:5432/lexground \
	  seed sh -c "lexground evaluate \
	    --golden $(FIXTURE_GOLDEN) \
	    --manifest $(FIXTURE_MANIFEST) \
	    --thresholds $(DATA)/thresholds.offline.json"

.PHONY: seed
seed: up ## Create the schema and index the fixture corpus
	$(PY)/lexground init-db
	$(PY)/lexground ingest --manifest $(FIXTURE_MANIFEST) --cache-dir $(FIXTURE_CORPUS) --offline

.PHONY: seed-live
seed-live: up ## Index the real EUR-Lex corpus (needs a seeded cache; see docs/corpus.md)
	$(PY)/lexground init-db
	$(PY)/lexground ingest --manifest $(DATA)/corpus.json --cache-dir $(DATA)/corpus

.PHONY: dev
dev: ## Run the API with reload
	cd $(BACKEND) && .venv/bin/uvicorn lexground.main:app --reload --port 8000

.PHONY: dev-ui
dev-ui: ## Run the frontend against a local API
	cd frontend && npm run dev

.PHONY: test
test: ## Run the full backend test suite
	cd $(BACKEND) && .venv/bin/pytest -q

.PHONY: lint
lint: ## Lint and format-check both halves
	cd $(BACKEND) && .venv/bin/ruff check src tests && .venv/bin/ruff format --check src tests
	cd frontend && npm run typecheck

.PHONY: eval
eval: ## Score the fixture golden set and apply the offline gate
	$(PY)/lexground evaluate \
	  --golden $(FIXTURE_GOLDEN) \
	  --manifest $(FIXTURE_MANIFEST) \
	  --thresholds $(DATA)/thresholds.offline.json \
	  --report reports/eval.json

.PHONY: eval-judge
eval-judge: ## Score with live synthesis and the groundedness judge (needs a provider key)
	$(PY)/lexground evaluate \
	  --golden $(FIXTURE_GOLDEN) \
	  --manifest $(FIXTURE_MANIFEST) \
	  --thresholds $(DATA)/thresholds.json \
	  --report reports/eval.json \
	  --judge

.PHONY: eval-live
eval-live: ## Score the real EUR-Lex golden set
	$(PY)/lexground evaluate \
	  --golden $(DATA)/golden/cases.jsonl \
	  --manifest $(DATA)/corpus.json \
	  --thresholds $(DATA)/thresholds.json \
	  --report reports/eval-live.json \
	  --judge

.PHONY: check
check: lint test eval ## Everything CI runs
