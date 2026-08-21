# Codex Prompt Pack

This file is the copy/paste prompt sequence for building the Tesla Personal Platform from the repository seed.

The architectural source of truth remains the standing project documentation:

- `AGENTS.md`
- `docs/architecture.md`
- `docs/security-model.md`
- `docs/tesla-onboarding.md`
- `docs/data-and-analytics.md`
- `docs/fleet-api-coverage.md`
- `docs/event-and-webhooks.md`
- `docs/implementation-roadmap.md`

If a prompt in this file ever conflicts with one of those documents, **the design/source-of-truth document wins**. Fix this prompt pack to match rather than changing the architecture implicitly.

The Word field guide is background material for the human operator. Coding agents should not require it.

---

## How to use this pack

1. Put the seed files in the repository before running Prompt 1.
2. Run the prompts in order.
3. Merge/review the prior phase before starting the next phase.
4. Do not paste Tesla secrets, OAuth tokens, private keys, service-account keys, or PINs into an agent prompt.
5. For Tesla-specific phases, the agent must re-check current official Tesla developer documentation rather than relying only on the dated seed snapshot.
6. Automated tests must never issue commands to a real vehicle or automatically alter a real vehicle's telemetry configuration.
7. If a phase reaches a step that requires a real Tesla login, real key pairing, production secret value, or real command, implement the code/runbook and stop at a clearly documented manual validation step unless explicitly instructed otherwise.
8. Keep changes reviewable. A phase may use multiple commits, but it should not silently expand into unrelated platform work.

Core implementation phases are 1-9 and 11. Phase 10 (semantic events/webhooks) is planned but optional; it can be deferred without compromising live vehicle access, permanent telemetry history, or generic analytics.

---

## Prompt 1 — Repository scaffold

```text
Read AGENTS.md and every existing file under docs/ before making changes. Treat the repository Markdown as the source of truth and preserve every seed document, including docs/prompt-pack.md. Scaffold the tesla-personal-platform monorepo exactly around the service/package boundaries in docs/architecture.md.

Create the documented services, packages, infra/terraform, scripts/admin, scripts/dev, docs/deployment.md, docs/mcp-tool-catalog.md placeholder, and docs/runbooks placeholders. Add a concise README that links to the source-of-truth documents and explains the intended phase sequence. Add linting, formatting, type/static checks as appropriate for the chosen implementation stack, unit-test foundations, container skeletons, and Cloud Build PR validation configuration.

Do not implement Tesla API behavior, platform OAuth, Tesla OAuth, real vehicle commands, Fleet Telemetry networking, BigQuery ingestion, or production infrastructure in this phase. Do not add secrets. Do not replace the existing architecture with generated boilerplate. If the implementation language/runtime is not already established, choose the smallest practical stack that fits the documented components, record the choice and rationale in the README/deployment notes, and avoid adding languages/frameworks without a concrete need.

Finish with a PR-style summary listing the repository structure created, validation commands run, assumptions, deviations from the docs, and any decisions that should be revisited before Phase 2.
```

### Phase 1 completion check

- Seed docs preserved.
- Repository structure matches `docs/architecture.md`.
- Local/CI validation skeleton works.
- No real Tesla or GCP behavior exists yet.
- No secrets exist in source or fixtures.

---

## Prompt 2 — GCP / Terraform baseline

```text
Read AGENTS.md, docs/architecture.md, docs/security-model.md, docs/data-and-analytics.md, docs/implementation-roadmap.md, and the existing repository before changing infrastructure.

Implement the shared GCP baseline in Terraform, targeting us-central1 unless a documented reason requires otherwise. Create narrowly scoped service accounts, Artifact Registry, Cloud Run placeholders for mcp-gateway and telemetry-processor, Pub/Sub raw telemetry topic/subscription, Firestore, Secret Manager secret containers without secret values, Cloud Logging/Monitoring basics, and one small e2-micro Compute Engine telemetry VM with a reserved public IPv4 and only the firewall exposure required for Fleet Telemetry plus secure administration. Configure Cloud Build permissions for PR validation and later deployment without granting Owner or Editor.

Provision the shared BigQuery/project-level prerequisites and a restricted system/quarantine destination for telemetry whose vehicle ownership cannot be resolved. Do not statically create per-user BigQuery datasets in Terraform; those are created idempotently by the manual add-user workflow so adding a friend does not require a Terraform rollout. Ensure the architecture supports granting the telemetry processor write access and the MCP gateway read/query access to each newly created user dataset.

Do not implement Tesla behavior yet. Do not put real secret values in Terraform, tfvars, build substitutions, state examples, or documentation. Add/update docs/deployment.md with the resource map, IAM intent, environment/bootstrap steps, and deployment flow. Run terraform fmt, validate, and a non-destructive plan where possible, and summarize the plan and any IAM tradeoffs.
```

