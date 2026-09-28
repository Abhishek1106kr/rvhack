# ARC monorepo commands. Run from the repo root.

UV ?= uv
BACKEND_PORT ?= 8000
FRONTEND_PORT ?= 3000

.PHONY: install dev dev-backend dev-frontend test lint check

install:
	cd backend && $(UV) sync
	pnpm install

# Both servers in one terminal; Ctrl+C stops both.
dev:
	@trap 'kill 0' INT TERM EXIT; \
	$(MAKE) --no-print-directory dev-backend & \
	$(MAKE) --no-print-directory dev-frontend & \
	wait

dev-backend:
	cd backend && $(UV) run uvicorn app.main:app --reload --host 0.0.0.0 --port $(BACKEND_PORT)

dev-frontend:
	pnpm --filter frontend exec next dev --port $(FRONTEND_PORT)

test:
	cd backend && $(UV) run pytest

lint:
	cd backend && $(UV) run ruff check .
	pnpm --filter frontend lint
	pnpm --filter frontend typecheck

check: lint test
