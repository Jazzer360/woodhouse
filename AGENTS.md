# AGENTS.md

This repository controls real vehicles and stores sensitive historical location/telemetry data.

**Read `docs/architecture.md` and the task-relevant files under `docs/` before changing code.**

The Markdown files in this repository are the implementation source of truth. Do not require a separate Word/PDF field guide to understand the project.

---

## 1. Required reading by task

Always read:

- `docs/architecture.md`
- this file

Also read:

- auth/security work -> `docs/security-model.md`
- Tesla onboarding/OAuth/Virtual Key work -> `docs/tesla-onboarding.md`
- telemetry/BigQuery/analytics work -> `docs/data-and-analytics.md`
- Fleet API/tool work -> `docs/fleet-api-coverage.md`
- webhooks/events -> `docs/event-and-webhooks.md`
- phase planning -> `docs/implementation-roadmap.md`
- copy/paste implementation prompts -> `docs/prompt-pack.md`

Before Tesla-specific implementation, verify current behavior against Tesla's official developer documentation. Do not assume endpoint names, fields, scopes, firmware requirements, pricing, or limits are unchanged.

`docs/prompt-pack.md` is a sequencing/convenience document, not permission to override architecture. If a prompt conflicts with `AGENTS.md` or a task-specific source-of-truth document, follow the source-of-truth document and update the prompt pack to match.

---

## 2. Do not overbuild

This is a personal-first project shared only with manually approved users.

Do not add without explicit instruction:

- public signup;
- organizations;
- plans/subscriptions;
- usage quotas or commercial rate-limit systems;
- billing/metering product logic;
- Kubernetes;
- elaborate tenancy management;
- a custom web UI;
- per-user feature-plan matrices.

A BigQuery `maximumBytesBilled` query guardrail is acceptable as protection against accidental runaway SQL. It is not a user quota system.

---

## 3. Identity and authorization invariant

Every request must resolve an authenticated internal `user_id` before accessing Tesla or historical data.

Never authorize from a caller-provided:

- `user_id`;
- email;
- BigQuery dataset ID;
- VIN ownership claim.

The server derives identity from the authenticated session/token and the allowlist binding.

The initial access model is a manual allowlist, stored in Firestore or the approved equivalent.

On first successful verified login, bind the immutable OIDC `(issuer, subject)` to the preapproved record. Later authorization uses the bound immutable identity.

---

## 4. Multi-user means simple isolation

One approved application user is one tenant boundary.

Each user has:

- their own Tesla OAuth token state;
- their own vehicle ownership mappings;
- their own BigQuery dataset;
- zero visibility into another user's history/vehicles.

Do not build organization-level sharing unless explicitly requested.

---

## 5. Multiple vehicles are mandatory

Never assume one vehicle per user or Tesla account.

All relevant code must be vehicle-scoped:

- OAuth discovery may return many vehicles;
- each vehicle gets an internal `vehicle_id`;
- each vehicle is separately paired with the application Virtual Key when required;
- each vehicle is separately configured for Fleet Telemetry;
- all raw historical rows contain `vehicle_id`;
- analytics can query one or several vehicles in the authenticated user's dataset.

If an MCP tool omits the vehicle selector:

- auto-resolve only if exactly one eligible vehicle exists;
- otherwise return an ambiguity requiring the caller/model to choose.

Never guess based on the last-used vehicle.

---

## 6. Never commit or log secrets

Never commit/log:

- Tesla client secret;
- Tesla access token;
- Tesla refresh token;
- EC command-signing private key;
- PINs;
- webhook signing keys;
- MCP authorization signing secrets;
- service-account private keys;
- cookies/session tokens.

Do not place them in:

- source;
- tests/fixtures;
- Docker images;
- Terraform literal values;
- Cloud Build substitutions that echo;
- README/docs;
- logs.

Use Secret Manager or the approved runtime secret store.

---

## 7. Tesla OAuth is not MCP authentication

Keep these trust relationships separate:

```text
MCP client -> platform auth/allowlist -> MCP server
MCP server -> per-user Tesla OAuth -> Tesla account
MCP server -> application private key -> signed vehicle commands
```

Never give the MCP client Tesla access/refresh tokens or the vehicle command private key.

---

## 8. Tesla refresh-token handling

Follow current Tesla official docs.

Current design expectations:

- authorization-code flow;
- `offline_access`;
- state validation;
- nonce/PKCE where required/recommended;
- safe refresh-token rotation;
- atomically persist replacement refresh token;
- explicit reconnect flow for `login_required`/revocation.