### Phase 2 completion check

- Shared GCP foundation is reproducible.
- Per-user datasets are intentionally dynamic, not hard-coded in Terraform.
- Telemetry VM has no command/OAuth secrets.
- IAM is least-privilege oriented.

---

## Prompt 3 — Platform authentication and manual allowlist

```text
Read AGENTS.md, docs/security-model.md, docs/architecture.md, docs/data-and-analytics.md, and the existing repository.

Implement the simple platform authentication and manual allowlist model. Reuse the proven YNAB MCP authorization pattern where practical. Use a mainstream OIDC provider, with Google OIDC as the practical default if no equivalent existing pattern is already established. A verified allowlisted email is only the bootstrap invitation key; on first approved login bind the immutable OIDC issuer+subject to a random internal user_id, and authorize subsequent requests from that immutable binding rather than the email string.

Implement idempotent admin commands such as scripts/admin/add-user and scripts/admin/disable-user. add-user must create/update the Firestore allowlist record, create the user's opaque BigQuery dataset with no default expiration, and configure the dataset access needed by mcp-gateway for read/query and telemetry-processor for append/write. The MCP server must resolve user context server-side and reject caller-supplied user IDs, dataset IDs, and ownership claims.

Do not add public signup, organizations, memberships, plans, billing, usage quotas, or SaaS-style rate-limit product logic. Add tests for non-allowlisted login, unverified email, first-login immutable binding, later email changes, issuer/subject mismatch, disabled users, and attempts to cross user boundaries. Document the manual 'add Homer' workflow.
```

### Phase 3 completion check

- Only manually approved identities can enter the platform.
- Immutable OIDC identity is bound after the first approved login.
- Each user gets one isolated BigQuery dataset with no expiration.
- Disabling a user immediately blocks platform access.

---

## Prompt 4 — Tesla developer app onboarding, OAuth, and Virtual Key

```text
Read AGENTS.md, docs/tesla-onboarding.md, docs/security-model.md, docs/fleet-api-coverage.md, and the existing repository. Before coding, re-check Tesla's current official authentication, partner-registration, OAuth scope, token-refresh, Virtual Key, and vehicle-list documentation. Update the repository docs if Tesla behavior has changed; do not silently code against stale assumptions.

Implement Tesla onboarding in mcp-gateway: the required public .well-known application public-key endpoint; an idempotent admin partner-registration/verification command; per-user Tesla OAuth start/callback; state and any currently required/recommended PKCE/nonce protections; secure refresh-token rotation and persistence; region/base-URL discovery; and vehicle enumeration/registry updates.

Support multiple vehicles per Tesla account from the first implementation. Each discovered vehicle gets an internal vehicle_id owned by the authenticated platform user. Implement vehicle-specific Virtual Key pairing links/status and fleet-status verification. Pairing remains a user-in-the-loop Tesla action; do not pretend OAuth automatically pairs every vehicle. Do not configure broad Fleet Telemetry yet and do not expose broad vehicle commands yet.

Tesla client secret and the application EC private signing key must come only from Secret Manager/runtime secret injection and must never be logged or returned to MCP clients. Add comprehensive mocks/tests for callback user binding, token rotation, multiple vehicles, partial pairing, revoked/expired auth, and cross-user vehicle isolation. CI must not contact a real Tesla account or vehicle.
```

### Phase 4 completion check

- Developer app registration/key-hosting path exists.
- Each approved user can independently authorize their Tesla account.
- Multiple vehicles are discovered and stored correctly.
- Virtual Key state is tracked per vehicle.

---

## Prompt 5 — Complete typed Fleet API client

