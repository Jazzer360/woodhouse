# Deployment

**Status:** Phase 6 live MCP baseline. The gateway retains the Google
OIDC/allowlist and per-user Tesla OAuth boundaries, exposes the coverage-matrix
MCP surface, and routes signed commands through an instance-local official Tesla
Vehicle Command Proxy sidecar. Broad Fleet Telemetry configuration remains
deferred.

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
| Build source staging | GCS `${project_id}-tpp-cloudbuild-source` | Private source objects expire after seven days; deployer receives bucket-scoped read access only |
| MCP gateway | Cloud Run `mcp-gateway` | Health, authenticated stateless Streamable HTTP `/mcp`, Tesla onboarding routes, live Fleet reads, and the public Tesla application-key path |
| Vehicle Command Proxy | `mcp-gateway` Cloud Run sidecar | Official digest-pinned image; loopback TLS only; signs typed vehicle commands |
| Telemetry processor | Cloud Run `telemetry-processor` | Internal ingress, authenticated same-project Pub/Sub invoker only |
| Telemetry edge | Compute Engine `tpp-telemetry-edge` | Idle shielded COS `e2-micro`; no receiver or container deployed yet |
| Telemetry address | Regional static external IPv4 | Reserved for the future public receiver |
| Raw transport | `tpp-raw-telemetry` topic and processor subscription | 31-day retention; authenticated push path |
| Mutable state | Firestore Native `(default)` | Allowlist and atomic immutable OIDC identity bindings; regional database with delete protection |
| Secret storage | Eight Secret Manager containers | Terraform manages containers/IAM only; operators add secret versions out of band |
| Quarantine | `tesla_system_quarantine.raw_unknown_telemetry` | Restricted, partitioned append destination for unmapped telemetry |
| Monitoring | backlog alert, unknown-vehicle log metric, and OAuth callback request-log exclusion | No notification destination unless existing channel IDs are supplied; callback query URLs are excluded from Cloud Logging |
| Network | custom VPC and `/28` subnet | No default ingress rules |

The MCP gateway receives project-level BigQuery job permission but no
project-level data access. Its application container receives platform auth,
the Tesla client secret, public application key, token-encryption key, and the
proxy's public TLS certificate. Only the official proxy sidecar mounts the Tesla
command private key and proxy TLS private key. Cloud Run assigns one service
identity to the whole multi-container revision, so Secret Manager IAM cannot
distinguish the sidecar from the application container; mount/environment
isolation and the absence of Secret Manager client code in the application are
the practical boundary. Revisit a separately identified signing service if the
deployment becomes broadly multi-tenant. The telemetry processor and
telemetry-edge receive none of the command-proxy secrets.

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
| `tpp-mcp-gateway` | Firestore user; BigQuery job user; runtime secret access for onboarding plus command/proxy key material mounted only into the command-proxy sidecar |
| `tpp-telemetry-processor` | Firestore user; writer on the quarantine dataset |
| `tpp-telemetry-edge` | Publisher on the raw topic; log and metric writer |
| `tpp-pubsub-push` | Invoker on telemetry-processor only |
| `tpp-build-validator` | Log writer only; no deploy, secret, or data permission |
| `tpp-build-deployer` | Artifact Registry writer on this repository, Cloud Run developer, `actAs` only on the two Cloud Run runtime accounts, and object viewer only on the dedicated short-lived build-source bucket |
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

The Phase 6.1 default is Auth0 as the OAuth 2.1 authorization server, with
Google configured as its upstream social connection. This is two distinct
pieces: Auth0 issues MCP-resource access tokens to ChatGPT and browser sessions;
Google authenticates the human. The Firestore allowlist remains the final
authorization decision.

Create an Auth0 API/resource server whose identifier is exactly:

```text
https://woodhouse.derekjass.com/mcp
```

Use RS256 access tokens and define the `mcp:access` permission. In **Tenant
Settings > Default Audience**, select this API so Auth0 emits a locally
verifiable RS256 JWT for the MCP resource. Do not create a custom authorization
server in this repository.

