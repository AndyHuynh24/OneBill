# OneTally developer commands. Run from the repo root.
# AWS commands use the "onetally" SSO profile unless AWS_PROFILE is already set.
export AWS_PROFILE ?= onetally

.PHONY: install test lint synth diff dev frontend-env \
	test-services test-infra test-frontend lint-services lint-infra lint-frontend

install:
	cd services && uv sync
	cd infra && npm ci
	cd frontend && npm ci

test: test-services test-infra test-frontend

test-services:
	cd services && uv run pytest

test-infra:
	cd infra && npm test

test-frontend:
	cd frontend && npm test

lint: lint-services lint-infra lint-frontend

lint-services:
	cd services && uv run ruff check . && uv run ruff format --check .

lint-infra:
	cd infra && npm run lint

lint-frontend:
	cd frontend && npm run lint

synth:
	cd infra && npx cdk synth --quiet

diff:
	cd infra && npx cdk diff --method=template

# After deploying: generate frontend/.env.local from stack outputs, then `make dev`.
frontend-env:
	./scripts/write_frontend_env.sh

dev:
	cd frontend && npm run dev
