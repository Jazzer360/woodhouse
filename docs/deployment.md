# Deployment

**Status:** Phase 4 Tesla-onboarding baseline. The gateway retains the Phase 3 Google OIDC/allowlist boundary and adds fail-closed Tesla OAuth, encrypted token rotation, multi-vehicle discovery, and per-vehicle Virtual Key status. Broad commands and telemetry configuration remain unimplemented.

## Fixed deployment choices

- GCP project: `woodhouse-506215`
- primary region: `us-central1`
- telemetry VM zone: `us-central1-a`
- implementation runtime: Python 3.12 with a `uv` workspace
- shared infrastructure: Terraform with a GCS backend

Python remains the smallest practical common stack for the planned MCP, GCP, and analytics components. The telemetry-edge Python image remains a placeholder: Phase 7 must compare it with Tesla's then-current official receiver and may adopt the official/native implementation.

## Resource map

| Resource | Terraform identity | Current behavior |
|---|---|---|
| Artifact Registry | `tesla-personal-platform` | New immutable-tag Docker repository |
| MCP gateway | Cloud Run `mcp-gateway` | Health, authenticated `/mcp`, Tesla onboarding routes, and the public Tesla application-key path; Tesla behavior is separately opt-in |
| Telemetry processor | Cloud Run `telemetry-processor` | Internal ingress, authenticated same-project Pub/Sub invoker only |
| Telemetry edge | Compute Engine `tpp-telemetry-edge` | Idle shielded COS `e2-micro`; no receiver or container deployed yet |
| Telemetry address | Regional static external IPv4 | Reserved for the future public receiver |
| Raw transport | `tpp-raw-telemetry` topic and processor subscription | 31-day retention; authenticated push path |
| Mutable state | Firestore Native `(default)` | Allowlist and atomic immutable OIDC identity bindings; regional database with delete protection |
| Secret storage | Six Secret Manager containers | Terraform manages containers/IAM only; operators add secret versions out of band |
| Quarantine | `tesla_system_quarantine.raw_unknown_telemetry` | Restricted, partitioned append destination for unmapped telemetry |
| Monitoring | backlog alert and unknown-vehicle log metric | No notification destination unless existing channel IDs are supplied |
| Network | custom VPC and `/28` subnet | No default ingress rules |

The MCP gateway receives project-level BigQuery job permission but no project-level data access. Its Secret Manager access covers platform auth, the Tesla client secret, the public application key, and the token-encryption key. It deliberately cannot read the Tesla command private key before the Phase 6 signing runtime exists. The telemetry processor can write only the shared quarantine dataset until the user workflow grants it access to a newly created user's dataset.

The `tpp-user-admin` service account is keyless and used only through operator
impersonation. It can write Firestore allowlist entities, create BigQuery
datasets, and update dataset metadata/ACLs through a custom role containing only
`bigquery.datasets.create`, `bigquery.datasets.get`, and
`bigquery.datasets.update`. It cannot run BigQuery jobs and has no BigQuery
table-data, Secret Manager, Cloud Run deployment, or vehicle API access.

The keyless `tpp-dataset-owner` service account has no project-level roles, keys,
or operator impersonation binding. BigQuery requires every dataset policy to
retain a direct owner, so each per-user dataset grants that otherwise dormant
identity `OWNER`. Keeping this mandatory data-capable entry separate prevents
the impersonatable `tpp-user-admin` path from reading user telemetry.

Firestore IAM is database-wide and cannot scope `roles/datastore.user` to only
the `allowed_users` collection. This is the principal Phase 3 IAM tradeoff: the
keyless admin identity is operator-only and impersonation-audited, but it can
read/write other documents in the default Firestore database. Revisit a separate
admin service/database or narrower mediation before delegating this workflow
beyond trusted operators.

## Network exposure

The telemetry VM has exactly two ingress rules:

- TCP `443` from the public internet to the telemetry-edge service account;
- TCP `22` only from Google's IAP TCP-forwarding range `35.235.240.0/20`.

Tesla's current Fleet Telemetry overview requires a publicly reachable server but does not prescribe a port on that page or publish stable source CIDRs that can safely replace `0.0.0.0/0`. Port `443` is the documented platform default and is configurable through `fleet_telemetry_port`; Phase 7 must make the vehicle configuration, receiver listener, certificate, and firewall agree. The rule targets only the telemetry-edge service account and that single TCP port. No process listens on that port yet. If Tesla later publishes an authoritative sender range, restrict the rule in the same reviewed change that verifies receiver delivery.

