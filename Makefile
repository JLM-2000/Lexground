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

.PHONY: up
up: ## Start Postgres in the background
	docker compose up -d db

.PHONY: down
down: ## Stop the stack and drop its volumes
	docker compose down -v

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
eval-judge: ## Score the fixture set with synthesis and the groundedness judge (needs ANTHROPIC_API_KEY)
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
