# Tesla Personal Platform — Architecture

**Status:** Final seed architecture for implementation  
**Repository:** `tesla-personal-platform`  
**Primary deployment:** Google Cloud Platform  
**Primary interface:** Authenticated MCP server  
**Live vehicle source:** Tesla Fleet API  
**Historical source:** BigQuery  
**Realtime history/event source:** Tesla Fleet Telemetry

> The repository Markdown files are the implementation source of truth. The human-readable field guide is supplementary and must not be required by coding agents.

---

## 1. What we are building

A small, personal-first Tesla platform with four capabilities:

1. **Live vehicle access through MCP**
   - current vehicle state from Tesla Fleet API;
   - broad typed vehicle controls and settings;
   - support multiple vehicles per Tesla account;
   - never depend on BigQuery for ordinary current-state requests.

2. **Complete historical telemetry in BigQuery**
   - Tesla Fleet Telemetry streams to a tiny always-on VM;
   - every valid telemetry record received by the platform is durably retained;
   - no default expiration;
   - no downstream sampling/thinning after receipt;
   - BigQuery is the source for history-based questions.

3. **Generic analytics rather than bespoke historical endpoints**
   - MCP exposes `get_analytics_schema`;
   - MCP exposes `run_analytics_query`;
   - the LLM inspects the user-safe schema, writes purpose-specific SQL, executes it, and interprets the result;
   - no need to prebuild endpoints such as `get_colorado_trip_playlist`.

4. **Realtime semantic events/webhooks**
   - raw telemetry can also feed a lightweight state/event processor;
   - changes such as charge complete, unlock, drive start/end, geofence transitions, etc. can become webhook events;
   - event processing must never be allowed to block or filter raw historical storage.

This is **not** intended to be a public SaaS product. It is primarily for the owner, with simple manual sharing to a few explicitly approved friends.

---

## 2. Non-goals — do not overbuild

Do not introduce these unless the project is explicitly redesigned later:

- public signup;
- organizations/workspaces;
- subscription plans;
- user billing;
- per-user rate limits or quotas;
- elaborate tenant administration;
- multi-region high availability;
- Kubernetes;
- a custom web dashboard before there is a concrete need;
- generic unrestricted Tesla API passthrough tools;
- dependence on a native ChatGPT BigQuery connector.

Multi-user support should stay simple: **manual allowlist + isolated user data + each user connects their own Tesla account.**

---

## 3. Simple user / tenancy model

For this project, **one approved application user is one tenant boundary**.

A user can have:

- one authenticated platform identity;
- one or more Tesla OAuth connections if ever needed;
- multiple Tesla vehicles under a Tesla account;
- one BigQuery dataset containing history for all of that user's vehicles.

Conceptually:

```text
Approved user
    |
    +-- Platform identity (OIDC issuer + immutable subject)
    |
    +-- Tesla OAuth connection
    |      +-- Vehicle A
    |      +-- Vehicle B
    |      +-- Vehicle C
    |
    +-- BigQuery dataset
           +-- raw telemetry for all owned/authorized vehicles
           +-- derived views/tables
```

### 3.1 Manual access control

Use a small Firestore allowlist initially.

Recommended record:

```text
allowed_users/{normalized_email}
  status: active | disabled
  user_id: opaque internal ID
  dataset_id: opaque server-controlled BigQuery dataset ID
  oidc_issuer: null until first successful login
  oidc_subject: null until first successful login
  created_at
  notes
```

An `oidc_identities/{sha256(issuer + NUL + subject)}` lookup record may mirror the
binding so authorization can resolve by immutable identity without querying or
trusting the token's current email. The allowlist record remains authoritative;
the lookup record is created in the same Firestore transaction as the first bind.

Initial flow:

1. Owner manually adds a friend's verified email to the allowlist.
2. Friend authenticates through the configured identity provider.
3. On first successful login, if email is verified and allowlisted, bind the immutable `(issuer, subject)` to that allowlist record.
4. Future authorization uses the immutable identity, not merely the email string.
5. Disabling the allowlist record immediately blocks MCP access without deleting historical data.

Use the already-proven personal MCP authorization pattern from the YNAB project where practical. Google remains the practical upstream sign-in identity. For ChatGPT interoperability, an established OAuth 2.1 authorization server (Auth0 by default) brokers Google sign-in and issues an access token whose audience is the MCP resource. Do not couple the allowlist or tenant model to provider-specific identity semantics.

