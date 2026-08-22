# Security and Access Model

## 1. Goal

Keep access extremely simple while ensuring that an MCP endpoint capable of controlling vehicles and querying precise historical location data is not public.

This is not a public signup system. The owner manually approves users.

---

## 2. Human identity

Initial recommendation: use a mainstream OIDC identity provider (Google is the practical first choice) and reuse the proven MCP authorization pattern from the existing personal YNAB integration where possible.

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

Phase 3 uses Google OIDC ID tokens as the practical default. The gateway verifies
the Google signature, expiry, configured audience, issuer, and immutable subject.
Both Google issuer spellings accepted by its token verifier are normalized to
`https://accounts.google.com` before creating or resolving immutable bindings.
Provider verification is behind an interface so the allowlist and tenant model
do not depend on Google-specific claims.

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

The provider login/token-acquisition UX remains the OIDC client's concern in
this phase. The gateway implements no public signup and stores no Google access
or refresh token.

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
`user_id` resolved at `/tesla/oauth/start`; the callback accepts no caller-
selected user identity. Tesla's signed OIDC ID token must contain the recorded
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

Command audit is required, but redacted.

Analytics job metadata may record bytes processed, duration, tables referenced, and user/vehicle scope; do not dump query results into general logs.
