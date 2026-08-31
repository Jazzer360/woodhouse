# Deployment

**Status:** Phase 7 implementation complete; operator checkpoint pending. The gateway retains the Google
OIDC/allowlist and per-user Tesla OAuth boundaries, exposes the coverage-matrix
MCP surface, and routes signed commands through an instance-local official Tesla
Vehicle Command Proxy sidecar. The official telemetry receiver and permanent
raw-history path are ready to deploy, while broad real-vehicle Fleet Telemetry
configuration remains deferred to Phase 8.

## Fixed deployment choices

- GCP project: `woodhouse-506215`
- primary region: `us-central1`
- telemetry VM zone: `us-central1-a`
- implementation runtime: Python 3.12 with a `uv` workspace
- shared infrastructure: Terraform with a GCS backend

Python remains the smallest practical common stack for the MCP, GCP, and
analytics components. Telemetry-edge is Tesla's official `v0.9.4` receiver
image, pinned to
`sha256:28c8b9e244b842a3d7443567cfa385b4db20cf533b8dee3411ce6fe540eb67e2`.
Its native Pub/Sub dispatcher is used directly; no custom Tesla protocol
adapter or second application-language codebase is maintained.

## Resource map

| Resource | Terraform identity | Current behavior |
|---|---|---|
| Artifact Registry | `tesla-personal-platform` | New immutable-tag Docker repository |
| Build source staging | GCS `${project_id}-tpp-cloudbuild-source` | Private source objects expire after seven days; deployer receives bucket-scoped read access only |
| MCP gateway | Cloud Run `mcp-gateway` | Health, authenticated stateless Streamable HTTP `/mcp`, Tesla onboarding routes, live Fleet reads, and the public Tesla application-key path |
| Vehicle Command Proxy | `mcp-gateway` Cloud Run sidecar | Official digest-pinned image; loopback TLS only; signs typed vehicle commands |
| Telemetry processor | Cloud Run `telemetry-processor` | Authenticated Pub/Sub push; trusted VIN routing; append-only per-user BigQuery writes |
| Telemetry edge | Compute Engine `tpp-telemetry-edge` | Shielded COS `e2-micro`; exact-digest official receiver; local health/Prometheus endpoints; automatic rollback |
| Certificate renewer | Cloud Run Job `tpp-telemetry-cert-renewer` | Daily DNS-01 check; atomic Secret Manager release; edge reload and public fingerprint verification |
| Telemetry address | Regional static external IPv4 | DNS target for `telemetry.woodhouse.derekjass.com` |
| Raw transport | `tpp-raw-telemetry_{V,alerts,connectivity,errors}` | Official receiver topics, 31-day retention, authenticated processor push |
| Synthetic transport | `tpp-raw-telemetry` | Operator-only non-vehicle Phase 7 verification; edge cannot publish |
| Mutable state | Firestore Native `(default)` | Allowlist and atomic immutable OIDC identity bindings; regional database with delete protection |
| Secret storage | Eleven Secret Manager containers | Terraform manages containers/IAM only; operators add secret versions out of band |
| Quarantine | `tesla_system_quarantine.raw_unknown_telemetry` | Restricted, partitioned append destination for unmapped/invalid telemetry |
| Synthetic evidence | `tesla_system_quarantine.raw_synthetic_telemetry` | Restricted non-vehicle duplicate/retry path evidence |
| Monitoring | backlog alerts, unknown-vehicle and missing-config-provenance log metrics/alerts, and OAuth callback request-log exclusion | No notification destination unless existing channel IDs are supplied; callback query URLs are excluded from Cloud Logging |
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

Terraform derives the telemetry receiver's runtime JSON from the checked-in
base configuration, injects the active `project_id`, and passes it through
trusted instance metadata. The VM writes and mounts that generated file
read-only. The digest-pinned container therefore cannot retain a hard-coded
Pub/Sub project that differs from the infrastructure receiving its records.
The startup script uses the ordinary Compute Engine `startup-script` metadata
key so an existing deletion-protected edge VM can receive the configuration
in place; the exact-digest delivery reset executes it before health polling.

The `tpp-user-admin` service account is keyless and used only through operator
impersonation. It can write Firestore allowlist entities, create BigQuery
datasets, and update dataset metadata/ACLs through a custom role containing only
`bigquery.datasets.create`, `bigquery.datasets.get`,
`bigquery.datasets.update`, `bigquery.tables.create`, `bigquery.tables.get`,
and `bigquery.tables.update`. It cannot run BigQuery jobs, delete tables, read
table data under its steady-state project/dataset IAM, access Secret Manager,
deploy Cloud Run, or call vehicle APIs. BigQuery nevertheless requires
`bigquery.tables.getData` on referenced tables while it validates a logical-view
definition. The Phase 9 `add-user` transaction therefore adds a dataset-scoped
`READER` entry for this identity immediately before validating the managed
views and removes it in `finally`, whether validation succeeds or fails. The
identity still has no BigQuery job role. A process-kill between grant and
cleanup is detectable ACL drift; re-running `add-user` removes it before doing
any other repair and again on normal completion.

