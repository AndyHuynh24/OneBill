# OneBill developer commands. Run from the repo root.
# AWS commands use the "onebill-iam" profile (the IAM user in account 097021214533, no SSO)
# unless AWS_PROFILE is already set. See docs/runbooks/new-aws-account.md.
export AWS_PROFILE ?= onebill-iam

.PHONY: install test lint synth diff dev frontend-env eval layer-requirements \
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
	cd services && uv run ruff check . ../eval && uv run ruff format --check . ../eval

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

# Extraction accuracy on your labelled receipts (eval/receipts/, local only). Dry run unless ARGS="--yes".
eval:
	cd services && uv run python ../eval/run_eval.py --profile $(AWS_PROFILE) $(ARGS)

# Re-export the Pillow layer's hash-pinned requirements after changing the lambda-layer group.
layer-requirements:
	cd services && uv export --only-group lambda-layer --format requirements-txt --no-emit-project --no-header > ../infra/layers/pillow/requirements.txt