The gateway also serves a small browser onboarding page. It is not a signup
portal: an operator must run `scripts/admin/add-user` first. After platform
sign-in, the page starts the existing per-user Tesla OAuth flow, lists every
owned vehicle, provides Tesla's vehicle-specific Virtual Key deep link, and
refreshes pairing status. Pairing itself remains an explicit Tesla app action.

### 3.2 Adding a friend should remain simple

The desired admin experience is approximately:

```text
add-user homer@example.com
```

That admin action may:

- create the allowlist record;
- allocate an opaque `user_id`;
- create the user's BigQuery dataset;
- create any minimal supporting configuration.

No signup portal is required.

---

## 4. Multiple vehicles are first-class

Never design around a single `current_vehicle` per user.

Tesla OAuth is account-scoped; one authorized Tesla account may return multiple vehicles. Each vehicle must have its own internal record and must be separately considered for:

- live reads;
- commands;
- virtual-key pairing;
- Fleet Telemetry configuration;
- telemetry ownership routing;
- historical analytics.

Recommended model:

```text
users/{user_id}

tesla_connections/{connection_id}
  owner_user_id
  tesla_subject/account metadata
  encrypted refresh-token state

vehicles/{vehicle_id}
  owner_user_id
  tesla_connection_id
  vin
  tesla_vehicle_id
  display_name
  virtual_key_status
  telemetry_status
  analytics_dataset_id
```

MCP vehicle arguments may be optional only when the caller has exactly one eligible vehicle. If multiple vehicles exist and the request is ambiguous, do not guess.

---

## 5. High-level architecture

```text
                                      ChatGPT / MCP client
                                              |
                                              | MCP OAuth/authentication
                                              v
                                +-----------------------------+
                                | MCP / Tesla Gateway         |
                                | Cloud Run                   |
                                |                             |
                                | - allowlist enforcement     |
                                | - Tesla OAuth onboarding    |
                                | - Fleet API client          |
                                | - typed vehicle tools       |
                                | - Vehicle Command Proxy     |
                                | - analytics query endpoint  |
                                +--------------+--------------+
                                               |
                                 live reads /  | signed commands
                                               v
                                      Tesla Fleet API
                                               |
                                               v
                                           Vehicles
                                               |
                                               | Fleet Telemetry
                                               v
                           +-----------------------------------------+
                           | Telemetry Edge                         |
                           | tiny Compute Engine VM                 |
                           | Tesla fleet-telemetry receiver         |
                           +-------------------+---------------------+
                                               |
                                               | publish every record
                                               v
                                           Pub/Sub
                                               |
                                               v
                           +-----------------------------------------+
                           | Telemetry Processor                    |
                           | Cloud Run                              |
                           |                                       |
                           | - map VIN -> approved user/vehicle     |
                           | - append every raw event to BigQuery   |
                           | - optionally derive semantic events    |
                           | - update event-state in Firestore      |
                           | - dispatch configured webhooks         |
                           +---------------+-------------------------+
                                           |
                               +-----------+-----------+
                               |                       |
                               v                       v
                        Per-user BigQuery          Firestore
                        datasets                  allowlist/state
```

The Vehicle Command Proxy is a second container in the same Cloud Run revision,
not an internet-facing service. It listens only on `127.0.0.1`, mounts the
application EC private key and a separate loopback TLS key, and accepts only the
typed command paths selected by the gateway. The Python container mounts only
the TLS public certificate. Cloud Run's single revision service account is a
known isolation limit; the sidecar mount boundary is the smallest supported
private deployment for this personal platform.

The live MCP surface is an explicit registry of typed tool names and schemas.
It derives the platform user before dispatch, resolves only that user's vehicle
records, and uses a vehicle only when selected by opaque internal ID or when
exactly one eligible vehicle exists. Current-state tools call Fleet API, never
BigQuery. See `docs/mcp-tool-catalog.md` for the complete mapping and safety
metadata.

### Why Pub/Sub stays in the design

The VM should not synchronously depend on BigQuery availability. Pub/Sub is a small, cheap durability boundary:

```text
vehicle -> VM -> Pub/Sub -> BigQuery processor
```

The Pub/Sub hop is **not** a filtering layer. It exists so temporary downstream failures do not cause telemetry loss.

