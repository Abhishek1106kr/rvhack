# ARC monorepo commands. Run from the repo root.

UV ?= uv
BACKEND_PORT ?= 8000
FRONTEND_PORT ?= 3000
# Which server to run: problem.app (the assembled domain agent) or app.main (runtime only, dev adapters).
ARC_APP ?= problem.app:app
ARC_LLM_MODEL ?= qwen2.5:1.5b
MODELS := $(CURDIR)/models
PIPER_VOICE := en_US-lessac-medium
PIPER_URL := https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/lessac/medium
SILERO_URL := https://github.com/snakers4/silero-vad/raw/v6.2/src/silero_vad/data/silero_vad.onnx

export ARC_MODELS_DIR := $(MODELS)
export ARC_LLM_MODEL

.PHONY: install models dev dev-backend dev-frontend dev-ollama test lint check eval-live deploy deploy-logs deploy-down

install:
	cd backend && $(UV) sync
	pnpm install

# Everything the real voice loop needs, downloaded once. Run with wifi before the demo.
models:
	mkdir -p $(MODELS)/piper $(MODELS)/whisper
	test -f $(MODELS)/silero_vad.onnx || curl -fL -o $(MODELS)/silero_vad.onnx $(SILERO_URL)
	test -f $(MODELS)/piper/$(PIPER_VOICE).onnx || curl -fL -o $(MODELS)/piper/$(PIPER_VOICE).onnx $(PIPER_URL)/$(PIPER_VOICE).onnx
	test -f $(MODELS)/piper/$(PIPER_VOICE).onnx.json || curl -fL -o $(MODELS)/piper/$(PIPER_VOICE).onnx.json $(PIPER_URL)/$(PIPER_VOICE).onnx.json
	cd backend && $(UV) run python -c "from faster_whisper import WhisperModel; WhisperModel('base.en', device='cpu', compute_type='int8', download_root='$(MODELS)/whisper')"
	ollama pull $(ARC_LLM_MODEL)

# Ollama, backend and control room in one terminal; Ctrl+C stops all three.
dev:
	@trap 'kill 0' INT TERM EXIT; \
	$(MAKE) --no-print-directory dev-ollama & \
	$(MAKE) --no-print-directory dev-backend & \
	$(MAKE) --no-print-directory dev-frontend & \
	wait

# Starts `ollama serve` unless one is already running (e.g. as a system service).
dev-ollama:
	@curl -sf localhost:11434/api/version >/dev/null && echo "ollama already running" || ollama serve

dev-backend:
	cd backend && PYTHONPATH=$(CURDIR) $(UV) run uvicorn $(ARC_APP) --reload \
		--reload-dir . --reload-dir $(CURDIR)/problem --host 0.0.0.0 --port $(BACKEND_PORT)

dev-frontend:
	pnpm --filter frontend exec next dev --port $(FRONTEND_PORT)

test:
	cd backend && $(UV) run pytest

lint:
	cd backend && $(UV) run ruff check . ../problem
	pnpm --filter frontend lint
	pnpm --filter frontend typecheck

check: lint test

# Real model decisions + latency (needs `make models` and Ollama running).
eval-live:
	cd backend && PYTHONPATH=$(CURDIR) $(UV) run python -m problem.evaluation.live_eval

# Production stack: Ollama + backend + control room behind Caddy (see docs/deploy.md).
deploy:
	docker compose up -d --build
	@echo "ARC is starting on http://localhost:$${ARC_HTTP_PORT:-8080} (first run pulls the LLM)"

deploy-logs:
	docker compose logs -f backend caddy

deploy-down:
	docker compose down
