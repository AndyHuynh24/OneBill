# OneBill developer commands. Run from the repo root.
# AWS commands use the "onebill-iam" profile (the IAM user in account 097021214533, no SSO)
# unless AWS_PROFILE is already set.
export AWS_PROFILE ?= onebill-iam

.PHONY: install test lint synth diff dev deploy-web frontend-env frontend-env-staging deploy-staging \
	deploy-web-staging eval eval-ask layer-requirements \
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

# Point the local frontend at a stage's stacks (frontend/.env.local).
frontend-env:
	./scripts/write_frontend_env.sh OneBill

frontend-env-staging:
	./scripts/write_frontend_env.sh OneBill-Staging

# Local frontend against STAGING (new features are tested there). STAGE=prod for production.
STAGE ?= staging
dev:
	@if [ "$(STAGE)" = "prod" ]; then ./scripts/write_frontend_env.sh OneBill; \
	else ./scripts/write_frontend_env.sh OneBill-Staging; fi
	cd frontend && npm run dev

# Staging: every stack, then its website (its own CloudFront address; email sign-in only).
deploy-staging:
	cd infra && npx cdk deploy -c stage=staging 'OneBill-Staging-*'

deploy-web-staging: frontend-env-staging
	cd frontend && npm run build
	cd infra && npx cdk deploy -c stage=staging OneBill-Staging-Web
	./scripts/write_frontend_env.sh OneBill-Staging

# Production website: build against production's stacks and publish it.
deploy-web: frontend-env
	cd frontend && npm run build
	cd infra && npx cdk deploy OneBill-Web
	@if [ -f .firebaserc ]; then npx --yes firebase-tools deploy --only hosting; fi

# Extraction accuracy on your labelled receipts (eval/receipts/, local only). Dry run unless ARGS="--yes".
eval:
	cd services && uv run python ../eval/run_eval.py --profile $(AWS_PROFILE) $(ARGS)

# Ask OneBill on fixed questions over a fake dataset (eval/ask_cases.json). Dry run unless ARGS="--yes".
eval-ask:
	cd services && uv run python ../eval/run_ask_eval.py --profile $(AWS_PROFILE) $(ARGS)

# Re-export the Pillow layer's hash-pinned requirements after changing the lambda-layer group.
layer-requirements:
	cd services && uv export --only-group lambda-layer --format requirements-txt --no-emit-project --no-header > ../infra/layers/pillow/requirements.txt