The implemented edge is Tesla's official Fleet Telemetry `v0.9.4` receiver,
pinned by image digest and configured to use its native Google Pub/Sub
dispatcher. Tesla record types are published to the receiver-defined topics
`tpp-raw-telemetry_V`, `_alerts`, `_connectivity`, and `_errors`; all four feed
the same authenticated persistence handler. The original
`tpp-raw-telemetry` topic is reserved for guarded non-vehicle operator fixtures
and never receives permission from the edge identity.

The receiver validates Tesla vehicle client certificates with its embedded
production Tesla CA bundle and derives VIN/device identity from the verified
certificate. It overwrites payload VIN fields before dispatch. The processor
also binds each push to the exact fleet subscription and expected record type,
so the separately authorized synthetic topic cannot impersonate the production
vehicle path.

Public server-certificate lifecycle is owned by a separate scheduled Cloud Run
Job, not by telemetry-edge. The job performs DNS-01 with a Cloudflare token
restricted to DNS edits for the one authoritative zone, retains compact ACME
state in Secret Manager, validates SAN/chain/key/validity, publishes an atomic
certificate release manifest, restarts the VM, and verifies both guest health
and the public leaf fingerprint. The edge continues to possess only its TLS
material and receiver responsibilities.

Routine renewal of the short-lived server leaf certificate must not rewrite a
vehicle's Fleet Telemetry configuration. Phase 8 must configure a stable
hostname, port, and CA trust profile that is compatible with replacement leaf
certificates, and must never put the expiring leaf certificate in the vehicle's
`ca` field. Tesla's current documentation requires the configured host and CA
to validate the served certificate but does not promise that every future
public-CA chain transition is transparent. Phase 8 therefore also owns a
candidate-versus-configured-trust compatibility gate and a separate,
per-vehicle signed reconciliation path for genuine hostname, port, or CA trust
changes. The certificate-renewal job must remain unable to access Tesla OAuth
tokens or the command-signing key.

Phase 8 implements this boundary with a CA-only Secret Manager trust profile,
an authenticated per-user/per-vehicle controller in the gateway control plane,
and the official Vehicle Command Proxy sidecar for the signed create operation.
The field-profile hash, full config hash, and CA trust-profile ID/hash are
persisted independently; the broad field policy is in
[`fleet-telemetry-configuration.md`](fleet-telemetry-configuration.md).

---

## 6. Repository layout

Use one monorepo.

```text
/
  README.md
  AGENTS.md

  docs/
    architecture.md
    security-model.md
    tesla-onboarding.md
    data-and-analytics.md
    fleet-api-coverage.md
    event-and-webhooks.md
    implementation-roadmap.md
    prompt-pack.md                  # copy/paste implementation prompts

    deployment.md                 # created/refined during Prompt 1
    mcp-tool-catalog.md           # created/refined during later phases
    runbooks/
      oauth-recovery.md
      telemetry-repair.md
      telemetry-cert-renewal.md
      command-key-rotation.md
      rollback.md
      emergency-revocation.md

  services/
    mcp-gateway/
    telemetry-processor/
    telemetry-edge/
    certificate-renewer/

  packages/
    tesla-client/
    auth/
    shared-models/
    analytics/
    event-schema/

  infra/
    terraform/

  scripts/
    admin/
    dev/
```

Do not split repositories until there is a concrete reason.

---

## 7. Core service responsibilities

### 7.1 MCP / Tesla Gateway — Cloud Run

Owns:

- MCP protocol endpoint;
- OAuth protected-resource discovery and access-token validation;
- allowlisted browser onboarding at `/onboarding`;
- platform authentication / allowlist enforcement;
- Tesla OAuth start/callback/refresh;
- `.well-known` Tesla public-key endpoint;
- Fleet API typed client;
- Vehicle Command Proxy integration;
- vehicle resolution for multiple vehicles;
- current/live state tools;
- typed vehicle command tools;
- `get_analytics_schema`;
- `run_analytics_query`;
- optional event/webhook management tools.

Must not:

- expose Tesla credentials to the client;
- expose a generic `call_tesla_api` MCP tool;
- trust a caller-supplied user ID or BigQuery dataset ID.

### 7.2 Telemetry Edge — Compute Engine VM

Owns only:

