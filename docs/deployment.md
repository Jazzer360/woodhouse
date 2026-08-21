# Deployment

**Status:** Phase 2 shared-infrastructure baseline. The configuration is production-shaped but still hosts health-only placeholders and implements no Tesla, OAuth, command, or telemetry behavior.

## Fixed deployment choices

- GCP project: `woodhouse-506215`
- primary region: `us-central1`
- telemetry VM zone: `us-central1-a`
- implementation runtime: Python 3.12 with a `uv` workspace
- shared infrastructure: Terraform with a GCS backend

Python remains the smallest practical common stack for the planned MCP, GCP, and analytics components. The telemetry-edge Python image remains a placeholder: Phase 7 must compare it with Tesla's then-current official receiver and may adopt the official/native implementation.

## Resource map

| Resource | Terraform identity | Phase 2 behavior |
|---|---|---|
| Artifact Registry | `tesla-personal-platform` | New immutable-tag Docker repository |
| MCP gateway | Cloud Run `mcp-gateway` | Private, health-only public placeholder image; no unauthenticated invoker |
| Telemetry processor | Cloud Run `telemetry-processor` | Internal ingress, authenticated same-project Pub/Sub invoker only |
| Telemetry edge | Compute Engine `tpp-telemetry-edge` | Idle shielded COS `e2-micro`; no receiver or container deployed yet |
| Telemetry address | Regional static external IPv4 | Reserved for the future public receiver |
| Raw transport | `tpp-raw-telemetry` topic and processor subscription | 31-day retention; authenticated push path |
| Mutable state | Firestore Native `(default)` | Regional database with delete protection |
| Secret storage | Four Secret Manager containers | Metadata and IAM only; no secret versions or values |
| Quarantine | `tesla_system_quarantine.raw_unknown_telemetry` | Restricted, partitioned append destination for unmapped telemetry |
| Monitoring | backlog alert and unknown-vehicle log metric | No notification destination unless existing channel IDs are supplied |
| Network | custom VPC and `/28` subnet | No default ingress rules |

The MCP gateway receives project-level BigQuery job permission but no project-level data access. The telemetry processor can write only the shared quarantine dataset until Phase 3 grants it access to a newly created user's dataset.

## Network exposure

The telemetry VM has exactly two ingress rules:

- TCP `443` from the public internet to the telemetry-edge service account;
- TCP `22` only from Google's IAP TCP-forwarding range `35.235.240.0/20`.

Tesla's current Fleet Telemetry overview requires a publicly reachable server but does not prescribe a port on that page. Port `443` is the documented platform default and is configurable through `fleet_telemetry_port`; Phase 7 must make the vehicle configuration, receiver listener, certificate, and firewall agree. No process listens on that port in Phase 2.

Project SSH keys are blocked, OS Login is required, and no direct public SSH rule exists. The VM service account can publish to the raw topic and write logs/metrics. It has no Tesla OAuth, command-key, Secret Manager, Firestore, or BigQuery access.

