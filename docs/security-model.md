# Security and Access Model

## 1. Goal

Keep access extremely simple while ensuring that an MCP endpoint capable of controlling vehicles and querying precise historical location data is not public.

This is not a public signup system. The owner manually approves users.

---

## 2. Human identity

Use a mainstream OIDC identity provider and reuse the proven MCP authorization
pattern from the existing personal YNAB integration where practical. Google is
the upstream login identity. Auth0 is the default OAuth 2.1 authorization
server because ChatGPT requires MCP protected-resource discovery, PKCE S256,
resource-specific access tokens, and supported client registration. ChatGPT is
registered as an Auth0 third-party client through its exact Client ID Metadata
Document URL and receives only the user-delegated `mcp:access` permission. The
platform does not implement a custom authorization server.

The platform stores:

```text
email                 # invitation/bootstrap convenience
email_verified        # must be true at first binding
oidc_issuer            # immutable provider namespace
oidc_subject           # immutable identity inside that issuer
internal user_id       # opaque platform identifier
status                 # active/disabled
```

Authorization after initial binding is based on `(issuer, subject)`, not the email string alone.

---

## 3. Firestore allowlist

Simple initial structure:

```text
allowed_users/{normalized_email}
  user_id
  dataset_id
  status
  oidc_issuer
  oidc_subject
  provisioning_state
  created_at
  notes
```

The implementation also maintains a non-enumerable-by-email identity index:

```text
oidc_identities/{sha256(issuer + NUL + subject)}
  allowlist_email
  user_id
  oidc_issuer
  oidc_subject
```

The first successful login creates the index record and updates the allowlist
record in one Firestore transaction. Later requests resolve this index first and
do not use the token's current email for authorization. The index hash is a
deterministic document key, not a password or credential.

Manual admin workflow:

```text
uv run python scripts/admin/add-user --project-id woodhouse-506215 --email homer@example.com
uv run python scripts/admin/disable-user --project-id woodhouse-506215 --email homer@example.com
```

`add-user` should be idempotent and may also create the user's BigQuery dataset.

The implemented workflow allocates a random `usr_<opaque>` internal ID and a
separate random `tesla_u_<opaque>` dataset ID once. A new record remains disabled
while its dataset is being provisioned, then becomes active only after the
non-expiring dataset and runtime ACLs are confirmed. Re-running `add-user` keeps
the identifiers and immutable OIDC binding, repairs the dataset/ACLs, and
reactivates the record. `disable-user` changes only access status.

No public enrollment UI is required.

## 3.1 Gateway token boundary

Phase 6.1 uses Auth0 as the standards-compliant OAuth authorization server with
Google configured as its upstream social/OIDC connection. The gateway verifies
the access-token signature through the issuer JWKS, exact issuer, MCP resource
audience, expiry/not-before claims, subject, and `mcp:access` scope. MCP access
tokens authorize an existing immutable issuer/subject binding without requiring
an email claim or forwarding the bearer token to UserInfo. The separate browser
flow obtains verified email from its signed ID token only for the first
allowlist bind. Direct Google ID-token verification is retained temporarily as
a migration/diagnostic path when platform OIDC is not enabled; it is not the
ChatGPT connector contract.

The bearer token is accepted only in the HTTP `Authorization` header. Every
protected request derives `user_id` and `dataset_id` from Firestore. JSON fields
that attempt to select a user, dataset, or ownership claim are rejected even
when nested. Resource ownership checks compare the derived context with an owner
ID read from trusted server storage; a caller's ownership statement is never an
authorization input.

The HTTP boundary limits request size and JSON nesting, applies an idle socket
timeout, and enforces one absolute deadline for reading each request body. A
caller cannot keep a worker occupied indefinitely by stalling or trickling a
declared request body.

The gateway publishes `/.well-known/oauth-protected-resource`, advertises OAuth
on every MCP tool, and returns both HTTP `WWW-Authenticate` challenges and MCP
`_meta["mcp/www_authenticate"]` errors. ChatGPT performs authorization code +
PKCE against Auth0 and sends only the resulting MCP access token to `/mcp`.

Browser onboarding uses a separate Auth0 regular-web-app client with
authorization code + PKCE and nonce/state validation. Its client secret is
injected from Secret Manager. A short-lived `Secure; HttpOnly; SameSite=Lax`
pre-authentication cookie binds OAuth state to the browser that initiated login
and is cleared at callback. The browser then receives only an opaque, random
session cookie with the same protections. Login state and sessions live
server-side in Firestore, enforce expiry at request time, and have asynchronous
Firestore TTL cleanup. Every session use re-resolves the immutable identity and
active allowlist status, so `disable-user` takes effect immediately.
State-changing onboarding forms require a session-bound CSRF token.