Use Auth0's manual CIMD registration for ChatGPT:

1. In **Dashboard > Settings > Advanced**, enable **Client ID Metadata Document
   (CIMD) Registration**.
2. In the ChatGPT plugin/app management page, copy the exact Client ID Metadata
   Document URL shown for this MCP connection.
3. In **Applications > Applications**, choose **Create Application > Import
   from URL**, paste that CIMD URL, preview it, and create the third-party
   application.
4. In the MCP API's **Settings > Application Access Policy**, choose
   **Per-app authorization** for User-Delegated Access.
5. In the API's **Application Access** tab, edit the imported ChatGPT client and
   grant User-Delegated Access only to `mcp:access`.

Auth0 discovery must publish PKCE `S256` and CIMD support. Configure Google as
the intended social connection and promote it to a domain-level connection so
the imported third-party application can use it. Request `openid email profile
mcp:access`.

Create a separate Auth0 **Regular Web Application** for browser onboarding:

```text
Allowed Callback URL: https://woodhouse.derekjass.com/auth/callback
Allowed Logout URL:   https://woodhouse.derekjass.com/
Allowed Web Origin:   https://woodhouse.derekjass.com
```

Because the API uses **Per-app authorization**, also open the MCP API's
**Application Access** tab, add this Regular Web Application, and grant it
User-Delegated Access to only `mcp:access`. Both the imported ChatGPT client and
the browser-onboarding client need that one permission; no other API permission
is required.

Put its client secret into Secret Manager locally; do not paste it into chat,
Terraform, shell history, or a checked-in file:

```powershell
$secret = Read-Host "Paste Auth0 browser client secret" -MaskInput
$secret | gcloud secrets versions add platform-oidc-client-secret --data-file=-
Remove-Variable secret
```

Then configure Terraform with public identifiers only:

```hcl
enable_platform_oidc          = true
platform_oidc_issuer          = "https://YOUR_TENANT.REGION.auth0.com/"
platform_oidc_resource_url    = "https://woodhouse.derekjass.com/mcp"
platform_oidc_client_id       = "AUTH0_BROWSER_CLIENT_ID"
platform_oidc_redirect_uri    = "https://woodhouse.derekjass.com/auth/callback"
enable_mcp_external_access    = true
```

The gateway exposes both
`/.well-known/oauth-protected-resource` and the path-aware compatibility URL
`/.well-known/oauth-protected-resource/mcp`. Auth0 remains responsible for its
authorization-server/OIDC discovery, authorization, token, registration, and
UserInfo endpoints. The gateway validates token signature, exact issuer,
audience `https://woodhouse.derekjass.com/mcp`, time claims, subject, and
`mcp:access` before resolving the Firestore allowlist. MCP access-token
authorization uses the immutable issuer/subject and does not forward that
bearer token to UserInfo. The separate browser flow obtains verified email from
its signed ID token only when creating the first allowlist binding.

The old `oidc_audience` Google client setting is retained only for a controlled
migration or direct diagnostic deployment when `enable_platform_oidc=false`.
It is not sufficient for ChatGPT's MCP OAuth contract because its ID token is
audienced to the Google client rather than the MCP resource.

Terraform defaults `enable_mcp_external_access` to `false`. That switch grants
the Cloud Run route to `allUsers` so internet MCP clients can
reach application-level authentication; it does not grant application access.
The gateway returns `401` unless the bearer token is valid and resolves to an
active immutable allowlist binding. Terraform refuses to enable the route unless
either platform OIDC or the legacy diagnostic audience is configured. The
process fails closed unless it has `GOOGLE_CLOUD_PROJECT` and one of those two
authentication configurations.

Public smoke checks use `/health`, which contains no identity
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

- `tesla_oauth_states`: hashed, single-use, ten-minute callback bindings with
  asynchronous Firestore TTL cleanup on `expires_at`;