```text
Read AGENTS.md and docs/fleet-api-coverage.md. First re-audit Tesla's current official Vehicle Endpoints, Vehicle Commands, User Endpoints, Partner Endpoints, and Charging Endpoints. Update docs/fleet-api-coverage.md and its audit date before implementation if anything has changed.

Build the typed Tesla Fleet API client package. Implement every coverage-matrix row whose Implementation column is Required, regardless of whether its Exposure is MCP, Internal, or Excluded. Implement Compatibility rows only as directed by the matrix. This includes complete relevant personal-vehicle command coverage, not just common lock/climate/charging operations.

Implement Tesla regional base-URL handling, per-user OAuth token selection/refresh integration, typed request/response models, targeted vehicle-data reads, wake-awareness, conservative retries only where safe, request/response redaction, and normalized error categories. Do not expose a generic call_tesla_api(method, path, body) MCP operation.

Add endpoint-level mocked tests and a machine-checkable coverage test that parses or otherwise validates docs/fleet-api-coverage.md so the phase cannot be declared complete while any Required row lacks a typed client implementation. No automated test may call a real vehicle.
```

### Phase 5 completion check

- Coverage matrix is current as of the implementation date.
- Every Required row has typed client support.
- Coverage test prevents silent endpoint omissions.
- Generic Tesla passthrough does not exist.

---

## Prompt 6 — Vehicle Command Proxy and complete live MCP surface

```text
Read AGENTS.md, docs/architecture.md, docs/security-model.md, docs/fleet-api-coverage.md, and the existing typed Fleet API client.

Integrate Tesla's official Vehicle Command Proxy using the simplest supported private deployment model for this architecture. Keep the application EC private key backend-only and expose the proxy only to the minimum trusted runtime. Do not place the private key on telemetry-edge.

Expose every coverage-matrix operation marked Exposure=MCP through typed MCP tools or a clearly documented typed/grouped tool mapping. Current-state questions must use Fleet API through the gateway rather than BigQuery. Every tool must derive the authenticated user internally, resolve only vehicles owned by that user, support multiple vehicles, and auto-select a vehicle only when exactly one eligible vehicle exists; otherwise return a clear ambiguity rather than guessing.

For every tool document and enforce required Tesla scope, wake behavior, read/write status, risk class, retry/idempotency behavior, and audit behavior. Record a redacted command audit for every attempted write, success or failure. Security-sensitive operations require unambiguous current-turn user intent. Keep operations marked Excluded out of MCP even if the typed client supports them internally.

Create/refine docs/mcp-tool-catalog.md and add tests proving MCP coverage of every matrix row marked MCP, cross-user vehicle rejection, multi-vehicle ambiguity handling, risk classification, command auditing, and no secret leakage. Automated tests must not issue real vehicle commands.
```

### Phase 6 completion check

- Live/current Fleet API reads and approved controls are available through typed MCP tools.
- Every MCP-marked coverage row is mapped and tested.
- Vehicle ownership and multi-vehicle ambiguity are enforced centrally.
- Private signing key remains backend-only.

---

## Prompt 7 — Fleet Telemetry edge and permanent raw BigQuery history

```text
Read AGENTS.md, docs/architecture.md, docs/tesla-onboarding.md, docs/data-and-analytics.md, and the current official Tesla fleet-telemetry receiver/protocol documentation.

Implement services/telemetry-edge on the Compute Engine VM using a pinned/current compatible Tesla fleet-telemetry receiver plus only the minimal adapter needed to publish decoded records to Pub/Sub. The VM's job is intentionally boring: accept authenticated Tesla telemetry connections, decode valid records, preserve transport/source metadata, publish every valid received record to the raw Pub/Sub topic, expose health/metrics, and nothing more. It must not possess Tesla OAuth refresh tokens, Tesla client secret, the command-signing private key, MCP authentication logic, or analytics/event business rules.

Implement services/telemetry-processor to consume the raw Pub/Sub stream, resolve VIN/Tesla vehicle identity through the trusted vehicle registry, derive the owning internal user_id and server-side BigQuery dataset, and append every valid received observation to that user's raw_telemetry_events history. Preserve source timestamp, ingestion timestamp, internal vehicle_id, VIN/Tesla identifiers needed for provenance, complete decoded payload, telemetry config version/hash, receiver/client version where available, and transport/message identifiers.

The retention invariant is: throttle at the Tesla source; preserve at the destination. Do not time-sample, thin, value-filter, or intentionally deduplicate raw history after receipt. Exact transport redeliveries may therefore appear more than once in the raw table; preserve message IDs/metadata so derived analytics can distinguish or de-duplicate retry deliveries later without rewriting raw provenance. Configure no default table/dataset expiration. Partition/cluster the raw table according to docs/data-and-analytics.md.

Acknowledge Pub/Sub only after the raw record has been durably accepted according to the chosen BigQuery write path. If a VIN cannot be mapped to an approved user's vehicle, never guess ownership and never write it into a user dataset; route it to the restricted quarantine/system path and surface diagnostics.

Implement TLS/certificate handling, structured health metrics, immutable image deployment to the VM by exact digest, health-checked restart, and rollback. Add tests for multiple users, multiple vehicles, buffered source timestamps, intentional preservation of duplicate deliveries, unknown VIN quarantine, transient Pub/Sub/BigQuery failures, and no downstream filtering.
```