The keyless `tpp-dataset-owner` service account has no project-level roles, keys,
or operator impersonation binding. BigQuery requires every dataset policy to
retain a direct owner, so each per-user dataset grants that otherwise dormant
identity `OWNER`. Keeping this mandatory data-capable entry separate prevents
the impersonatable `tpp-user-admin` path from retaining telemetry access after
the narrow view-validation transaction.

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

Tesla's current Fleet Telemetry overview requires a publicly reachable server
but does not publish stable source CIDRs that can safely replace `0.0.0.0/0`.
Port `443` is fixed across the receiver, firewall, certificate validation, and
future vehicle configuration. The rule targets only the telemetry-edge service
account and that single TCP port. If Tesla later publishes an authoritative
sender range, restrict the rule in the same reviewed change that verifies
receiver delivery.

Firewall logging remains enabled for IAP administration but is disabled on the public Fleet Telemetry allow rule. Unauthenticated internet scanning would otherwise create unbounded log volume and cost; Phase 7 receiver health, application logs, and metrics provide useful operational visibility once a listener exists.

Project SSH keys are blocked, OS Login is required, and no direct public SSH
rule exists. The VM service account can inspect and publish only the four
official receiver topics, pull only from the platform Artifact Registry
repository, read only the telemetry TLS certificate, key, and atomic release
manifest after delivery is enabled,
and write logs/metrics. It has no Tesla OAuth, command-key, Firestore, BigQuery,
or synthetic-topic access.