No public signup is introduced. Auth0 authentication does not bypass the
Firestore invitation. Existing records bound directly to Google require the
guarded `reset-user-identity` operator workflow once before binding to the new
Auth0 issuer/subject; the command preserves `user_id`, dataset, Tesla ownership,
and history.

---

## 4. Tesla account authorization

Each approved platform user authorizes the single developer application against their own Tesla account through Tesla's authorization-code OAuth flow.

Per-user state includes:

- Tesla OAuth subject/account metadata;
- region/base URL;
- current refresh token;
- granted scopes;
- connection status.

Do not share Tesla OAuth tokens between users.

Phase 4 stores access and rotating refresh tokens together as an AES-256-GCM
authenticated ciphertext in `tesla_connections/{user_id}`. The encryption key
is injected into mcp-gateway from the `tesla-token-encryption-key` Secret Manager
container. The internal `user_id` is authenticated additional data, so moving a
ciphertext between users makes decryption fail. Plaintext tokens are never
stored in Firestore, logged, or returned by HTTP/MCP responses.

Authorization requests use random, hashed-at-rest, single-use state records with
a ten-minute lifetime. The state record binds the callback to the platform
`user_id` resolved at `/tesla/oauth/start`; browser-started requests additionally
bind to the exact initiating opaque platform session and verify that binding
before exchanging a Tesla code or persisting tokens. The callback accepts no
caller-selected user identity. Tesla's signed OIDC ID token must contain the recorded
nonce and the configured client audience. Tesla does not currently document or
advertise PKCE for this confidential-client flow; see `docs/tesla-onboarding.md`.
Firestore TTL deletes abandoned state records after `expires_at`; correctness
still comes from transactional expiry validation and single-use deletion because
TTL processing is asynchronous.
Gateway application access logs omit all query strings. Cloud Run's platform
request log records the full request URL before application-level redaction, so
Terraform excludes `run.googleapis.com/requests` entries for callback URLs that
contain a query string. This prevents callback authorization codes and state
values from being retained prospectively; the exclusion does not retroactively
delete earlier entries. JSON callback and onboarding
responses are marked `no-store` with a `no-referrer` policy.

Refresh commits use an optimistic token version. A successful exchange must
atomically replace the encrypted credential blob. A competing loser reloads the
newer committed credential rather than overwriting it. Tesla `login_required`
marks only that connection for reauthorization.

---

## 5. Vehicle ownership mapping

After a Tesla OAuth connection succeeds, call Tesla vehicle-list and create/update a registry record for every vehicle returned.

Every platform operation first resolves:

```text
authenticated user -> permitted Tesla connection -> permitted vehicle
```

Never authorize a command merely because the caller knows a VIN.

Telemetry routing similarly resolves:

```text
VIN -> vehicle registry -> owner user_id -> BigQuery dataset
```

Unknown VINs are quarantined; ownership is never guessed.

Phase 4 also creates `vehicle_vin_index/{sha256(vin)}` transactionally with the
vehicle record. A VIN already assigned to another platform `user_id` causes
onboarding to fail closed instead of silently creating a second owner mapping.

Phase 7 repeats the trusted chain during every telemetry delivery. The processor
requires the SHA-256 VIN index, authoritative vehicle document, exactly one
active allowlist record, and an opaque `tesla_u_*` dataset identifier to agree.
A publisher cannot supply `user_id`, `vehicle_id`, or dataset. Disabled users,
duplicate bindings, inconsistent VIN records, and malformed dataset IDs fail to
the restricted quarantine path rather than a user dataset.

The VIN presented to that routing chain is not accepted from arbitrary JSON.
Tesla's pinned official receiver terminates mTLS with
`tls.RequireAndVerifyClientCert`, validates the client chain against its embedded
production Tesla CA bundle, derives the device identity from the verified leaf
certificate, and overwrites VIN fields in decoded `V`, `alerts`, `errors`, and
`connectivity` records with that certificate-derived identity. The processor
then requires the exact Terraform-owned fleet subscription associated with the
record type. The operator fixture subscription requires its bounded synthetic
marker and can never enter a user dataset as vehicle telemetry.

Phase 8 keeps telemetry configuration writes in the authenticated control
plane. The browser session supplies only an internal vehicle selector and an
exact, expiring-by-drift configuration hash; Firestore re-resolves ownership,
Tesla OAuth scopes, active status, Virtual Key pairing, and reported Fleet
Telemetry client capability before any call. Each apply contains exactly one
server-resolved VIN, is signed through the official Vehicle Command Proxy, and
is audited before the Tesla write. A successful state is persisted only after
Tesla reports `synced=true`, the read-back configuration matches exactly, and
no current relevant telemetry errors are present.