### Phase 7 completion check

- Every valid received telemetry observation is durably retained indefinitely.
- Pub/Sub is a durability boundary, not a filter.
- Raw duplicates are preserved rather than silently collapsed.
- Unknown vehicles cannot leak into a user's history.

---

## Prompt 8 — Broad Fleet Telemetry configuration

```text
Read AGENTS.md, docs/tesla-onboarding.md, docs/data-and-analytics.md, and Tesla's current Fleet Telemetry available-data documentation/source schema before implementation.

Implement a versioned broad Fleet Telemetry configuration for each eligible vehicle. The default philosophy is broad inclusion rather than minimizing warehouse storage: subscribe to essentially all practically useful supported vehicle telemetry available to the application, including media metadata/playback, location/navigation, driving/powertrain, battery/charging, climate, body/security, TPMS, software/configuration, connectivity/alerts, and self-driving/FSD counters where actually supported. Explicitly document any available fields intentionally omitted and why.

Use rational per-field interval_seconds/change behavior at the Tesla source; do not ask for maximum-frequency sampling everywhere when the signal does not justify it. This source configuration is the only intentional telemetry-frequency throttling layer. Once a record is emitted to our receiver, Phase 7's permanent-storage rule remains unchanged.

Implement per-vehicle inspect, desired-vs-current diff, apply, verify, remove, repair/reapply, telemetry-error inspection, and config hash/version persistence through Tesla's currently recommended signed configuration path. Multiple vehicles on the same Tesla account must be configurable independently, and failure on one vehicle must not block the others. Do not implement plan-based telemetry profiles or downstream sampling.

Add tests using current schema fixtures/mocks. CI must never automatically apply or remove a real vehicle telemetry configuration. Provide a manual validation/runbook step for the first real vehicle.
```

### Phase 8 completion check

- Broad telemetry coverage is deliberate and documented.
- Frequency decisions live only in Tesla config.
- Config can be inspected/diffed/repaired per vehicle.
- Real config changes require explicit manual execution outside CI.

---

## Prompt 9 — Generic historical analytics MCP

```text
Read AGENTS.md, docs/architecture.md, docs/data-and-analytics.md, docs/security-model.md, and the existing raw telemetry schema.

Implement the BigQuery historical analytical layer and the two general MCP tools get_analytics_schema and run_analytics_query. Do not create one-off history endpoints for questions that can be answered by model-authored SQL.

Create/refine derived analytical views or rebuildable tables for at least drives, charge_sessions, media_history, vehicle_state_changes, and useful daily/efficiency summaries. Keep raw_telemetry_events permanent and authoritative. If semantic_events has not yet been implemented because the optional event/webhook phase has not run, do not invent fake event data; integrate that view later when Phase 10 exists.

get_analytics_schema must return the authenticated user's available tables/views, fields/types/descriptions, join keys, partitioning hints, and useful examples without exposing another user's namespace.

run_analytics_query must derive the authenticated user's dataset server-side and never accept a caller-selected dataset/project. Allow one read-only Standard SQL SELECT/WITH statement only. Use a real parser/AST or equivalently robust validation to reject explicit cross-project/dataset references, DML, DDL, EXPORT DATA, external queries/connections, remote functions, scripting, and other escape paths. Set the user's dataset as BigQuery defaultDataset, dry-run first, enforce bounded timeout/result size and a reasonable maximumBytesBilled safety ceiling, then execute and return bounded results. The byte ceiling is protection against accidental runaway SQL, not a per-user quota system.

Log query job metadata such as user_id, bytes processed, duration, and referenced in-scope objects without logging sensitive result rows into general logs. Add tests proving cross-user/cross-dataset access is impossible, invalid SQL is rejected, multiple vehicles in one user's dataset can be compared, and the model can reconstruct a road-trip playlist from drive/location plus media_history without a dedicated playlist endpoint.
```

### Phase 9 completion check