- public Fleet Telemetry endpoint;
- Tesla vehicle connection/TLS handling;
- decoding the official telemetry protocol;
- publishing every received valid record to Pub/Sub;
- health/metrics/logging.

Must not contain:

- Tesla OAuth refresh tokens;
- Tesla client secret;
- vehicle command private key;
- MCP auth logic;
- business/event semantics;
- historical filtering.

### 7.3 Telemetry Processor — Cloud Run

Owns:

- consume raw telemetry from Pub/Sub;
- resolve VIN -> internal vehicle -> user;
- append every valid raw event to that user's BigQuery dataset;
- preserve source and ingestion timestamps;
- handle unknown vehicles safely (quarantine/system diagnostics, never guess ownership);
- maintain minimal latest event-state required for transition detection;
- derive semantic events;
- deliver webhooks.

Raw BigQuery persistence is the highest-priority responsibility. Event derivation must not cause raw events to be discarded.

The Phase 7 handler returns success to Pub/Sub only after BigQuery accepts the
append. It deliberately supplies no BigQuery insert ID, so redeliveries remain
visible as distinct raw rows with their Tesla and Pub/Sub provenance. Before
event derivation is added, it remains a persistence-only boundary; future event
work must not be inserted before or into its acknowledgement decision.

---

## 8. Live vs historical query rule

This distinction is fundamental.

### Live/current questions

Use Fleet API through the MCP gateway.

Examples:

- Is the car locked?
- What is the current charge level?
- What is the cabin temperature?
- Where is the vehicle right now?
- What charge limit is set?

Avoid waking the car if a suitable recent state is already available and the user did not require exact realtime state, but do not use BigQuery as the normal current-state API.

### Historical questions

Use BigQuery.

Examples:

- What music played on the Colorado trip?
- How much energy did I consume while parked last month?
- Compare efficiency between two vehicles.
- What percentage of miles were self-driving?
- How often did I arrive home below 20% SOC?

The MCP should not need a bespoke endpoint for each historical question.

---

## 9. Analytics interface

The historical analysis interface is intentionally generic and read-only.

Required MCP tools:

```text
get_analytics_schema()
run_analytics_query(sql)
```

### 9.1 `get_analytics_schema`

Returns a compact description of the authenticated user's BigQuery dataset:

- available tables/views;
- columns/types;
- descriptions;
- suggested join keys;
- useful examples.

It must expose only the current user's analytical namespace.

### 9.2 `run_analytics_query`

Executes model-authored Standard SQL against the authenticated user's dataset.

Server rules:

- read-only `SELECT` / `WITH` only;
- set the authenticated user's dataset as `defaultDataset`;
- reject cross-project/cross-dataset table references;
- reject DML/DDL;
- reject `EXPORT DATA`;
- reject external queries/connections and remote functions;
- enforce timeout;
- enforce a sane result-row limit;
- use BigQuery dry-run before execution;
- use a conservative `maximumBytesBilled` safety ceiling to prevent accidental runaway queries, not as a user quota;
- record query metadata/cost for diagnostics.

Phase 9 implements this with a pinned SQLGlot BigQuery AST/scope validator and
executes the canonical AST rendering, not the original caller string. The
trusted static catalog contains the permanent raw table, complete pinned field
catalog, universal long-form observations, sparse exact-emission wide sample
views for charging/climate/driving/location/media, boundary-correct drive and
charge sessions, uncertainty-preserving FSD route segments/summaries, telemetry
capability diagnostics, and the state/session/daily derived views. The schema
tool intersects that catalog with objects actually
present in the user's dataset and never returns the physical dataset ID. See
`docs/data-and-analytics.md` for the concrete query/response bounds.

The safety ceiling is an implementation guardrail, not a commercial plan or per-user rate limit.

### 9.3 Per-user dataset isolation

Each approved platform user gets one BigQuery dataset, for example:

```text
tesla_u_<opaque_user_id>
```

All of that user's vehicles live in that dataset and are separated by `vehicle_id`.

The dataset ID is resolved server-side from the authenticated user. Never accept it as an MCP argument.

This provides simple isolation while still allowing cross-vehicle analytics for a user with several Teslas.

---

## 10. Telemetry retention philosophy

**Throttle at the source; preserve at the destination.**

Tesla Fleet Telemetry configuration controls which fields are transmitted and their `interval_seconds`. Tesla already performs change-based emission subject to the configured interval.

Once a valid telemetry message reaches our VM:

