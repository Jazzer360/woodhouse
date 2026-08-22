# Tesla Personal Platform

Personal-first Tesla platform monorepo for authenticated live vehicle access, permanent telemetry history, generic analytics, and optional semantic events. The repository Markdown is the implementation source of truth. Phase 4 adds the Tesla developer-app registration boundary, per-user OAuth, encrypted rotating credentials, multi-vehicle discovery, and vehicle-specific Virtual Key state. Broad Fleet API behavior, commands, and telemetry remain deferred.

## Source of truth

- [Engineering rules](AGENTS.md)
- [Architecture and repository boundaries](docs/architecture.md)
- [Security model](docs/security-model.md)
- [Tesla onboarding](docs/tesla-onboarding.md)
- [Data and analytics](docs/data-and-analytics.md)
- [Fleet API coverage contract](docs/fleet-api-coverage.md)
- [Events and webhooks](docs/event-and-webhooks.md)
- [Implementation roadmap](docs/implementation-roadmap.md)
- [Copy/paste prompt pack](docs/prompt-pack.md)
- [Deployment notes](docs/deployment.md)

## Stack choice

The platform uses Python 3.12 in a `uv` workspace, with Ruff, mypy, pytest, and pip-audit. Phase 4 retains the standard-library HTTP server, uses PyJWT for Tesla OIDC verification, `cryptography` for AES-GCM token protection and P-256 public-key validation, and the official Google clients for Firestore, BigQuery, and Secret Manager. No web framework is added.

`telemetry-edge` is only a health-checkable container placeholder. Phase 7 must re-evaluate its implementation against Tesla's current official Fleet Telemetry receiver; adopting the official receiver or a small Go adapter then is preferable to introducing a second language now.

## Layout

```text
services/                 mcp-gateway, telemetry-processor, telemetry-edge
packages/                 tesla-client, auth, shared-models, analytics, event-schema
infra/terraform/          shared GCP baseline and one-time state bootstrap
scripts/admin/            manual administration entry points added in later phases
scripts/dev/              local development notes
docs/runbooks/            operational placeholders completed by relevant phases
```

## Phase sequence

Follow [the implementation roadmap](docs/implementation-roadmap.md) in order: scaffold; GCP baseline; platform auth; Tesla onboarding; typed Fleet client; live MCP; raw telemetry; telemetry configuration; analytics; optional events/webhooks; then hardening. Merge and review each phase before beginning the next.

## Validate

```bash
uv sync --frozen --all-packages --group dev
uv run ruff check .
uv run ruff format --check .
uv run mypy services packages tests
uv run pytest
uv export --frozen --all-packages --no-emit-workspace --output-file .uv-cache/audit-requirements.txt --quiet
uv run pip-audit --strict --requirement .uv-cache/audit-requirements.txt
terraform -chdir=infra/terraform fmt -check
terraform -chdir=infra/terraform/bootstrap init -backend=false
terraform -chdir=infra/terraform/bootstrap validate
terraform -chdir=infra/terraform/bootstrap plan -refresh=false -input=false -lock=false \
  -var=project_id=woodhouse-506215 \
  -var=state_bucket_name=woodhouse-506215-tpp-tfstate
terraform -chdir=infra/terraform init -backend=false
terraform -chdir=infra/terraform validate
docker build -f services/mcp-gateway/Dockerfile .
docker build -f services/telemetry-processor/Dockerfile .
docker build -f services/telemetry-edge/Dockerfile .
```

The shared-root speculative plan is generated from a backend-free temporary copy in `cloudbuild.pr.yaml`. Authoritative local plans use the bootstrapped GCS backend described in [deployment notes](docs/deployment.md).

The Cloud Build PR configuration runs the same categories without deploying or contacting real vehicles.