- `tesla_connections`: one encrypted rotating token state per platform user;
- `vehicles`: safe vehicle metadata and per-vehicle key status;
- `vehicle_vin_index`: collision-safe VIN-to-owner mapping used to reject
  cross-user ownership conflicts.

Cloud Run emits a platform request log containing the full URL before gateway
code can strip query parameters. Terraform therefore excludes the gateway's
Tesla `/oauth/callback?...` and platform `/auth/callback?...` request-log entries
from Cloud Logging. Application access logs retain only the query-free callback
paths. The exclusion is prospective and does not remove any entry retained
before it was applied.

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
`homer@example.com` claim and atomically binds its configured issuer/subject. Later
email changes do not change authorization. Do not edit `oidc_issuer` or
`oidc_subject` to transfer an account; disable and follow a reviewed recovery
procedure instead.

After deployment, Homer visits:

```text
https://woodhouse.derekjass.com/onboarding
```

The page starts Google sign-in through Auth0, enforces the invitation, starts
Tesla OAuth, lists all returned vehicles, and offers a separate Virtual Key link
and status refresh for each vehicle. Tesla pairing still completes in the Tesla
mobile app.

For the one-time transition of an account already bound directly to Google's
issuer, confirm the existing opaque `user_id` from `add-user` output and run:

```bash
uv run python scripts/admin/reset-user-identity \
  --project-id woodhouse-506215 \
  --email homer@example.com \
  --confirm-user-id usr_REPLACE_WITH_EXISTING_ID
```

This deletes only the old identity index and clears the binding. It preserves
the internal user, dataset, Tesla connection, vehicle ownership, and history.
Run it only immediately before that same approved user signs in through Auth0.

To block access without deleting history, dataset ACLs, or the immutable binding:

```bash
uv run python scripts/admin/disable-user \
  --project-id woodhouse-506215 \
  --email homer@example.com
```

`disable-user` is idempotent. It does not revoke Google itself, delete BigQuery
data, alter Tesla consent, or remove a future vehicle Virtual Key.

## Phase 6.1 ChatGPT connection checkpoint

After applying Terraform and deploying the reviewed gateway image:

1. Verify `https://woodhouse.derekjass.com/.well-known/oauth-protected-resource`
   returns the exact MCP resource, Auth0 issuer, and `mcp:access` scope.
2. Open `https://woodhouse.derekjass.com/onboarding`, sign in with an allowlisted
   Google account, authorize Tesla, and verify every expected vehicle appears.
3. Open each pending pairing link, complete the Tesla-app action, and use
   **Refresh pairing status**. A partially paired account remains partially
   paired; statuses are per vehicle.
4. In ChatGPT enable developer mode, open Plugins, add the public MCP URL
   `https://woodhouse.derekjass.com/mcp`, and choose OAuth with CIMD.
5. Import the exact ChatGPT Client ID Metadata Document URL into Auth0 and grant
   that imported client User-Delegated Access to only `mcp:access`, as described
   above. The CIMD supplies ChatGPT's exact redirect URI; do not reuse the
   browser-onboarding application's client secret. Never enter a Tesla token,
   Auth0 client secret, or EC private key into ChatGPT.
6. Refresh the plugin metadata, verify all typed tools declare `mcp:access`, and
   run `tesla_list_vehicles` before any write. Continue to require explicit
   current-turn intent for security-sensitive operations.

Current OpenAI references:

- <https://developers.openai.com/plugins/build/auth>
- <https://developers.openai.com/plugins/deploy/connect-chatgpt>

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

For an operator-initiated deployment, submit source through the dedicated
`${project_id}-tpp-cloudbuild-source` bucket by passing
`--gcs-source-staging-dir=gs://${project_id}-tpp-cloudbuild-source/source` to
`gcloud builds submit`. The deployer has read access only to this short-lived
bucket, avoiding project-wide Storage Object Viewer access and, critically,
access to the Terraform state bucket. Source objects expire after seven days.

Terraform apply remains an explicit reviewed operation from merged `main`. Automating it requires a separately reviewed apply identity/approval gate; the application deployer intentionally cannot change IAM, networks, secrets, Firestore, BigQuery, Pub/Sub, or Compute Engine.

