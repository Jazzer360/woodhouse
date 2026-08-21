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
  status
  oidc_issuer
  oidc_subject
  created_at
  notes
```

Manual admin workflow:

```text
scripts/admin/add-user --email homer@example.com
scripts/admin/disable-user --email homer@example.com
```

`add-user` should be idempotent and may also create the user's BigQuery dataset.

No public enrollment UI is required.

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
