# Implementation Roadmap

This document defines the implementation order and phase intent. The exact copy/paste prompts live in [`docs/prompt-pack.md`](prompt-pack.md).

The repository seed files are intended to make the Word field guide unnecessary during implementation. If a prompt conflicts with a source-of-truth design document, the design document wins and the prompt pack should be corrected.

Run the phases in order unless a phase is explicitly marked optional. Merge/review the previous phase before starting the next one.

---

## Phase 1 — Repository scaffold

**Goal:** Create the monorepo/code/CI skeleton without Tesla or production behavior.

**Prompt:** `docs/prompt-pack.md` → **Prompt 1 — Repository scaffold**

**Exit criteria:**

- repository layout matches `docs/architecture.md`;
- seed docs are preserved;
- lint/static/test/container/PR-CI skeletons run;
- no Tesla calls, real OAuth, telemetry networking, or secrets exist.

---

## Phase 2 — GCP / Terraform baseline

**Goal:** Provision the shared runtime/infrastructure foundation with least-privilege IAM.

**Prompt:** `docs/prompt-pack.md` → **Prompt 2 — GCP / Terraform baseline**

**Exit criteria:**

- Cloud Run placeholders, telemetry VM, Pub/Sub, Firestore, Secret Manager containers, Artifact Registry, monitoring, and quarantine/system telemetry destination exist in Terraform;
- per-user BigQuery datasets remain dynamically created by the manual user workflow;
- infrastructure validates/plans without Owner/Editor IAM.

---

## Phase 3 — Platform authentication and manual allowlist

**Goal:** Make the MCP private to manually approved people and establish the per-user data boundary.

**Prompt:** `docs/prompt-pack.md` → **Prompt 3 — Platform authentication and manual allowlist**

**Exit criteria:**

- verified allowlisted identity binds to immutable OIDC issuer/subject;
- disabled/non-allowlisted users are rejected;
- add-user creates the user's opaque BigQuery dataset with no default expiration and required service access;
- cross-user access tests pass.

---

## Phase 4 — Tesla developer app onboarding, OAuth, and Virtual Key

**Goal:** Let each approved platform user authorize their own Tesla account and pair each vehicle safely.

**Prompt:** `docs/prompt-pack.md` → **Prompt 4 — Tesla developer app onboarding, OAuth, and Virtual Key**

**Exit criteria:**

- `.well-known` public key endpoint and partner registration flow exist;
- per-user Tesla OAuth/token rotation exists;
- multiple vehicles are enumerated and registered;
- Virtual Key pairing/status is vehicle-specific;
- no broad command surface is enabled yet.

---

## Phase 5 — Complete typed Fleet API client

**Goal:** Implement complete relevant Fleet API coverage as defined by the current coverage matrix.

**Prompt:** `docs/prompt-pack.md` → **Prompt 5 — Complete typed Fleet API client**

**Exit criteria:**

- Tesla docs and coverage matrix were re-audited;
- every Implementation=Required matrix row has typed client support;
- compatibility behavior follows the matrix;
- coverage tests prevent silent omissions;
- no generic Tesla MCP passthrough exists.

---

## Phase 6 — Vehicle Command Proxy and complete live MCP surface

**Goal:** Expose the approved live/current reads and controls through typed MCP tools with safe command signing.

**Implementation status:** Complete in code; deployment and the documented
first live MCP checkpoint remain operator actions after merge.

**Prompt:** `docs/prompt-pack.md` → **Prompt 6 — Vehicle Command Proxy and complete live MCP surface**

**Exit criteria:**

- all Exposure=MCP matrix rows are mapped to typed MCP tools/grouped tools;
- current state uses Fleet API;
- multiple-vehicle ambiguity and ownership are enforced;
- write commands are audited and risk-classified;
- private command key remains backend-only.

---

## Phase 6.1 — ChatGPT OAuth compatibility and browser onboarding

**Goal:** Make the completed MCP surface linkable from ChatGPT and make the
manual allowlist lead to a guided, per-user Tesla/Virtual Key onboarding page.

**Implementation status:** Implemented in code; Auth0/Google configuration,
deployment, existing-owner identity migration, and the first ChatGPT connection
remain operator checkpoint actions.

**Exit criteria:**

- `/mcp` is served by the official MCP SDK and publishes the current OAuth
  protected-resource metadata plus Pydantic-generated semantic tool schemas;
- access tokens are validated for issuer, MCP audience, expiry, and scope;
- ChatGPT receives rotating, expiring refresh-token access so routine access-token
  expiry does not force interactive relinking;
- `add-user` remains the only enrollment path;
- `/onboarding` binds only verified allowlisted identities and stores opaque sessions;
- Tesla OAuth and per-vehicle Virtual Key pairing are guided without exposing credentials;
- multiple vehicles and partial pairing are represented independently.

---

## Phase 7 — Fleet Telemetry edge and permanent raw BigQuery history

**Goal:** Receive Fleet Telemetry and permanently retain every valid received observation.

**Status:** Receiver/storage operator checkpoint complete; unattended certificate
renewal enablement pending before real-vehicle configuration.
No real vehicle Fleet Telemetry configuration is applied in this phase.

**Prompt:** `docs/prompt-pack.md` → **Prompt 7 — Fleet Telemetry edge and permanent raw BigQuery history**