[Cloud Run recognizes same-project Pub/Sub subscriptions as an allowed source for internal ingress](https://cloud.google.com/run/docs/securing/ingress#available_network_ingress_settings), so the telemetry processor does not need public ingress for push delivery. [Compute Engine recommends the `cloud-platform` OAuth scope with access controlled through IAM roles](https://cloud.google.com/compute/docs/access/service-accounts#authorization); that scope is used on the VM, with effective authorization restricted by the service account's narrow IAM roles. Legacy granular OAuth scopes do not grant permissions and do not cover every authentication protocol.

## Service accounts and IAM intent

| Identity | Granted access |
|---|---|
| `tpp-mcp-gateway` | Firestore user; BigQuery job user; accessor on the MCP auth, Tesla client, and command-key secret containers |
| `tpp-telemetry-processor` | Firestore user; writer on the quarantine dataset |
| `tpp-telemetry-edge` | Publisher on the raw topic; log and metric writer |
| `tpp-pubsub-push` | Invoker on telemetry-processor only |
| `tpp-build-validator` | Log writer only; no deploy, secret, or data permission |
| `tpp-build-deployer` | Artifact Registry writer on this repository, Cloud Run developer, and `actAs` only on the two Cloud Run runtime accounts |

The Cloud Build service agent may mint tokens only for the two custom build identities. Neither build identity receives Owner, Editor, Secret Manager access, BigQuery data access, or vehicle-VM administration.

### Phase 3 per-user dataset contract

The idempotent `add-user` workflow—not shared Terraform—will create `tesla_u_<opaque_user_id>` with no default expiration and grant:

- `roles/bigquery.dataEditor` on that dataset to `tpp-telemetry-processor`;
- `roles/bigquery.dataViewer` on that dataset to `tpp-mcp-gateway`.

The gateway already has project-level `roles/bigquery.jobUser` so it can run scoped queries. No caller supplies a dataset ID, and no shared project-level data-reader/writer role is granted.

## One-time operator bootstrap

Install Terraform 1.9+ and the Google Cloud CLI, select `woodhouse-506215`, then create Application Default Credentials:

```bash
gcloud config set project woodhouse-506215
gcloud auth application-default login
```

The account performing the initial bootstrap needs the following project roles. They are deliberately explicit instead of using Owner or Editor:

- `roles/serviceusage.serviceUsageAdmin`
- `roles/resourcemanager.projectIamAdmin`
- `roles/iam.serviceAccountAdmin`
- `roles/iam.serviceAccountUser`
- `roles/artifactregistry.admin`
- `roles/run.admin`
- `roles/pubsub.admin`
- `roles/datastore.owner`
- `roles/secretmanager.admin`
- `roles/compute.admin`
- `roles/compute.securityAdmin`
- `roles/bigquery.admin`
- `roles/logging.admin`
- `roles/monitoring.editor`
- `roles/cloudbuild.builds.editor`
- `roles/storage.admin` for creation and administration of the state bucket

These bootstrap roles belong to the human/operator automation applying the reviewed Terraform, not to an application runtime. After bootstrap, reduce the operator to the permissions required for reviewed plans and applies. Terraform still needs resource-specific admin roles and project IAM policy write access whenever configuration changes those resources.

To administer telemetry-edge through IAP, place the operator's IAM member string in `admin_principals`. Terraform then grants only IAP tunnel access, OS Admin Login, Compute Viewer, and `actAs` on the telemetry-edge service account. Example values belong in an uncommitted `.tfvars` file:

```hcl
admin_principals = ["user:operator@example.com"]
```

## Terraform state bootstrap

The small `infra/terraform/bootstrap` root uses local state once to create `woodhouse-506215-tpp-tfstate`. The bucket has uniform access, enforced public-access prevention, object versioning, and cleanup of old noncurrent versions after 90 days while retaining at least 20 newer versions. It deliberately has no bucket retention policy because that can prevent Terraform from deleting its own state-lock object.

```bash
terraform -chdir=infra/terraform/bootstrap init
terraform -chdir=infra/terraform/bootstrap plan -out=bootstrap.tfplan
terraform -chdir=infra/terraform/bootstrap apply bootstrap.tfplan

terraform -chdir=infra/terraform init \
  -backend-config=backend.gcs.tfbackend.example
terraform -chdir=infra/terraform plan -out=shared.tfplan
terraform -chdir=infra/terraform apply shared.tfplan
```

State operators need `roles/storage.objectAdmin` on the state bucket. Keep the bootstrap state protected until the bucket exists; it contains no secret values, is ignored by Git, and can be reconstructed by importing the bucket. Do not place runtime secrets in Terraform input because ordinary resource attributes may appear in state.

If Firestore or the state bucket already exists, import it rather than attempting duplicate creation. The Artifact Registry repository is intentionally created by this configuration because none exists yet.

## Cloud Build flow

PR validation uses `cloudbuild.pr.yaml` with the validator identity. It runs Python quality/tests/audit, container builds, Terraform formatting/validation, and `-refresh=false` speculative plans for both Terraform roots. For the shared root it plans a temporary copy without the GCS backend declaration, so the result checks the complete create graph rather than live drift. The validator has only log-writing permission and cannot read or mutate GCP resources.

The GitHub repository connection and trigger are external bootstrap concerns, so Terraform does not guess their connection IDs. Configure the PR trigger to use `tpp-build-validator`. A later main-branch delivery trigger uses `tpp-build-deployer` to push images tagged by the full commit SHA and deploy only the affected Cloud Run services. Terraform ignores the deployed container-image field so a later infrastructure plan cannot roll an application back to the Phase 2 placeholder.

Terraform apply remains an explicit reviewed operation from the merged `main` state in Phase 2. Automating it requires a separately reviewed apply identity/approval gate; the application deployer intentionally cannot change IAM, networks, secrets, Firestore, BigQuery, Pub/Sub, or Compute Engine.

Telemetry-edge delivery is deferred to Phase 7, when the VM will pull an exact image digest, health-check it, and support rollback. Production must never identify a service image as `latest`.

## Secret handling

Terraform creates these empty containers:

- `mcp-auth-signing-key`
- `tesla-client-secret`
- `tesla-command-private-key`
- `webhook-hmac-key`

No secret version, key material, token, PIN, service-account key, or example value is committed. Later phases add values out-of-band and inject only the minimum runtime references.