[Cloud Run recognizes same-project Pub/Sub subscriptions as an allowed source for internal ingress](https://cloud.google.com/run/docs/securing/ingress#available_network_ingress_settings), so the telemetry processor does not need public ingress for push delivery. [Compute Engine recommends the `cloud-platform` OAuth scope with access controlled through IAM roles](https://cloud.google.com/compute/docs/access/service-accounts#authorization); that scope is used on the VM, with effective authorization restricted by the service account's narrow IAM roles. Legacy granular OAuth scopes do not grant permissions and do not cover every authentication protocol.

The Pub/Sub push subscription delivers to the complete generated Cloud Run
handler URL, including `/pubsub/push`. Its token uses the stable path-scoped
custom audience
`https://telemetry-processor.woodhouse.derekjass.com/pubsub/push`, which Cloud
Run explicitly accepts and the application verifies exactly along with the
`tpp-pubsub-push` email and `email_verified` claim. This avoids accepting a
broader service-root token while keeping Terraform free of a Cloud Run
self-reference cycle.

## Service accounts and IAM intent

| Identity | Granted access |
|---|---|
| `tpp-mcp-gateway` | Firestore user; BigQuery job user; runtime secret access for onboarding plus command/proxy key material mounted only into the command-proxy sidecar |
| `tpp-telemetry-processor` | Firestore user; writer on the quarantine/system dataset and each user dataset through dataset ACLs |
| `tpp-telemetry-edge` | Four-topic publisher/inspector; platform-repository reader; gated TLS-secret reader; log and metric writer |
| `tpp-cert-renewer` | Read narrow Cloudflare/ACME/release secrets; add TLS/state/release versions; reset and inspect only `tpp-telemetry-edge` |
| `tpp-cert-scheduler` | Invoke only the certificate-renewal Cloud Run Job |
| `tpp-pubsub-push` | Invoker on telemetry-processor only |
| `tpp-build-validator` | Log writer only; no deploy, secret, or data permission |
| `tpp-build-deployer` | Artifact Registry writer, Cloud Run developer, `actAs` on Cloud Run runtimes, and—when enabled—`actAs` plus metadata/reset/health access only for `tpp-telemetry-edge` |
| `tpp-analytics-view-reconciler` | Log writer plus active-allowlist read and managed BigQuery view metadata reconciliation; no query jobs, steady-state table-data access, dataset creation, raw-table deletion, secrets, or vehicle access |
| `tpp-user-admin` | Firestore user; BigQuery dataset/raw-table/view metadata updater; no steady-state table-data or query-job access; temporary per-dataset read only while BigQuery validates managed view definitions |
| `tpp-partner-admin` | Secret accessor only for Tesla client-secret and public-key containers; no project role or runtime impersonation |
| `tpp-dataset-owner` | Required direct owner on per-user datasets; no project roles, keys, or impersonation binding |

The Cloud Build service agent may mint tokens only for the validator, deployer,
and analytics-view reconciler build identities. None receives Owner, Editor, or
Secret Manager access. The reconciler receives temporary dataset read only
while BigQuery validates view SQL and restores the exact prior ACL afterward.

Members in `user_admin_principals` receive only
`roles/iam.serviceAccountTokenCreator` on `tpp-user-admin`, allowing keyless ADC
impersonation for the manual workflow. They do not receive that service account's
permissions directly and no service-account key is created.

[Cloud Run uses its service agent to access deployed container images](https://cloud.google.com/run/docs/securing/service-identity#service-agent), and same-project Artifact Registry access needs no additional runtime-account grant. The `tpp-mcp-gateway` and `tpp-telemetry-processor` runtime identities therefore do not receive `roles/artifactregistry.reader`; adding it would expose repository contents to application code without helping Cloud Run pull an image. Cross-project repositories would instead require an explicit reader grant to the Cloud Run service agent.

### Phase 3 per-user dataset contract

The idempotent `add-user` workflow—not shared Terraform—will create `tesla_u_<opaque_user_id>` with no default expiration and grant:

- `OWNER` on that dataset to the dormant, non-impersonatable `tpp-dataset-owner` identity, as required by BigQuery;
- `roles/bigquery.dataEditor` on that dataset to `tpp-telemetry-processor`;
- `roles/bigquery.dataViewer` on that dataset to `tpp-mcp-gateway`;
- `roles/bigquery.dataViewer` on that dataset to the approved user's normalized
  invitation email, for direct inspection of only their own history.

It also creates `raw_telemetry_events`, partitions it daily by
`source_timestamp`, clusters it by `vehicle_id, record_type`, and configures no
table expiration. Phase 9 additionally creates or updates the complete
dependency-ordered logical-view set defined by the analytics package. New users
receive that set through `add-user`; existing active users are kept synchronized
by the main-merge analytics-view trigger.

The four ACL entries above are the permanent contract. During managed view
creation/update only, `add-user` temporarily adds `tpp-user-admin` as `READER`
because BigQuery refuses to validate a view unless its creator has
`tables.getData` on each referenced object. A `finally` cleanup restores the
four-entry contract on success or failure. This narrowly scoped, short-lived
grant is an implementation requirement of BigQuery view validation, not a
runtime analytics permission.

The merge reconciler uses the same preflight/create/update implementation as
`add-user` but preserves the exact existing permanent dataset ACL rather than
re-authoring it. It first creates the complete candidate dependency graph as
one-hour `tpp_preflight_*` shadow views, deletes those exact shadows after
BigQuery validates them, and changes no canonical view if any shadow fails.
During canonical promotion it snapshots existing metadata and reverses prior
updates/creates in dependency-safe order if a later operation fails. After
every desired canonical view has been promoted, only the merge reconciler
removes stale `VIEW` objects carrying the full
Woodhouse application/data-class/layer/manager labels. Raw tables and unmanaged
objects are outside its deletion policy. One user's failure fails the build
with a tenant-neutral error containing only the failing canonical view name;
successful earlier datasets remain safe and the next idempotent run retries the
full active-user set.

The gateway already has project-level `roles/bigquery.jobUser` so it can run
scoped queries. The operator principal also has that project-level job role, so
its per-user dataset reader grant supports console queries. `add-user` does not
grant a project job role to every approved user. No caller supplies a dataset
ID, and no shared project-level data-reader/writer role is granted.

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
server in this repository. Keep the access-token lifetime short (the Auth0
default is acceptable); connection persistence comes from refresh tokens, not
from a long-lived bearer access token.

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

Treat the CIMD URL as the OAuth client identity, not merely as an import source.
ChatGPT's current stable identity is `https://chatgpt.com/oauth/client.json`
when the authorization server supports issuer identification; older links may
instead have a callback-specific URL. If the URL currently shown by ChatGPT is
not an Auth0 client, import it before reconnecting. A legacy callback-specific
client and the stable client are distinct registrations, and refresh-token
settings on one do not change grants issued to the other.

Configure refresh-token persistence for that connection:

1. In the MCP API's **Settings**, enable **Allow Offline Access**.
2. In the imported ChatGPT third-party application's **Advanced Settings >
   Grant Types**, confirm both **Authorization Code** and **Refresh Token** are
   enabled. A CIMD import should create both; do not assume configuration drift
   has preserved them.
3. In that application's refresh-token settings, enable rotation and automatic
   reuse detection. Use expiring tokens with:

   ```text
   Idle refresh-token lifetime:     2,591,998 seconds (just under 30 days)
   Maximum refresh-token lifetime: 31,557,600 seconds (one year)
   Rotation overlap/leeway:         3 seconds
   ```

   The current Auth0 configuration requires the idle lifetime to be less than
   `2,591,999` seconds, so `2,591,998` is the highest accepted integer. Auth0
   third-party applications require expiring refresh tokens, and one year is
   Auth0's supported maximum. The idle window moves after each successful
   refresh; the maximum lifetime does not. Normal use at least once per idle
   window can therefore keep the connection usable until the one-year maximum.
4. Do not add `offline_access` to Woodhouse's protected-resource metadata or
   gateway-required scopes. It is an authorization-server request for a
   refresh token, not a Woodhouse API permission. Auth0 publishes it through
   authorization-server discovery, and the ChatGPT OAuth client advertises the
   `refresh_token` grant.
5. After saving these settings, remove the existing Woodhouse connection from
   ChatGPT and connect it once more. Previously issued authorization grants do
   not retroactively acquire a refresh token or a longer refresh-token family
   lifetime.

Auth0 discovery must publish PKCE `S256` and CIMD support. Configure Google as
the intended social connection and promote it to a domain-level connection so
the imported third-party application can use it. ChatGPT should request
`openid email profile offline_access mcp:access`; browser onboarding requests
only `openid email profile mcp:access` because its opaque Woodhouse session is a
separate, deliberately bounded credential.

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

## Tesla API call visibility

The gateway writes a structured `tesla_api_call` event before and after every
actual HTTPS attempt made by the production Tesla transports. This includes
Fleet API reads/writes, OAuth token exchanges and refreshes, partner calls,
safe-read retries, command wake preflight/polling, and calls to the instance-local
Vehicle Command Proxy. In Logs Explorer, start with:

```text
resource.type="cloud_run_revision"
resource.labels.service_name="mcp-gateway"
jsonPayload.event="tesla_api_call"
```

Trace everything associated with one MCP result or command audit:

```text
resource.type="cloud_run_revision"
resource.labels.service_name="mcp-gateway"
jsonPayload.event="tesla_api_call"
jsonPayload.correlation_id="corr_REPLACE_ME"
```

Show non-successful terminal events:

```text
resource.type="cloud_run_revision"
resource.labels.service_name="mcp-gateway"
jsonPayload.event="tesla_api_call"
(jsonPayload.phase="failed" OR jsonPayload.outcome!="success")
```

Use `call_id` to pair each `started` event with its `completed` or `failed`
event. `flow_phase` distinguishes `read`, `command_preflight`, `automatic_wake`,
`wake_poll`, and `command`. `destination=vehicle_command_proxy` proves the
gateway reached the local signer; only its successful downstream behavior proves
that the proxy reached Tesla. Logs intentionally expose route templates and
request/query field names, never raw URLs, query values, headers, bodies,
credentials, VINs, exact locations, or other sensitive payloads. Firestore
`tesla_command_audits` remains the durable write audit rather than Cloud Logging.
The `tesla-register-partner` operator command emits the same JSON events to
standard error for its Tesla OAuth and Partner API attempts.

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
write runtime ACLs plus approved-user read access to only that user's dataset.
While creating/updating Phase 9 views it briefly grants its own keyless identity
dataset read solely for BigQuery validation, removes that entry in `finally`, then
marks the invitation active. Re-running the command reuses the
same identifiers and repairs drift. If dataset provisioning fails for a new
invitation, the record remains disabled and a safe retry completes it.

The approved-user reader entry is intentionally authoritative: manual grants
not represented by this workflow are removed as ACL drift on the next repair.
The operator's Terraform-managed project-level `roles/bigquery.jobUser` grant,
together with this dataset-level reader entry, permits BigQuery console
inspection and read-only queries without granting access to another user's
dataset.

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
6. Enable the documented Auth0 offline-access and rotating refresh-token policy,
   then unlink and relink the ChatGPT connection once so the new grant contains
   a refresh token. Keep `offline_access` out of Woodhouse tool permissions.
7. Refresh the plugin metadata, verify the SDK protects all 13 semantic tools
   with `mcp:access`, and run `get_tesla_account(action="list_vehicles")` before any
   write. Continue to require explicit current-turn intent for
   security-sensitive operations.

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

The interactive GitHub App authorization and regional Cloud Build v2 repository
connection are one-time external bootstrap concerns. Set the resulting full
repository resource name in the ignored `terraform.tfvars` as
`cloud_build_repository`. Terraform then owns all application triggers so their event
filters, build identities, and configuration paths cannot drift. The regional
`tpp-pr-validation` trigger uses `tpp-build-validator` and `cloudbuild.pr.yaml`
for pull requests targeting `main`.

Application push triggers use `tpp-build-deployer` and the shared
`cloudbuild.main.yaml` delivery contract:

- `tpp-main-mcp-gateway` selects gateway, package, workspace-lock, and delivery
  configuration changes and substitutes `_SERVICE=mcp-gateway`;
- `tpp-main-telemetry-processor` selects processor, package, workspace-lock,
  and delivery configuration changes and substitutes
  `_SERVICE=telemetry-processor`;
- `tpp-main-telemetry-edge`, enabled only after the TLS operator prerequisites,
  selects official receiver/config changes and substitutes
  `_SERVICE=telemetry-edge` plus the exact VM zone.

The separate `tpp-main-analytics-views` push trigger selects analytics, auth,
and reconciliation-script changes. It runs `cloudbuild.analytics-views.yaml`
as `tpp-analytics-view-reconciler`, lists active tenant mappings from Firestore,
and reconciles each private dataset from the merged source definitions. It does
not build/deploy an application image and cannot mutate raw telemetry or Tesla
configuration. Creating this identity, custom role, and trigger requires one
reviewed Terraform apply; after that bootstrap, view updates are merge-driven.

The trigger is not retroactive. Immediately after the first Terraform apply
creates it, run the already-merged `main` revision once through the trigger:

```bash
gcloud builds triggers run tpp-main-analytics-views \
  --project=woodhouse-506215 \
  --region=us-central1 \
  --branch=main
```

Require a successful build. The reconciler's final postcondition re-lists each
dataset and fails unless its Woodhouse-managed analytics view names exactly
match the source definition set; create/update/delete API failures also fail the
build. Unmanaged views and all non-view objects are ignored by that comparison.
Before canonical updates, dependency-rewritten one-hour shadow views validate
the complete candidate graph in the same dataset and are deleted in reverse
dependency order. Logical-view create/update calls use a 120-second validation
deadline because BigQuery performs a dry-run-style semantic planning pass
before accepting the metadata change. This does not run or bill the historical
query. Planner errors such as unsupported correlated table subqueries must be
fixed in the checked-in SQL; the longer deadline exists so Cloud Build reports
the real diagnostic.

The initial adoption of an existing environment is an import, not a recreate.
After this configuration is merged, import the three existing regional triggers
before the first authoritative plan:

```bash
terraform -chdir=infra/terraform import -var-file=terraform.tfvars \
  'google_cloudbuild_trigger.pr_validation[0]' \
  "$(gcloud builds triggers describe tpp-pr-validation --project=woodhouse-506215 --region=us-central1 --format='value(resourceName)')"

terraform -chdir=infra/terraform import -var-file=terraform.tfvars \
  'google_cloudbuild_trigger.main_mcp_gateway[0]' \
  "$(gcloud builds triggers describe tpp-main-mcp-gateway --project=woodhouse-506215 --region=us-central1 --format='value(resourceName)')"

terraform -chdir=infra/terraform import -var-file=terraform.tfvars \
  'google_cloudbuild_trigger.main_telemetry_processor[0]' \
  "$(gcloud builds triggers describe tpp-main-telemetry-processor --project=woodhouse-506215 --region=us-central1 --format='value(resourceName)')"
```

Review the following saved plan and require zero trigger replacements. The
GitHub connection itself remains external because creating it requires an
interactive authorization grant; its trigger consumers do not.

The delivery build uses the repository root as Docker context, tags and pushes
the affected service image with the full merge commit SHA, resolves the
Artifact Registry digest, and deploys that digest. Gateway delivery updates only
the named `application` container so it cannot replace the Tesla Vehicle
Command Proxy sidecar. The build waits for the latest Cloud Run revision to be
ready, verifies the revision image digest, runs the gateway's public `/health`
smoke check, and records service, commit, revision, and digest in Cloud Build
logs. The telemetry processor has internal-only ingress, so its automated
checkpoint is Cloud Run revision readiness and digest identity; Phase 7 adds a
real synthetic Pub/Sub ingestion check without exposing an unauthenticated
health route.

For telemetry-edge, the build resolves the commit-tagged image to a digest,
writes only that digest and the 40-character commit to VM metadata, and resets
the VM. The startup script validates both values, resolves the TLS release
manifest to exact certificate/key versions, validates their hashes, hostname,
chain, expiry, and key match in a staging directory, then activates them as a
pair. It pulls from Artifact Registry, starts the receiver with a read-only
filesystem and dropped capabilities, and polls the receiver's local `/status`.
A failed TLS pair is rolled back before the previously healthy image fallback.
Cloud Build reads a guest attribute and succeeds only for the requested commit.
Terraform ignores only the two
delivery-owned metadata keys so an infrastructure apply cannot roll back the
application image.

For an operator-initiated deployment, submit source through the dedicated
`${project_id}-tpp-cloudbuild-source` bucket by passing
`--gcs-source-staging-dir=gs://${project_id}-tpp-cloudbuild-source/source` to
`gcloud builds submit`. The deployer has read access only to this short-lived
bucket, avoiding project-wide Storage Object Viewer access and, critically,
access to the Terraform state bucket. Source objects expire after seven days.

Terraform apply remains an explicit reviewed operation from merged `main`. Automating it requires a separately reviewed apply identity/approval gate; the application deployer intentionally cannot change IAM, networks, secrets, Firestore, BigQuery, Pub/Sub, or Compute Engine.

Production must never identify a service image as `latest`.

## Phase 7 operator checkpoint

Stop here until the Phase 7 PR is merged. Do not call Tesla's
`fleet_telemetry_config` endpoint during this checkpoint.

### 1. Apply the durable path with edge delivery disabled

From an up-to-date `main`, confirm the ignored `terraform.tfvars` contains the
real operator in `admin_principals` and keeps:

```hcl
telemetry_hostname             = "telemetry.woodhouse.derekjass.com"
enable_telemetry_edge_delivery = false
```

Run and review an authoritative plan, then apply it:

```bash
terraform -chdir=infra/terraform plan -var-file=terraform.tfvars -out=phase7.tfplan
terraform -chdir=infra/terraform apply phase7.tfplan
```

The merge-triggered processor image is rollout-safe: before this apply it stays
healthy but returns `503` for all pushes. It cannot acknowledge an observation
until the required authenticated-push and storage configuration exists.

Re-run `add-user` once for every existing approved account to create/repair its
permanent raw table. This is idempotent:

```bash
gcloud auth application-default login \
  --impersonate-service-account=tpp-user-admin@woodhouse-506215.iam.gserviceaccount.com
uv run python scripts/admin/add-user \
  --project-id woodhouse-506215 \
  --email APPROVED_EMAIL
```

### 2. Create and verify public DNS

Create exactly this record in the authoritative DNS zone:

```text
name:  telemetry.woodhouse.derekjass.com
type:  A
value: 34.46.67.52
TTL:   300 (or provider default)
```

Confirm the reserved address still matches before changing DNS:

```bash
terraform -chdir=infra/terraform output -raw telemetry_edge_public_ip
```

It must print `34.46.67.52`. Verify authoritative and public propagation before
requesting a certificate:

```bash
dig +short telemetry.woodhouse.derekjass.com A
dig +short @1.1.1.1 telemetry.woodhouse.derekjass.com A
```

Both must equal the Terraform output. Do not use a proxied/CDN DNS mode; Tesla
must establish mTLS directly with the receiver.

### 3. Issue, validate, and upload the TLS certificate

Use a certificate from a commonly trusted public CA. A manual Let's Encrypt
DNS-01 issuance is suitable for the first checkpoint and avoids exposing an
HTTP challenge port:

```bash
certbot certonly --manual --preferred-challenges dns \
  --agree-tos --no-eff-email \
  --email OPERATOR_EMAIL \
  --domain telemetry.woodhouse.derekjass.com
```

Certbot prints the exact `_acme-challenge.telemetry.woodhouse.derekjass.com`
TXT value. Add it, confirm it with `dig TXT`, then continue Certbot. Validate the
result before upload:

```bash
openssl x509 -in /path/to/fullchain.pem -noout -checkend 2592000
openssl x509 -in /path/to/fullchain.pem -noout -subject -issuer -dates -ext subjectAltName
openssl x509 -in /path/to/fullchain.pem -pubkey -noout \
  | openssl pkey -pubin -outform DER | openssl sha256
openssl pkey -in /path/to/privkey.pem -pubout -outform DER | openssl sha256
```

The two SHA-256 outputs must match, the SAN must contain the exact hostname,
and the certificate must remain valid for at least 30 days. Add versions without
copying PEM material into Git, Terraform, logs, or command arguments:

```bash
gcloud secrets versions add telemetry-edge-tls-cert \
  --project=woodhouse-506215 --data-file=/path/to/fullchain.pem
gcloud secrets versions add telemetry-edge-tls-key \
  --project=woodhouse-506215 --data-file=/path/to/privkey.pem
```

This manual certificate is bootstrap material only. Complete
[the unattended renewal checkpoint](runbooks/telemetry-cert-renewal.md) before
configuring a real vehicle. Google-managed certificates are not used here:
Tesla requires mTLS to terminate in the official receiver, while a managed
certificate attached to a Google TLS/HTTPS proxy is not exportable to that
receiver and would terminate the client-certificate boundary in the wrong
component.

### 4. Enable exact-digest delivery and deploy once

Set `enable_telemetry_edge_delivery = true`, plan, and apply again. This grants
the edge access only to its TLS secrets and creates its exact-digest main
trigger. Then start the first deployment from merged `main`:

```bash
gcloud builds triggers run tpp-main-telemetry-edge \
  --project=woodhouse-506215 \
  --region=us-central1 \
  --branch=main
```

The build must report `deployed_service=telemetry-edge`, the merged commit, and
a `sha256` digest. Verify the VM guest deployment status, local receiver health,
metrics, and service identity:

```bash
gcloud compute instances get-guest-attributes tpp-telemetry-edge \
  --project=woodhouse-506215 --zone=us-central1-a \
  --query-path=telemetry-edge/status
gcloud compute ssh tpp-telemetry-edge \
  --project=woodhouse-506215 --zone=us-central1-a --tunnel-through-iap \
  --command='curl -fsS http://127.0.0.1:8080/status && curl -fsS http://127.0.0.1:9090/metrics | head'
gcloud compute ssh tpp-telemetry-edge \
  --project=woodhouse-506215 --zone=us-central1-a --tunnel-through-iap \
  --command='curl -fsS -H "Metadata-Flavor: Google" http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/email'
```

The identity must be
`tpp-telemetry-edge@woodhouse-506215.iam.gserviceaccount.com`. Validate the
public certificate/chain with Tesla's pinned-version `check_server_cert.sh` and
an independent handshake:

```bash
git clone --depth 1 --branch v0.9.4 \
  https://github.com/teslamotors/fleet-telemetry.git /tmp/fleet-telemetry-v0.9.4
jq -n \
  --arg hostname telemetry.woodhouse.derekjass.com \
  --arg ca "$(cat /path/to/fullchain.pem)" \
  '{hostname:$hostname,port:443,ca:$ca}' >/tmp/validate-telemetry-server.json
/tmp/fleet-telemetry-v0.9.4/tools/check_server_cert.sh \
  /tmp/validate-telemetry-server.json
openssl s_client -connect telemetry.woodhouse.derekjass.com:443 \
  -servername telemetry.woodhouse.derekjass.com -verify_return_error </dev/null
```

No normal HTTP response is expected on public port 443; it is Tesla's mTLS
telemetry protocol endpoint.

### 5. Run the isolated synthetic end-to-end proof

Use the operator identity listed in `admin_principals`. The script can publish
only to the synthetic topic and read only the restricted system dataset:

```bash
gcloud auth application-default login
uv run python scripts/admin/verify-telemetry-pipeline \
  --project-id woodhouse-506215 \
  --confirm-non-vehicle-fixtures
```

Success proves, without fabricating a user or vehicle identity:

- two identical fixture observations remain as two raw rows;
- a deliberate first persistence failure is negatively acknowledged, retried,
  and then durably stored;
- a marked unknown VIN lands in restricted quarantine and never a user dataset;
- the authenticated Pub/Sub-to-Cloud Run-to-BigQuery path is live.

After these checks, report the checkpoint evidence and stop. Phase 8 separately
requires an exact desired/current config diff and explicit approval for each
real vehicle before making any Tesla telemetry configuration call.

## Phase 8 Fleet Telemetry configuration checkpoint

Prepare the versioned CA-only vehicle trust profile from the currently served
TLS chain. This retains issuing CA certificates and deliberately drops the
expiring leaf; it refuses to overwrite an existing output file:

```bash
uv run python scripts/admin/prepare-telemetry-trust-profile \
  --hostname telemetry.woodhouse.derekjass.com \
  --profile-id lets-encrypt-current-2026-08 \
  --output /tmp/woodhouse-telemetry-ca-profile.pem
```

The command prints only the public profile ID, hash, count, hostname, port, and
output path. Inspect the CA subjects and validity, then add the CA-only PEM as a
new secret version. Never upload the leaf or any private key to this secret:

```bash
openssl crl2pkcs7 -nocrl -certfile /tmp/woodhouse-telemetry-ca-profile.pem \
  | openssl pkcs7 -print_certs -noout
gcloud secrets versions add telemetry-server-ca-profile \
  --project=woodhouse-506215 \
  --data-file=/tmp/woodhouse-telemetry-ca-profile.pem
```

The new secret containers are Terraform-owned, so bootstrap them in two safe
applies. First set the trust-profile ID while keeping telemetry control disabled
and the existing certificate schedule paused:

```hcl
enable_fleet_telemetry_control       = false
telemetry_trust_profile_id           = "lets-encrypt-current-2026-08"
telemetry_certificate_schedule_paused = true
```

Review a no-destroy plan and apply it. This creates the empty containers and
updates the renewal job without allowing a scheduled run against empty values.
Then upload the CA profile as shown above.

For the initial profile, no vehicle is configured yet. Create the separate,
non-secret cutover-readiness manifest using the exact SHA-256 printed by the
preparation command, then add it as a secret version:

```bash
printf '%s' \
  '{"ready":true,"required_vehicle_count":0,"trust_profile_id":"lets-encrypt-current-2026-08","trust_profile_sha256":"REPLACE_WITH_PRINTED_SHA256"}' \
  >/tmp/woodhouse-telemetry-trust-readiness.json
gcloud secrets versions add telemetry-trust-readiness \
  --project=woodhouse-506215 \
  --data-file=/tmp/woodhouse-telemetry-trust-readiness.json
```

For a later CA migration, do not publish a matching readiness version until
the canary and every required vehicle have synchronized the overlap/new trust
profile. The renewal job compares both ID and hash and therefore cannot cut the
server over early. Routine leaf renewal under the already-active profile does
not require a new readiness version or any Tesla call.

After both secret versions exist, set these non-secret Terraform values, review
a second no-destroy plan, and apply:

```hcl
enable_fleet_telemetry_control = true
telemetry_trust_profile_id     = "lets-encrypt-current-2026-08"
telemetry_certificate_schedule_paused = true
```

The certificate-renewal job now requires the exact profile and fails closed if
an ACME candidate is not compatible. Run it once manually after the second
apply and confirm the release manifest records the expected trust-profile
ID/hash. Only after that succeeds may a final Terraform apply set
`telemetry_certificate_schedule_paused = false`.

Open `https://woodhouse.derekjass.com/onboarding`, select one paired vehicle,
and click **Inspect telemetry configuration**. Save the complete safe diff and
review the volume notes in
[`fleet-telemetry-configuration.md`](fleet-telemetry-configuration.md). Do not
check Apply until the operator has selected that exact vehicle and approved the
displayed config hash. Apply processes one VIN, waits for `synced=true`, checks
Tesla telemetry errors, and persists trusted hashes only after verification.

After explicit approval and apply, verify one genuine observation without
printing VIN, location, or payload data:

```bash
bq query --use_legacy_sql=false --parameter='vehicle_id:STRING:VEHICLE_ID' '
SELECT
  COUNT(*) AS observation_count,
  COUNTIF(source_timestamp IS NOT NULL) AS with_source_timestamp,
  COUNTIF(ingested_at IS NOT NULL) AS with_ingestion_timestamp,
  ANY_VALUE(telemetry_config_version) AS config_version,
  ANY_VALUE(telemetry_config_hash) AS config_hash
FROM `woodhouse-506215.USER_DATASET.raw_telemetry_events`
WHERE vehicle_id = @vehicle_id
  AND ingested_at >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 30 MINUTE)'
```

Confirm the counts are nonzero, hashes match the synchronized vehicle record,
and both dataset/table expiration remain unset. Until then the first live
vehicle checkpoint is incomplete. CI and main-branch deploys never call Apply
or Remove.

## Secret handling

Terraform creates these empty containers:

- `mcp-auth-signing-key`
- `platform-oidc-client-secret`
- `tesla-client-secret`
- `tesla-command-private-key`
- `tesla-command-proxy-tls-cert`
- `tesla-command-proxy-tls-key`
- `tesla-command-public-key`
- `tesla-token-encryption-key`
- `telemetry-edge-tls-cert`
- `telemetry-edge-tls-key`
- `telemetry-edge-tls-release`
- `telemetry-acme-state`
- `telemetry-server-ca-profile`
- `telemetry-trust-readiness`
- `cloudflare-dns-api-token`
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
   requested `user_data` scope before testing `get_tesla_account(action="me")`,
   `get_tesla_account(action="feature_config")`, or
   `get_tesla_account(action="orders")`. Vehicle tools continue to enforce
   their narrower stored scopes.

The full first-live-read, deliberate ambiguity, low-risk command, and Firestore
audit verification is in [the MCP tool catalog](mcp-tool-catalog.md#first-live-mcp-operator-checkpoint).