- Arbitrary historical questions can be answered with schema inspection + safe model-written SQL.
- Raw telemetry remains permanent and rebuildable.
- Cross-user SQL escape is blocked.
- Current-state questions still use Fleet API, not BigQuery.

---

## Prompt 10 — Optional semantic events and webhooks

```text
Read AGENTS.md, docs/architecture.md, docs/event-and-webhooks.md, docs/data-and-analytics.md, and the existing telemetry-processor implementation.

Implement the optional semantic event/webhook branch without weakening or coupling the permanent raw-history path. Extend telemetry-processor only with the minimal per-vehicle state required to derive the documented initial semantic events. Raw telemetry persistence must succeed independently of event derivation or webhook delivery.

Use source timestamps for event chronology and an explicit actionability window so delayed/buffered historical telemetry can still update BigQuery and derived history without firing stale realtime actions. Make semantic event generation idempotent/replay-safe across duplicate Pub/Sub delivery and service restarts.

Implement authenticated per-user webhook subscriptions for semantic events, HMAC-SHA256 signing, event IDs/timestamps, bounded retry with dead-lettering, replay/test by event ID where appropriate, and SSRF defenses covering DNS/IP resolution, redirects, loopback/private/link-local/metadata targets, and re-resolution. A user may subscribe only to events from vehicles they own. Do not add per-user webhook quotas or a generalized SaaS plan system.

Expose semantic_events to the analytical schema once real event data exists. Add tests for duplicate delivery, stale buffered events, cross-user isolation, signatures, retry/dead-letter behavior, replay safety, and SSRF blocks.
```

### Phase 10 completion check

- Event/webhook failure cannot lose raw history.
- Buffered old telemetry cannot trigger stale realtime actions.
- Webhooks are signed, scoped, retry-safe, and SSRF-hardened.

---

## Prompt 11 — Hardening, operations, and final acceptance

```text
Read AGENTS.md and every source-of-truth document under docs/. Perform a final implementation, security, and operations review of the platform as actually built. Do not use this phase to introduce unrelated product features or SaaS complexity.

Re-audit Tesla's current Vehicle Endpoints, Vehicle Commands, User Endpoints, Partner Endpoints, Charging Endpoints, authentication, Virtual Key, and Fleet Telemetry documentation. Update docs/fleet-api-coverage.md and onboarding/telemetry docs if Tesla has changed. Verify the typed client and MCP coverage tests still satisfy every Required/MCP contract.

Review and test least-privilege IAM, Secret Manager use, OIDC allowlist binding, disabled-user behavior, Tesla refresh-token rotation/reconnect, per-user Tesla token isolation, multiple-vehicle resolution, Virtual Key status, command-signing private-key blast radius, command intent/risk policy, command audit redaction, VM TLS/certificate renewal, vehicle-to-user telemetry routing, permanent raw telemetry retention, Pub/Sub failure behavior, BigQuery dataset isolation, analytics SQL sandboxing, deployment rollback, telemetry-config repair, unknown-VIN quarantine, logging/monitoring, and Tesla/platform revocation.

If Phase 10 is implemented, also review event idempotency, stale-event actionability, webhook signing/replay, retries/dead-lettering, and SSRF protections. If Phase 10 was intentionally deferred, record that as a planned optional feature rather than a core acceptance failure.

Complete all runbooks referenced by docs/architecture.md, including OAuth recovery, telemetry repair, certificate renewal, command-key rotation, rollback, emergency revocation, and safe first-live validation. Provide explicit manual smoke-test steps for a real approved Tesla account/vehicle; automated CI must remain safe by default and must not send real commands or alter real telemetry configuration.

Finish with a final acceptance checklist, exact validation/test commands and results, current Fleet API coverage status, deployment/readiness summary, and a short list of known residual risks or deferred items.
```

### Phase 11 completion check

- Endpoint coverage has been re-audited.
- Security and isolation invariants have dedicated tests/checks.
- Operational recovery is documented.
- Real-vehicle validation is explicit/manual, not hidden in CI.
- Deferred optional work is clearly identified.

---

## After the prompt pack

After Phase 11, normal work should be done as scoped feature PRs rather than extending the platform with more numbered phases.

For a new Tesla API endpoint:

1. update `docs/fleet-api-coverage.md` first;
2. implement the typed client;
3. decide MCP/Internal/Excluded exposure deliberately;
4. add tests and tool-catalog documentation.

For a new historical question, first try:

```text
get_analytics_schema
run_analytics_query
```

Do not create a dedicated MCP history endpoint merely because the question is novel.