Vehicle trust contains only a public CA bundle. The expiring server leaf and
all private keys are excluded from `telemetry-server-ca-profile`. The gateway
may read that public trust profile but the certificate-renewal job receives no
Tesla OAuth credentials or vehicle-command key. A separate non-secret readiness
manifest gates a real CA/hostname migration; ordinary compatible leaf renewal
cannot trigger a Tesla configuration call.

Internet scanners and arbitrary clients can reach the public TCP socket, but a
client with no Tesla-issued certificate, an invalid chain, or no verified leaf
identity fails during the mTLS handshake before a telemetry record can be
decoded or published. This is an authenticity boundary, not a denial-of-service
boundary; network and receiver resource protections still matter for abusive
connection attempts.

This proves that an accepted production record traversed a Tesla-authenticated
vehicle connection; it is not proof that every sensor value is physically true.
Tesla explicitly advises backends to anticipate compromise of a vehicle TLS
private key. Keep ownership allowlisting and input sanitization, flag implausible
values in derived analytics, and never let telemetry alone authorize a vehicle
command.

Implementation reference: Tesla's official
[Fleet Telemetry receiver](https://github.com/teslamotors/fleet-telemetry/tree/v0.9.4)
documents vehicle client-certificate authentication and the same defense-in-depth
warning about possible vehicle-key compromise.

Pub/Sub push has two authentication layers: Cloud Run IAM accepts only the
`tpp-pubsub-push` invoker with the configured custom audience, and the
application verifies the Google signature, exact audience, issuer, exact
service-account email, and `email_verified`. A successful HTTP response is sent
only after BigQuery accepts the raw append.

---

## 6. Multiple vehicles

A single user may have any number of vehicles returned by Tesla.

Virtual-key pairing and Fleet Telemetry configuration are per vehicle.

The BigQuery dataset is per user, not per vehicle, so the user can naturally ask cross-vehicle questions.

---

## 7. Application signing key

Initial design uses one Tesla developer application and one EC `prime256v1` application key pair.

Private key:

- Secret Manager;
- available only to Vehicle Command Proxy / minimum command-signing runtime;
- never returned to clients;
- never copied to the telemetry VM.

Phase 4 does not inject the private key into the gateway container because no
command-signing path exists yet. Its public half is injected from the separate
`tesla-command-public-key` Secret Manager container solely for the public
`.well-known` response. Phase 6 must keep private-key access limited to the
Vehicle Command Proxy/minimum signing runtime.

Public key:

- hosted permanently at Tesla's required `.well-known` path;
- paired separately to each vehicle.

This is an acceptable simplification for personal use plus a few manually trusted users. Revisit blast-radius isolation only if this becomes a broad service.

---

## 8. MCP authorization vs Tesla authorization

These are separate gates:

1. Is this person allowed to use the platform? -> platform OAuth/allowlist.
2. Has this person authorized the developer app against their Tesla account? -> Tesla OAuth.
3. Does this physical vehicle trust signed commands from the app? -> Virtual Key.

A command requires all relevant gates.

---

## 9. Analytics isolation

Every user gets an opaque BigQuery dataset ID determined server-side.

The generic SQL MCP tool never accepts a dataset/project argument.

The server:

- resolves current user's dataset;
- sets it as default;
- rejects explicit dataset/project references;
- permits read-only SQL only.

Phase 9 enforces this with SQLGlot's BigQuery AST and scope model before the
query reaches BigQuery. It executes only the parser's canonical rendering,
requires each physical table to be an unqualified static-catalog object, and
rejects generic/user-defined calls except a narrow deterministic geography
constructor allowlist. BigQuery then independently receives the trusted
dataset as `defaultDataset`, a 1 GiB `maximumBytesBilled` cap, and a 30-second
job timeout. Results are bounded to 200 rows/512 KiB. The gateway service
account has dataset-level read access and project-level job creation only; it
cannot use caller SQL to acquire another dataset grant.

The approved user's normalized invitation email also has dataset-level
`READER` access to that user's dataset so the owner can inspect and query their
own raw history and derived views in the BigQuery console. This direct grant is
part of the authoritative per-user dataset ACL; it never grants access to a
different user's dataset. Running console queries additionally requires a
separate project-level BigQuery job-creation role. The operator account has
that role through reviewed Terraform; `add-user` does not grant it to every
approved user automatically.

The operator-only `tpp-user-admin` is not a runtime query principal and has no
BigQuery job role. BigQuery requires a view creator to hold `tables.getData` on
referenced raw/views while validating the definition, so the idempotent
provisioner temporarily grants that keyless identity `READER` on exactly the
target user's dataset. It revokes the entry in a `finally` path on both success
and failure, restoring the permanent owner/gateway-reader/processor-writer ACL.
A permanent approved-user reader entry is restored with those service entries.
A subsequent repair also removes any transient entry left by process death
before attempting view work. This is an audited operator-time exception, not a
cross-user MCP or steady-state admin access path.

This keeps generic model-written SQL without requiring complex shared-row security.

---

## 10. Disable/revoke behavior

Disabling a platform user:

- immediately blocks MCP access;
- does not automatically delete historical BigQuery data;
- should stop new commands/analytics;
- may optionally stop telemetry configuration after an explicit admin action.

Tesla access can additionally be revoked through Tesla consent management, and the Virtual Key can be removed from each vehicle.

---

## 11. Sensitive logs

Never log tokens, secrets, PINs, or unnecessary exact location.

Analytics logs never contain model-authored SQL or returned rows. They contain
only opaque user/correlation/job identifiers, referenced allowlisted object
names, processed/billed bytes, duration, row/byte counts, truncation, and safe
failure type/category. This preserves cost and performance diagnostics without
copying historical location or media data into general logging.

Command audit is required, but redacted.

Phase 6 creates `tesla_command_audits/{random_audit_id}` before every MCP write
reaches Tesla. A completion update records success, Tesla rejection, or a safe
error category; if completion fails, the initial `attempted` record remains.
Audit parameters structurally redact PIN/password, token, VIN, calendar, and
exact-location fields. Structured navigation destinations and encoded waypoint
lists are redacted as whole values so free-form addresses or nested coordinates
cannot survive under otherwise generic keys. Tesla response bodies are not
copied into the audit.
An automatic pre-command wake is a separate audited `tesla_wake_up` attempt,
linked operationally by its `automatic_for` tool name. The requested command is
still sent at most once and receives its own audit record.

Every actual outbound Tesla HTTPS attempt also emits structured operational
events to Cloud Logging at the transport boundary. One `started` event is
followed by exactly one `completed` or `failed` event when the process remains
alive. Events contain a random API call ID, MCP correlation ID when available,
internal vehicle ID when available, source/flow phase, attempt number, HTTP
method, templated route, typed operation, destination (`tesla_fleet_api`,
`tesla_oauth`, or the local `vehicle_command_proxy`), Tesla region,
request/query field names, byte counts,
duration, HTTP status/outcome, and a narrowly selected diagnostic summary.

Transport logging never records raw URLs, query values, HTTP headers, bearer
tokens, request bodies, response bodies, VINs, invitation/invoice IDs, PINs,
passwords, OAuth codes, client secrets, token values, calendar contents, or
coordinates. Diagnostic summaries may contain only the top-level Tesla
`error`, `error_description`, or `message`, plus command `result`/`reason`.
Summaries are parsed only for bounded error bodies and bounded command results,
not successful read bodies. Strings are length-bounded and scrubbed for every
scalar request-body echo, request secrets, VINs, emails, URLs, coordinates,
JWTs, and labeled credentials. OAuth success payloads are therefore visible
only as status/size metadata, never as credentials.

Firestore command audit remains the durable authorization/safety record for
writes. Cloud Logging is the operational trace of network attempts, including
reads, OAuth/token calls, retries, wake polling, local proxy calls, and partner
administration. A `started` event without a terminal event indicates process
termination or interruption and must not be treated as proof that a command did
or did not execute.

The official Vehicle Command Proxy is an instance-local, non-ingress Cloud Run
sidecar. It listens on the shared container network only so Cloud Run can run
the sidecar startup probe; public service ingress remains assigned solely to
the gateway application container. Only
that container mounts the application EC private key and its separate TLS
private key. The Python container mounts the TLS certificate only. Cloud Run
uses one service account for all containers in a revision, so this is a mount
and process boundary rather than distinct IAM principals; telemetry-edge and
telemetry-processor have no access. A separate signing service is the future
hardening option if the trust population expands.

Analytics job metadata may record bytes processed, duration, tables referenced, and user/vehicle scope; do not dump query results into general logs.

Telemetry processor logs contain disposition, record type, internal opaque
user/vehicle IDs when resolved, Pub/Sub message ID, and bounded error category.
They do not contain VIN, decoded payload, coordinates, or credentials. The
complete payload and VIN are retained only in the correct restricted BigQuery
destination. The official edge uses JSON operational logging with verbose
payload logging disabled.
