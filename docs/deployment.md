# Deployment

**Status:** Phase 1 scaffold; nothing in this repository is deployable to production yet.

## Runtime choice

The scaffold uses Python 3.12 and a `uv` workspace for all service and package boundaries. Runtime placeholders use only the Python standard library; cloud SDKs, MCP libraries, HTTP frameworks, and Tesla integrations are intentionally absent until their implementation phases establish concrete requirements.

The telemetry-edge Python container is not an implementation commitment. In Phase 7, compare Tesla's then-current official Fleet Telemetry receiver and protocol guidance. Prefer the official receiver image or its native implementation with only the minimal Pub/Sub adapter, even if that introduces Go at that point.

## Intended deployment map

| Component | Eventual target | Current Phase 1 artifact |
|---|---|---|
| `mcp-gateway` | Cloud Run | Health-only container skeleton |
| `telemetry-processor` | Cloud Run | Health-only container skeleton |
| `telemetry-edge` | Compute Engine `e2-micro` VM | Health-only container skeleton |
| Shared GCP resources | Terraform in `infra/terraform` | Empty validated Terraform root |
| Per-user BigQuery datasets | Idempotent admin workflow | Deferred to Phase 3 |

The complete planned resource and trust boundaries remain in [architecture.md](architecture.md). Phase 2 will define the shared GCP resources and least-privilege IAM. It must not hard-code per-user datasets or secret values.

## Container contract

Each service image exposes port `8080` and answers `GET /healthz` with a scaffold status document. These endpoints prove only that a container starts; they do not perform readiness checks against Tesla or GCP.

Build from the repository root so each Dockerfile has a consistent context:

```bash
docker build -f services/mcp-gateway/Dockerfile -t tpp/mcp-gateway:local .
docker build -f services/telemetry-processor/Dockerfile -t tpp/telemetry-processor:local .
docker build -f services/telemetry-edge/Dockerfile -t tpp/telemetry-edge:local .
```

Production images must later be tagged by commit SHA and deployed by exact digest. `latest` is not a production identity.

## PR validation

`cloudbuild.pr.yaml` installs the locked workspace, runs formatting/lint/type/unit/dependency checks, validates and plans the empty Terraform root, and builds all service images. It performs no deployment, OAuth, Fleet API request, vehicle command, telemetry configuration, or external data write.

## Secrets

No Phase 1 setting accepts a real secret. Phase 2 may create Secret Manager containers without values; later phases inject values only at runtime as documented in [the security model](security-model.md).
