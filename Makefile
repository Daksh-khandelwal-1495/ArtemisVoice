# ArtemisVoice platform: VoiceFlow Studio (agent under test) + Artemis (causal debugger).
# Host ports: Studio API 8000 · Studio UI 3000 · Artemis API 8001 · dashboard 5173 · Postgres 5433
# Run `make help` for the target list.

SHELL := /bin/bash
.DEFAULT_GOAL := help

PYTHON   ?= $(shell command -v python3.12 2>/dev/null || command -v python3)
VENV     := .venv
VPY      := $(VENV)/bin/python
STUDIO_BE := voiceflow-studio/backend
STUDIO_FE := voiceflow-studio/frontend
COMPOSE  := docker compose -f compose.yaml

.PHONY: help setup submodule check-submodule venv studio-deps ui-deps dev studio-api \
        test test-artemis test-studio lint build-ui up up-hybrid down logs ps demo-seed demo-reset urls

help: ## Show this help
	@awk 'BEGIN {FS = ":.*## "} /^[a-zA-Z_-]+:.*## / {printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}' $(MAKEFILE_LIST)

# ---- setup -------------------------------------------------------------------
setup: submodule venv studio-deps ui-deps ## Init submodule, uv sync Studio, Artemis .venv, npm installs
	@echo
	@echo "Setup done. Provider keys: cp $(STUDIO_BE)/.env.example $(STUDIO_BE)/.env and fill it in (never commit it)."
	@echo "Next: make dev   (or: make up for the full Docker stack)"

submodule: ## Initialise the Pipecat submodule
	git submodule update --init voiceflow-studio/vendor/pipecat
	@./scripts/check-submodule.sh

check-submodule:
	@./scripts/check-submodule.sh

venv: ## Artemis venv (.venv) with dev extras
	@test -n "$(PYTHON)" || { echo "python3.12+ not found"; exit 1; }
	test -x $(VPY) || $(PYTHON) -m venv $(VENV)
	$(VPY) -m pip install --upgrade pip
	$(VPY) -m pip install -e ".[dev]"

studio-deps: check-submodule ## uv sync the Studio backend (incl. dev group)
	cd $(STUDIO_BE) && uv sync

ui-deps: ## npm install for both frontends
	cd $(STUDIO_FE) && npm install --no-audit --no-fund
	cd frontend && npm install --no-audit --no-fund

# ---- development -------------------------------------------------------------
dev: ## Postgres (docker) + all 4 dev servers; Ctrl-C stops them
	@./scripts/dev.sh

studio-api: check-submodule ## Only the Studio API on the host :8000 (for live mic calls with make up-hybrid)
	cd $(STUDIO_BE) && ARTEMIS_API_URL=http://localhost:8001 ARTEMIS_DASHBOARD_URL=http://localhost:5173 \
	  CORS_ORIGINS=$${CORS_ORIGINS:-http://localhost:3000,http://localhost:5173} \
	  uv run uvicorn app.main:app --host 127.0.0.1 --port 8000

# ---- tests -------------------------------------------------------------------
test: test-artemis test-studio ## Artemis ruff + pytest, Studio pytest

test-artemis: ## Artemis: ruff + pytest
	$(VPY) -m ruff check backend
	$(VPY) -m pytest -q

test-studio: check-submodule ## Studio backend pytest (sqlite fallback when Postgres :5433 is down)
	cd $(STUDIO_BE) && uv run pytest -q

lint: ## Ruff for Artemis
	$(VPY) -m ruff check backend

build-ui: ## Production builds of both frontends (same as CI)
	cd $(STUDIO_FE) && npm run build
	cd frontend && npm run build

# ---- docker ------------------------------------------------------------------
up: check-submodule ## Full platform in Docker (build + start, wait for healthchecks)
	$(COMPOSE) up --build -d --wait
	@$(MAKE) --no-print-directory urls

up-hybrid: ## Docker for everything except the Studio API (run `make studio-api` on the host for live calls)
	-$(COMPOSE) stop studio-api
	ARTEMIS_STUDIO_API_URL=http://host.docker.internal:8000 \
	  $(COMPOSE) up --build -d --wait postgres studio-ui artemis-api artemis-dashboard
	@echo "Now run the Studio API on the host: make studio-api"
	@$(MAKE) --no-print-directory urls

down: ## Stop the Docker stack (keeps volumes; `docker compose down -v` wipes data)
	$(COMPOSE) down

logs: ## Follow Docker logs (SERVICE=studio-api to filter)
	$(COMPOSE) logs -f $(SERVICE)

ps: ## Docker service status
	$(COMPOSE) ps

demo-seed: ## Upsert Studio demo bots/datasets/oracles (container if running, else host)
	@./scripts/demo-seed.sh

demo-reset: ## Drop + recreate Studio tables, then seed (destroys sessions)
	@DEMO_RESET=1 ./scripts/demo-seed.sh

urls:
	@echo
	@echo "  VoiceFlow Studio UI   http://localhost:3000"
	@echo "  VoiceFlow Studio API  http://localhost:8000/api/health"
	@echo "  Artemis dashboard     http://localhost:5173"
	@echo "  Artemis API           http://localhost:8001/health"
	@echo "  Postgres              localhost:5433 (voiceflow/voiceflow)"
	@echo