Mutable token state belongs in locked-down application storage, not Git or environment files.

---

## 9. Virtual Key / command signing

Do not implement Tesla's vehicle cryptography from scratch without a strong reason.

Prefer Tesla's official Vehicle Command Proxy.

- private key stays backend-only;
- public key stays hosted at Tesla-required `.well-known` path;
- virtual-key pairing is per vehicle;
- command proxy is internal-only;
- signing key access is narrowly scoped.

---

## 10. Complete Fleet API coverage is a contract

`docs/fleet-api-coverage.md` is authoritative for endpoint coverage.

Before implementing Fleet API client/tool coverage:

1. re-open Tesla's current official Vehicle Endpoints, Vehicle Commands, User Endpoints, Partner Endpoints, and Charging Endpoints pages;
2. compare them against the matrix;
3. add/remove/update rows if Tesla changed the API;
4. note the audit date in the document;
5. do not declare the phase complete while any **Required** row is unimplemented.

Do not interpret “broad support” as permission to stop after common lock/climate/charging commands.

---

## 11. No generic Tesla MCP passthrough

Forbidden MCP design:

```text
call_tesla_api(method, endpoint, body)
```

Internal HTTP helpers may be generic, but the MCP surface must be typed and intentional.

Every MCP-exposed Tesla operation must have:

- clear name/description;
- typed validated arguments;
- current-user vehicle ownership check;
- required Tesla scope;
- wake behavior;
- risk class;
- safe retry/idempotency policy;
- audit behavior;
- tests.

---

## 12. Command safety

### Read-only

Execute after successful authorization.

### Normal reversible controls

Require clear user intent but no artificial second confirmation when intent is already explicit.

### Security-sensitive controls

Examples:

- unlock;
- trunk/frunk;
- windows;
- remote/keyless driving;
- HomeLink;
- PIN/security settings;
- parental/speed-limit controls.

Require unambiguous current-turn intent.

Example: `start the car` is ambiguous between climate and keyless driving. Do not guess.

### Excluded/admin-sensitive

Do not MCP-expose destructive/admin operations marked excluded in the coverage matrix unless architecture is explicitly revised.

---

## 13. Command audit

Every attempted vehicle command—success or failure—must create an audit record containing:

- timestamp;
- authenticated `user_id`;
- internal `vehicle_id`;
- command/tool name;
- redacted parameters;
- result/error category;
- correlation ID;
- source (`chatgpt-mcp`, `admin`, future UI/automation).

Never store PIN values or secrets in command audit.

---

## 14. Live vs historical rule

### Current/live state

Prefer Fleet API through the gateway.

Do not query BigQuery merely to answer normal realtime questions.

Avoid unnecessary vehicle wake-ups; use targeted reads and current telemetry knowledge where appropriate.

### Historical questions

Use BigQuery.

Do not invent bespoke MCP history endpoints for every imaginable question when the generic analytics interface can answer it.

---

## 15. Raw telemetry preservation invariant

**Throttle at the source; preserve at the destination.**

Fleet Telemetry configuration controls field selection/frequency.

After a valid record reaches the telemetry VM:

- publish it to Pub/Sub;
- append it to the authenticated owner's raw BigQuery history;
- do not sample/thin based on time or repeated values;
- do not impose a default expiration.

Raw telemetry is append-oriented.

Do not allow semantic-event logic, webhook failure, or derived-view logic to decide whether the raw record is retained.

---

## 16. Preserve raw metadata

Raw records must preserve at least:

- source timestamp;
- ingestion timestamp;
- internal `user_id`;
- internal `vehicle_id`;
- VIN/Tesla vehicle identifier as required for provenance;
- record/message type;
- complete decoded payload;
- telemetry configuration version/hash;
- transport/message ID if available.

Use source timestamp for historical chronology and ingestion timestamp for transport diagnostics.

Buffered telemetry may arrive much later than its source timestamp.

---

## 17. Unknown vehicle telemetry

If telemetry arrives for a VIN that is not mapped to an approved user/vehicle:

- do not guess ownership;
- do not route it to another user's dataset;
- preserve enough data in a restricted quarantine/system path for diagnosis;
- alert/log the condition;
- repair the registry/configuration deliberately.

---

## 18. Analytics endpoint policy

Required MCP tools:

```text
get_analytics_schema
run_analytics_query
```

The LLM should be responsible for composing purpose-specific SQL from the schema.

Do not add a new endpoint just because a user asks a novel statistic.