Telemetry-edge delivery is deferred to Phase 7, when the VM will pull an exact image digest, health-check it, and support rollback. Production must never identify a service image as `latest`.

## Secret handling

Terraform creates these empty containers:

- `mcp-auth-signing-key`
- `tesla-client-secret`
- `tesla-command-private-key`
- `tesla-command-proxy-tls-cert`
- `tesla-command-proxy-tls-key`
- `tesla-command-public-key`
- `tesla-token-encryption-key`
- `webhook-hmac-key`

No secret version, key material, token, PIN, service-account key, or example
value is committed. Operators add values out of band and inject only the
minimum runtime references.

## Phase 6 Vehicle Command Proxy deployment

The supported private model is Tesla's official `tesla/vehicle-command` image
as a sidecar in the `mcp-gateway` Cloud Run revision. It binds port `4443` on
the revision's shared container network so Cloud Run can perform its required
startup probe, but it has no Cloud Run ingress container port. External service
traffic is routed only to the `application` container on port `8080`; the
gateway reaches the proxy through `https://localhost:4443`. The application
trusts only the mounted proxy certificate. Commands are never retried.

Before planning the enabled revision:

1. Apply the merged Terraform once with `enable_tesla_command_proxy = false`.
   This creates the two empty TLS secret containers without mounting them or
   granting access. Confirm the plan has no destroy actions.

2. Resolve and review the current official image, then record its full digest;
   never use `latest` as the deployed identity:

   ```powershell
   docker buildx imagetools inspect tesla/vehicle-command:latest
   ```

   Set `tesla_command_proxy_image` locally to
   `tesla/vehicle-command@sha256:<reviewed-64-hex-digest>` and set
   `enable_tesla_command_proxy = true`. Neither value is secret.

3. Generate a dedicated loopback TLS certificate outside the repository. This
   key is separate from Tesla's application EC command-signing key:

   ```powershell
   $proxyDir = Join-Path $env:TEMP "tpp-command-proxy-tls"
   New-Item -ItemType Directory -Force $proxyDir | Out-Null
   openssl req -x509 -newkey rsa:3072 -sha256 -days 365 -nodes `
     -subj "/CN=localhost" `
     -addext "subjectAltName=DNS:localhost,IP:127.0.0.1" `
     -keyout (Join-Path $proxyDir "tls.key") `
     -out (Join-Path $proxyDir "tls.crt")
   ```

4. Add the two TLS values through local `gcloud`; do not paste them into chat,
   Terraform, tfvars, build substitutions, or Git:

   ```powershell
   gcloud secrets versions add tesla-command-proxy-tls-cert --project woodhouse-506215 --data-file (Join-Path $proxyDir "tls.crt")
   gcloud secrets versions add tesla-command-proxy-tls-key --project woodhouse-506215 --data-file (Join-Path $proxyDir "tls.key")
   ```

   The existing `tesla-command-private-key` version remains the application EC
   private key paired in Phase 4. Do not reuse either TLS key as that EC key.

5. Plan/apply the Terraform change from merged `main` with the proxy enabled,
   then deploy the gateway
   application image by immutable commit SHA. A multi-container update must
   target the named `application` container (for example, `gcloud run services
   update mcp-gateway --container application --image <sha-image> ...`) so it
   does not replace the proxy sidecar. Confirm the revision has two containers
   and becomes healthy before directing MCP traffic to it.

6. Existing Tesla connections predate the concrete `user_data` account-read
   tools. Re-run the normal `/tesla/oauth/start` flow once and approve the newly
   requested `user_data` scope before testing `tesla_me`,
   `tesla_feature_config`, or `tesla_orders`. Vehicle tools continue to enforce
   their narrower stored scopes.

The full first-live-read, deliberate ambiguity, low-risk command, and Firestore
audit verification is in [the MCP tool catalog](mcp-tool-catalog.md#first-live-mcp-operator-checkpoint).