- publish it;
- persist it;
- do not thin it;
- do not frequency-sample it;
- do not discard it because an adjacent value looks redundant;
- do not expire it by default.

The raw store is append-oriented and indefinite.

Transport redeliveries may result in repeated raw rows. Preserve identifiers/timestamps so derived views can de-duplicate delivery retries when analytically appropriate. Do not use de-duplication as a hidden sampling mechanism.

See `docs/data-and-analytics.md` for schema details.

---

## 11. Telemetry configuration philosophy

The telemetry config is where collection volume is controlled.

Aim for **broad useful coverage**, including media/history fields, while using rational source intervals.

Do not continuously reproduce an extreme high-frequency profiler stream merely because storage is cheap; Tesla transmission itself has cost and vehicle-side constraints.

The exact config must be generated against Tesla's current available-data schema at implementation time.

Important categories include:

- battery / charge;
- climate;
- doors/locks/body;
- driving/powertrain;
- location/navigation;
- odometer/range;
- TPMS;
- software/configuration;
- alerts/connectivity;
- self-driving counters where available;
- media title/artist/album/station/source/status/duration/elapsed.

The complete received payload is retained.

---

## 12. Tesla API coverage philosophy

The Fleet API client should target **complete relevant personal-vehicle coverage**, not a handpicked subset.

`docs/fleet-api-coverage.md` is the coverage contract.

Rules:

- every endpoint marked **Required** must have a typed client implementation and tests;
- endpoints marked **MCP** must have a typed MCP tool or a documented grouped tool mapping;
- endpoints marked **Internal** are implemented but not directly exposed as arbitrary MCP operations;
- endpoints marked **Excluded** are intentionally not exposed because they are destructive/admin-sensitive;
- business-only and non-vehicle Energy endpoints are out of scope unless explicitly added later;
- deprecated/not-recommended endpoints may be compatibility-only.

Before implementing the Fleet client, re-audit Tesla's current official endpoint pages and update the matrix.

---

## 13. Vehicle command policy

Do not expose a generic arbitrary Fleet API command tool.

Use typed tools and risk classes.

### Read-only

May execute after platform authorization.

### Normal reversible controls

Examples:

- climate;
- charging;
- schedules;
- navigation;
- media;
- lock;
- lights/horn;
- Sentry;
- cabin-overheat settings.

Require clear user intent but do not invent redundant confirmations.

Before sending any MCP vehicle command, the gateway fetches the live vehicle
state. If the vehicle is not online, it records and sends one `wake_up`, polls
the vehicle endpoint every 10 seconds for at most 60 seconds, and sends the
requested command once only after the vehicle reports online. The gateway never
retries a command after sending it because a missing or 5xx response does not
prove non-execution. Read tools continue to avoid implicit wakes.

### Security-sensitive controls

Examples:

- unlock;
- trunk/frunk;
- windows;
- keyless driving/remote start;
- HomeLink;
- PIN/security controls;
- parental/speed-limit controls.

Require unambiguous current-turn intent. If the user's language is ambiguous, do not guess.

### Intentionally excluded from MCP initially

Examples:

- `erase_user_data`;
- administrator PIN-reset/clear operations where safer alternatives exist;
- arbitrary driver/share administration;
- partner-application administration.

Client support may still exist internally where useful.

---

## 14. Tesla application / Virtual Key model

There is one developer application identity and one application EC key pair initially.

- **private key:** backend only, Secret Manager, used by Vehicle Command Proxy;
- **public key:** permanently hosted at Tesla-required `.well-known` path;
- each approved user separately authorizes the developer app with Tesla OAuth;
- each vehicle that requires signed commands/telemetry must separately pair the public virtual key.

A user's Tesla account may contain multiple vehicles; pairing and telemetry setup are per vehicle.

Phase 6 mounts the private key only into the official Vehicle Command Proxy
sidecar. It is absent from the Python gateway container environment and
filesystem and remains absent from telemetry-edge. Its public half comes from
the separate `tesla-command-public-key` secret solely for the public
`.well-known` response.

If this project ever becomes a broad commercial service, revisit whether one application/private signing key remains an acceptable blast radius. Do not add that complexity for a few manually approved friends.

See `docs/tesla-onboarding.md`.

---

## 15. Secrets / mutable state

### Secret Manager

Use for:

- Tesla client secret;
- vehicle command EC private key;
- MCP authorization signing secrets if self-hosted;
- webhook HMAC keys.

### Firestore

Use for small mutable operational state:

- allowlist;
- bound OIDC identities;
- Tesla OAuth refresh-token state (encrypted/locked down);
- vehicle registry / ownership routing;
- telemetry configuration state/hash;
- event-state required for transition detection;
- webhook subscriptions.

Never put Tesla tokens in client/browser storage.

---

## 16. GCP deployment baseline

Default region:

```text
us-central1
```

Core resources:

- Cloud Run: `mcp-gateway`;
- Cloud Run: `telemetry-processor`;
- Compute Engine: tiny `e2-micro` telemetry VM;
- static external IPv4 for telemetry endpoint;
- four official-receiver Pub/Sub topics/subscriptions plus one isolated
  synthetic verification topic/subscription;
- Firestore;
- BigQuery user datasets;
- Secret Manager;
- Artifact Registry;
- Cloud Build;
- Cloud Logging/Monitoring.

Use Terraform for shared infrastructure.

Per-user resources created by the manual `add-user` admin workflow may be managed by a small idempotent admin script if that is simpler than re-running Terraform for every friend.

---

## 17. CI/CD

Repository merges are the source of deployment truth.

### PR

- tests;
- lint/type checks;
- container build;
- security/dependency checks;
- Terraform fmt/validate/plan;
- no production deploy.

### Merge to `main`

- build immutable image;
- push Artifact Registry image tagged by commit SHA;
- deploy only affected Cloud Run service;
- telemetry-edge changes cause VM to pull an exact image digest and restart;
- run smoke/health checks;
- record deployed commit/digest.

Never maintain production VM code by manual editing.

---

## 18. Event/webhook path

The raw history pipeline and webhook pipeline share the raw Pub/Sub feed but have different concerns.

A telemetry record should be persisted even if event processing fails.

Initial semantic events may include:

- `charging.started`;
- `charging.completed`;
- `vehicle.locked`;
- `vehicle.unlocked`;
- `drive.started`;
- `drive.ended`;
- `vehicle.online`;
- `vehicle.offline`;
- `geofence.arrived`;
- `geofence.departed`;
- SOC threshold crossings.

Webhooks are optional consumers of semantic events and should be HMAC-signed with bounded retries.

See `docs/event-and-webhooks.md`.

---

## 19. Key engineering invariants

1. **An unauthenticated or non-allowlisted caller cannot use the MCP.**
2. **A user can access only vehicles mapped to their platform user ID.**
3. **Tesla OAuth credentials are user-specific; application client credentials/key are application-level.**
4. **Multiple vehicles per Tesla account are supported everywhere.**
5. **Current state normally comes from Fleet API, not BigQuery.**
6. **History normally comes from BigQuery, not repeated Fleet API polling.**
7. **Every valid Fleet Telemetry record received is retained indefinitely.**
8. **Telemetry is throttled at Tesla configuration, never by downstream sampling.**
9. **The analytics SQL endpoint is read-only and cannot escape the authenticated user's dataset.**
10. **Vehicle commands are typed and risk-classified; no generic command passthrough MCP tool.**
11. **Raw telemetry persistence must not depend on semantic event success.**
12. **No public signup, plans, quotas, or SaaS billing logic unless explicitly added later.**
13. **`docs/fleet-api-coverage.md` is audited before declaring Fleet API implementation complete.**

---

## 20. Source-of-truth documents

Coding agents must use these together:

- `AGENTS.md` — standing engineering rules;
- `docs/architecture.md` — system boundaries and invariants;
- `docs/security-model.md` — allowlist, identity, ownership, secret rules;
- `docs/tesla-onboarding.md` — Tesla developer app/OAuth/Virtual Key setup;
- `docs/data-and-analytics.md` — BigQuery/raw retention/analytics contract;
- `docs/fleet-api-coverage.md` — endpoint completeness contract;
- `docs/event-and-webhooks.md` — realtime event model;
- `docs/implementation-roadmap.md` — phase order, goals, and exit criteria;
- `docs/prompt-pack.md` — copy/paste implementation prompts aligned to the roadmap.

The field guide is useful background but is **not required** for coding once these files are in the repository. If a prompt conflicts with the design documents, the design documents win and `docs/prompt-pack.md` should be corrected.