Firewall logging remains enabled for IAP administration but is disabled on the public Fleet Telemetry allow rule. Unauthenticated internet scanning would otherwise create unbounded log volume and cost; Phase 7 receiver health, application logs, and metrics provide useful operational visibility once a listener exists.

Project SSH keys are blocked, OS Login is required, and no direct public SSH rule exists. The VM service account can publish to the raw topic and write logs/metrics. It has no Tesla OAuth, command-key, Secret Manager, Firestore, or BigQuery access.

[Cloud Run recognizes same-project Pub/Sub subscriptions as an allowed source for internal ingress](https://cloud.google.com/run/docs/securing/ingress#available_network_ingress_settings), so the telemetry processor does not need public ingress for push delivery. [Compute Engine recommends the `cloud-platform` OAuth scope with access controlled through IAM roles](https://cloud.google.com/compute/docs/access/service-accounts#authorization); that scope is used on the VM, with effective authorization restricted by the service account's narrow IAM roles. Legacy granular OAuth scopes do not grant permissions and do not cover every authentication protocol.

The Pub/Sub push subscription uses the complete processor handler URL, including `/pubsub/push`, as both its delivery endpoint and OIDC audience. Phase 7 token validation must require that exact audience rather than accepting the broader service-root URI.

## Service accounts and IAM intent

| Identity | Granted access |
|---|---|
| `tpp-mcp-gateway` | Firestore user; BigQuery job user; accessor on MCP auth, Tesla client-secret, public-key, and token-encryption secret containers; no private command-key access yet |
| `tpp-telemetry-processor` | Firestore user; writer on the quarantine dataset |
| `tpp-telemetry-edge` | Publisher on the raw topic; log and metric writer |
| `tpp-pubsub-push` | Invoker on telemetry-processor only |
| `tpp-build-validator` | Log writer only; no deploy, secret, or data permission |
| `tpp-build-deployer` | Artifact Registry writer on this repository, Cloud Run developer, and `actAs` only on the two Cloud Run runtime accounts |
| `tpp-user-admin` | Firestore user; BigQuery dataset creator; update metadata/ACLs on datasets; no table-data access |
| `tpp-partner-admin` | Secret accessor only for Tesla client-secret and public-key containers; no project role or runtime impersonation |
| `tpp-dataset-owner` | Required direct owner on per-user datasets; no project roles, keys, or impersonation binding |

The Cloud Build service agent may mint tokens only for the two custom build identities. Neither build identity receives Owner, Editor, Secret Manager access, BigQuery data access, or vehicle-VM administration.

Members in `user_admin_principals` receive only
`roles/iam.serviceAccountTokenCreator` on `tpp-user-admin`, allowing keyless ADC
impersonation for the manual workflow. They do not receive that service account's
permissions directly and no service-account key is created.

[Cloud Run uses its service agent to access deployed container images](https://cloud.google.com/run/docs/securing/service-identity#service-agent), and same-project Artifact Registry access needs no additional runtime-account grant. The `tpp-mcp-gateway` and `tpp-telemetry-processor` runtime identities therefore do not receive `roles/artifactregistry.reader`; adding it would expose repository contents to application code without helping Cloud Run pull an image. Cross-project repositories would instead require an explicit reader grant to the Cloud Run service agent.

### Phase 3 per-user dataset contract

The idempotent `add-user` workflow—not shared Terraform—will create `tesla_u_<opaque_user_id>` with no default expiration and grant:

- `OWNER` on that dataset to the dormant, non-impersonatable `tpp-dataset-owner` identity, as required by BigQuery;
- `roles/bigquery.dataEditor` on that dataset to `tpp-telemetry-processor`;
- `roles/bigquery.dataViewer` on that dataset to `tpp-mcp-gateway`.

The gateway already has project-level `roles/bigquery.jobUser` so it can run scoped queries. No caller supplies a dataset ID, and no shared project-level data-reader/writer role is granted.

The Python BigQuery client serializes these two dataset grants as legacy dataset
ACL roles `READER` and `WRITER`; for service-account principals those are the
dataset-level equivalents of `roles/bigquery.dataViewer` and
`roles/bigquery.dataEditor`. The workflow makes this ACL authoritative rather
than retaining BigQuery's default `projectReaders`/`projectWriters` entries;
re-running it removes ambient or drifted dataset grants. If a later phase needs
another principal or authorized view, it must update this workflow explicitly.

## Platform OIDC configuration

Create a Google OAuth/OIDC client for the intended MCP client and record its
client ID as `oidc_audience`. A client ID is not a secret; do not commit its
client secret if the chosen provider flow issues one. The gateway validates only
Google-signed ID tokens whose `aud` exactly matches this configured value and
whose issuer is `accounts.google.com` or `https://accounts.google.com`.

Terraform defaults `enable_mcp_external_access` to `false`. After deploying the
Phase 3 gateway image and configuring `oidc_audience`, explicitly set:

```hcl
oidc_audience              = "your-google-oauth-client-id"
enable_mcp_external_access = true
```

That switch grants the Cloud Run route to `allUsers` so internet MCP clients can
reach application-level authentication; it does not grant application access.
The gateway returns `401` unless the bearer token is valid and resolves to an
active immutable allowlist binding. Terraform refuses to enable the route with
no audience, and the process refuses to start without both `OIDC_AUDIENCE` and
`GOOGLE_CLOUD_PROJECT`.

The reserved `/mcp` route authenticates and applies the tenant-input guard before
returning the expected Phase 3 `501` response; actual MCP protocol/tools arrive
in later phases. Public smoke checks use `/health`, which contains no identity
state and remains unauthenticated. The service also exposes `/healthz` inside the
container for local health checks; Google Front End reserves that literal path
on the public `run.app` hostname and returns its own `404` before the container.

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

For manual platform-user administration, separately set:

```hcl
user_admin_principals = ["user:operator@example.com"]
```

This does not make the operator a project administrator. It permits impersonation
of only `tpp-user-admin`, whose exact permissions are described above.

For Tesla partner registration, set `partner_admin_principals`. This permits
keyless impersonation of only `tpp-partner-admin`, which can read the Tesla
client-secret and public-key containers but has no Firestore, BigQuery, Cloud
Run, infrastructure, or vehicle-command role.

## Phase 4 Tesla onboarding configuration

Terraform adds `tesla-command-public-key` and `tesla-token-encryption-key`
containers alongside the existing Tesla client-secret and private-command-key
containers. It never creates a secret version. The gateway receives the client
secret, public key, and token-encryption key only through Cloud Run Secret
Manager environment references when `enable_tesla_onboarding = true`. The
private command key is intentionally not injected until the Phase 6 signing
runtime exists.

The enable switch defaults false so a plan/apply remains deployable before the
operator creates secret versions. Enabling requires these non-secret values in
the uncommitted tfvars file:

```hcl
enable_tesla_onboarding  = true
tesla_client_id          = "Tesla dashboard client ID"
tesla_app_domain         = "woodhouse.derekjass.com"
tesla_oauth_redirect_uri = "https://woodhouse.derekjass.com/oauth/callback"
tesla_initial_audience   = "https://fleet-api.prd.na.vn.cloud.tesla.com"
```

Runtime state uses these Firestore collections:

- `tesla_oauth_states`: hashed, single-use, ten-minute callback bindings;
- `tesla_connections`: one encrypted rotating token state per platform user;
- `vehicles`: safe vehicle metadata and per-vehicle key status;
- `vehicle_vin_index`: collision-safe VIN-to-owner mapping used to reject
  cross-user ownership conflicts.

Application delivery still deploys an immutable commit-SHA image after merge.
Terraform controls configuration and secret references but ignores the image
field so it cannot roll the application back to the original placeholder.
Follow the ordered live procedure in
[`docs/tesla-onboarding.md`](tesla-onboarding.md#12-required-operator-checkpoint--first-real-tesla-onboarding).

## Manual add Homer workflow

After the reviewed Terraform configuration has created `tpp-user-admin` and the
operator impersonation binding, establish keyless Application Default
Credentials and install the locked workspace:

```bash
gcloud auth application-default login \
  --impersonate-service-account=tpp-user-admin@woodhouse-506215.iam.gserviceaccount.com
uv sync --frozen --all-packages --group dev
```

Add Homer:

```bash
uv run python scripts/admin/add-user \
  --project-id woodhouse-506215 \
  --email homer@example.com \
  --notes "Homer"
```

The command transactionally allocates stable random `user_id` and `dataset_id`
values, creates or repairs the `us-central1` dataset with no default table or
partition expiration, grants the dormant `tpp-dataset-owner` identity the direct
owner entry required by BigQuery, and enforces only gateway read and processor
write runtime ACLs, then
marks the invitation active. Re-running the command reuses the
same identifiers and repairs drift. If dataset provisioning fails for a new
invitation, the record remains disabled and a safe retry completes it.

On Homer's first protected request, the gateway requires a verified
`homer@example.com` claim and atomically binds its Google issuer/subject. Later
email changes do not change authorization. Do not edit `oidc_issuer` or
`oidc_subject` to transfer an account; disable and follow a reviewed recovery
procedure instead.

To block access without deleting history, dataset ACLs, or the immutable binding:

```bash
uv run python scripts/admin/disable-user \
  --project-id woodhouse-506215 \
  --email homer@example.com
```

`disable-user` is idempotent. It does not revoke Google itself, delete BigQuery
data, alter Tesla consent, or remove a future vehicle Virtual Key.

## Terraform state bootstrap

The small `infra/terraform/bootstrap` root uses local state once to create `woodhouse-506215-tpp-tfstate`. The bucket has uniform access, enforced public-access prevention, object versioning, and cleanup of old noncurrent versions after 90 days while retaining at least 20 newer versions. It deliberately has no bucket retention policy because that can prevent Terraform from deleting its own state-lock object.

```bash
cp infra/terraform/bootstrap/terraform.tfvars.example \
  infra/terraform/bootstrap/terraform.tfvars
cp infra/terraform/terraform.tfvars.example \
  infra/terraform/terraform.tfvars

terraform -chdir=infra/terraform/bootstrap init
terraform -chdir=infra/terraform/bootstrap plan \
  -var-file=terraform.tfvars -out=bootstrap.tfplan
terraform -chdir=infra/terraform/bootstrap apply bootstrap.tfplan

terraform -chdir=infra/terraform init \
  -backend-config=backend.gcs.tfbackend.example
terraform -chdir=infra/terraform plan \
  -var-file=terraform.tfvars -out=shared.tfplan
terraform -chdir=infra/terraform apply shared.tfplan
```

Review the copied, ignored `.tfvars` files before planning. Both Terraform roots require `project_id`; bootstrap also requires `state_bucket_name`. This keeps `woodhouse-506215` as the documented deployment choice while preventing an unparameterized clone or bare apply from silently targeting it.

State operators need `roles/storage.objectAdmin` on the state bucket. Keep the bootstrap state protected until the bucket exists; it contains no secret values, is ignored by Git, and can be reconstructed by importing the bucket. Do not place runtime secrets in Terraform input because ordinary resource attributes may appear in state.

If Firestore or the state bucket already exists, import it rather than attempting duplicate creation. The Artifact Registry repository is intentionally created by this configuration because none exists yet.

## Cloud Build flow

PR validation uses `cloudbuild.pr.yaml` with the validator identity. It runs Python quality/tests/audit, container builds, Terraform formatting/validation, and `-refresh=false` speculative plans for both Terraform roots. For the shared root it copies the complete Terraform directory, excluding only the nested bootstrap root, generated `.terraform` data, and the dedicated `backend.tf`, so future modules, templates, and other Terraform inputs participate automatically. The result checks the complete create graph rather than live drift. The validator has only log-writing permission and cannot read or mutate GCP resources.

The GitHub repository connection and trigger are external bootstrap concerns, so Terraform does not guess their connection IDs. Configure the PR trigger to use `tpp-build-validator`. A later main-branch delivery trigger uses `tpp-build-deployer` to push images tagged by the full commit SHA and deploy only the affected Cloud Run services. Terraform ignores the deployed container-image field so a later infrastructure plan cannot roll an application back to the Phase 2 placeholder.

Terraform apply remains an explicit reviewed operation from merged `main`. Automating it requires a separately reviewed apply identity/approval gate; the application deployer intentionally cannot change IAM, networks, secrets, Firestore, BigQuery, Pub/Sub, or Compute Engine.

Telemetry-edge delivery is deferred to Phase 7, when the VM will pull an exact image digest, health-check it, and support rollback. Production must never identify a service image as `latest`.

## Secret handling

Terraform creates these empty containers:

- `mcp-auth-signing-key`
- `tesla-client-secret`
- `tesla-command-private-key`
- `webhook-hmac-key`

No secret version, key material, token, PIN, service-account key, or example value is committed. Later phases add values out-of-band and inject only the minimum runtime references.