**Exit criteria:**

- telemetry-edge accepts/decodes/publishes valid records to Pub/Sub;
- telemetry-processor routes by trusted vehicle ownership and appends raw history;
- no downstream sampling, thinning, intentional raw de-duplication, or default expiration exists;
- source/ingestion timestamps and transport/config metadata are preserved;
- unknown VINs are quarantined rather than guessed;
- VM deployment/rollback and TLS health are operational.
- public certificate renewal is scheduled, atomic, monitored, and verified
  without placing DNS credentials on telemetry-edge;
- production records are bound to Tesla mTLS identity and exact fleet
  subscriptions, while synthetic traffic cannot enter a user dataset.

---

## Phase 8 — Cost-conscious Fleet Telemetry configuration

Implementation status: `broad-v4` is implemented declaratively from the
operator-supplied 93-field Tessie baseline with 19 reasoned overrides, 40
additions, and 2 removals. The resulting 131-field passenger-vehicle profile
explicitly accounts for all 239 catalog fields, uses opt-in deltas,
capability-projects synchronized speed/acceleration, media-volume context,
drive-boundary, charge-boundary, and FSD-pair payloads from live `fleet_status`
capability for Fleet Telemetry 1.3+, provides safe exact
Tessie/current/desired diff, signed
apply/verify/repair/remove, error inspection, audit, separate trust hashes, and
a canary-first opted-in transport reconciler. The first real vehicle remains
behind the required operator checkpoint in
[`fleet-telemetry-configuration.md`](fleet-telemetry-configuration.md); CI never
applies configuration.

**Goal:** Configure each vehicle with a useful Tessie-derived telemetry set at rational source intervals and measured cost.

**Prompt:** `docs/prompt-pack.md` → **Prompt 8 — Cost-conscious Fleet Telemetry configuration**

**Exit criteria:**

- current Tesla available-data definitions were reviewed;
- field coverage includes selected media and other analytically useful signals;
- intentional omissions are documented;
- baseline/override/addition/removal decisions are validated declaratively;
- actual Fleet API usage is checked against the developer-discount target;
- inspect/diff/apply/verify/remove/repair works per vehicle;
- telemetry field/interval config and server trust-profile versions/hashes are
  persisted separately;
- compatible server leaf renewal requires no vehicle reconfiguration;
- each candidate certificate is checked against the exact configured vehicle
  CA trust profile before activation;
- hostname/port/CA migrations have canary, sync-gated, per-vehicle
  reconciliation without giving Tesla credentials to telemetry-edge or the
  certificate-renewal job;
- automatic transport-trust maintenance requires explicit per-vehicle opt-in,
  while first enrollment and field/frequency changes remain explicit;
- CI never changes a real vehicle config automatically.

---

## Phase 9 — Generic historical analytics MCP

**Goal:** Make permanent history broadly queryable without building one-off analytical endpoints.

**Status:** Implemented in code, including boundary-correct drives, authoritative
charge sessions, uncertainty-preserving FSD route segments/summaries, and
telemetry capability diagnostics. Source-defined views are reconciled for every
active user's isolated dataset after relevant main merges, with `add-user` using
the same idempotent implementation; live analytics verification remains an
operator checkpoint.

**Prompt:** `docs/prompt-pack.md` → **Prompt 9 — Generic historical analytics MCP**

**Exit criteria:**

- `get_analytics_schema` and `run_analytics_query` work;
- derived analytical views exist for core history concepts;
- relevant main merges create/update the full managed view set for every active
  user and remove only stale, explicitly labeled managed views;
- SQL is read-only and cannot escape the authenticated user's dataset;
- max-bytes is only an accidental-query safety guardrail, not a quota product;
- trip-playlist/cross-vehicle examples work without dedicated endpoints.

---

## Phase 10 — Optional semantic events and webhooks

**Goal:** Add realtime semantic events/webhooks without coupling them to permanent history.

**Status:** Optional. This phase may be deferred; core live control, telemetry history, and analytics do not depend on it.

**Prompt:** `docs/prompt-pack.md` → **Prompt 10 — Optional semantic events and webhooks**

**Exit criteria when implemented:**

- raw BigQuery persistence is independent of event/webhook success;
- buffered old telemetry cannot cause stale realtime actions;
- semantic events are replay/idempotency safe;
- webhooks are user-scoped, HMAC-signed, retry/dead-lettered, and SSRF-hardened.

---

## Phase 11 — Hardening, operations, and final acceptance

**Goal:** Re-audit the live Tesla API, verify all security/operational invariants, and make the system supportable.

**Prompt:** `docs/prompt-pack.md` → **Prompt 11 — Hardening, operations, and final acceptance**

**Exit criteria:**

- current Tesla docs and endpoint coverage were re-audited;
- least-privilege IAM, secret/token handling, user/vehicle isolation, SQL sandboxing, telemetry permanence, command safety, and deployment rollback were reviewed/tested;
- required runbooks are complete;
- real-vehicle smoke testing remains an explicit manual procedure;
- known risks/deferred optional items are documented.

---

## Ongoing rule after Phase 11

New features should begin as normal scoped PRs, not new platform phases.

For historical questions, prefer generic analytics SQL over adding one-off MCP endpoints.

For new Tesla API endpoints, update `docs/fleet-api-coverage.md` first.