Examples that should not require new tools:

- trip playlists;
- efficiency by weather;
- FSD share by month;
- charge behavior by location;
- cross-vehicle comparisons.

---

## 19. Analytics SQL security

`run_analytics_query` is read-only.

Must:

- derive dataset from authenticated user;
- set it as BigQuery `defaultDataset`;
- allow Standard SQL `SELECT` / `WITH` only;
- reject DML/DDL;
- reject explicit cross-dataset/project table references;
- reject external queries/connections;
- reject remote functions;
- reject `EXPORT DATA`;
- dry-run first;
- impose timeout/result bounds;
- use a reasonable max-bytes safety cap;
- log query job metadata without dumping sensitive result data.

Use a real SQL parser/AST or comparably robust validation. Do not secure this with regex alone.

---

## 20. BigQuery design rules

Each approved user gets one dataset with no default expiration.

All of that user's vehicles share it, separated by `vehicle_id`.

Raw telemetry table should be partitioned by source date and clustered by useful keys such as `vehicle_id` and record type.

Derived entities are views/tables built from raw truth, e.g.:

- drives;
- charge sessions;
- media history;
- semantic events;
- daily summaries.

Never discard raw history after generating derived data.

---

## 21. Telemetry edge stays dumb

The VM may:

- terminate Tesla Fleet Telemetry transport;
- decode records;
- publish records;
- expose health/metrics.

It must not contain:

- user allowlist logic beyond what is necessary to tag/route safely;
- Tesla OAuth refresh tokens;
- Tesla client secret;
- command private key;
- MCP authorization;
- BigQuery analytics logic;
- semantic event definitions.

Prefer Pub/Sub as the durability boundary before downstream BigQuery writes.

---

## 22. Event/webhook processing never blocks raw storage

The processor may derive events and webhooks from the raw feed, but a webhook bug must not cause raw history loss.

Use source timestamps and an actionability window so buffered/replayed telemetry does not generate stale real-time actions.

Webhook handlers must be idempotent and HMAC-sign outbound payloads.

---

## 23. CI/CD rules

Production state comes from Git merges.

PR:

- tests;
- lint/type checks;
- container build;
- dependency/security scan;
- Terraform fmt/validate/plan;
- no deployment.

Main merge:

- immutable image tagged with commit SHA;
- deploy affected Cloud Run services;
- telemetry-edge VM pulls exact image digest;
- health check;
- deployment metadata recorded.

Do not deploy `:latest` as the production identity.

Do not manually edit application source on the production VM.

---

## 24. Infrastructure / IAM

Use least privilege.

Do not use `roles/owner` or `roles/editor` as convenience shortcuts.

Separate major identities:

- MCP gateway service account;
- telemetry processor service account;
- telemetry VM service account;
- Cloud Build deployment identity.

The telemetry VM does not need Tesla OAuth or command secrets.

The telemetry processor does not need the vehicle command private key.

---

## 25. Tests must never touch the real car

Automated tests use mocks/fakes/emulators.

CI must never:

- unlock;
- honk;
- flash lights;
- start climate;
- change charging;
- enable keyless driving;
- otherwise issue real commands.

Live smoke tests must be deliberate and manually initiated.

---

## 26. Documentation updates are part of code changes

Update in the same PR when applicable:

- endpoint coverage -> `docs/fleet-api-coverage.md`;
- architecture boundaries -> `docs/architecture.md`;
- security/auth -> `docs/security-model.md`;
- Tesla setup -> `docs/tesla-onboarding.md`;
- analytics/raw schema -> `docs/data-and-analytics.md`;
- events/webhooks -> `docs/event-and-webhooks.md`;
- phase/sequence -> `docs/implementation-roadmap.md`;
- MCP tool mapping -> `docs/mcp-tool-catalog.md` once created.

Do not leave critical operating knowledge only in PR comments.

---

## 27. PR definition of done

A PR is done when:

- scope is implemented;
- tests pass;
- lint/typecheck passes;
- secrets are not exposed;
- user/vehicle authorization is enforced;
- multiple vehicles are handled where relevant;
- historical retention invariant is preserved;
- docs are updated;
- deployment implications are documented;
- assumptions/deviations are stated.

---

## 28. When architecture and current Tesla docs differ

Protocol correctness follows Tesla's current official documentation.

If Tesla changes something that invalidates a seed assumption:

1. stop before silently redesigning the project;
2. update the relevant source-of-truth doc in the same PR;
3. explain why;
4. keep the simplest architecture that satisfies the current API.
