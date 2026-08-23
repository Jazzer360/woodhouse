# Tesla Personal Platform

Personal-first Tesla platform monorepo for authenticated live vehicle access,
permanent telemetry history, generic analytics, and optional semantic events.
The repository Markdown is the implementation source of truth. Through Phase 8
the platform includes per-user authentication/Tesla OAuth, complete typed Fleet
API coverage, a loopback-only official Vehicle Command Proxy, and the approved
typed live MCP surface. The official Tesla Fleet Telemetry receiver and
permanent, isolated raw-history pipeline and broad versioned per-vehicle
configuration control plane are implemented. The first real vehicle remains
paused at an explicit exact-diff operator checkpoint. Unattended
public-certificate renewal is gated by the same stable CA trust profile used by
vehicles.

## Source of truth

- [Engineering rules](AGENTS.md)
- [Architecture and repository boundaries](docs/architecture.md)
- [Security model](docs/security-model.md)
- [Tesla onboarding](docs/tesla-onboarding.md)
- [Data and analytics](docs/data-and-analytics.md)
- [Fleet Telemetry configuration](docs/fleet-telemetry-configuration.md)
- [Fleet API coverage contract](docs/fleet-api-coverage.md)
- [Events and webhooks](docs/event-and-webhooks.md)
- [Implementation roadmap](docs/implementation-roadmap.md)
- [Copy/paste prompt pack](docs/prompt-pack.md)
- [Deployment notes](docs/deployment.md)

## Stack choice

The platform uses Python 3.12 in a `uv` workspace, with Ruff, mypy, pytest, and
pip-audit. The gateway retains the standard-library HTTP server and adds a small
stateless MCP JSON-RPC boundary rather than another framework. Tesla command
signing uses Tesla's official proxy image as a private sidecar; no second
application language is maintained in this repository.

`telemetry-edge` uses Tesla's official Fleet Telemetry `v0.9.4` image pinned by
multi-platform digest. Its native Google Pub/Sub dispatcher is the minimal
adapter, so the repository contains no custom implementation of Tesla's wire
protocol. Python remains the application runtime for the authenticated
telemetry processor, isolated certificate-renewal job, and safe operator
verification.

## Layout

```text
services/                 mcp-gateway, telemetry-processor, telemetry-edge, certificate-renewer
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
docker build -f services/certificate-renewer/Dockerfile .
```

The shared-root speculative plan is generated from a backend-free temporary copy in `cloudbuild.pr.yaml`. Authoritative local plans use the bootstrapped GCS backend described in [deployment notes](docs/deployment.md).

The Cloud Build PR configuration runs the same categories without deploying or
contacting real vehicles. `cloudbuild.main.yaml` delivers affected Cloud Run
services, the certificate-renewal job, and telemetry-edge from `main` by full
commit tag and resolved image digest, preserves the gateway command-proxy
sidecar, and performs
readiness/health verification. Terraform apply remains a separate reviewed
operator action. Telemetry-edge delivery is intentionally opt-in until DNS and
certificate prerequisites pass the
[Phase 7 operator checkpoint](docs/deployment.md#phase-7-operator-checkpoint).
