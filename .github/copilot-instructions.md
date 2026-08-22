# GitHub Copilot Review Instructions

When reviewing pull requests in this repository, act as an independent security, correctness, and architecture reviewer.

Read `AGENTS.md` and the authoritative Markdown files under `docs/` before evaluating a change. Treat those files as the project source of truth. A passing test suite is necessary but is not evidence that the design is secure.

## Review priorities

Pay particular attention to:

- authentication or authorization bypasses;
- failure to enforce the manual approved-user allowlist;
- cross-user data exposure;
- cross-vehicle access or commands;
- trusting caller-supplied user IDs, VINs, vehicle IDs, Tesla account IDs, dataset names, or other ownership identifiers without resolving ownership from trusted server-side state;
- incorrect assumptions that a Tesla account has only one vehicle;
- improper Tesla OAuth access-token or refresh-token handling, including refresh-token rotation and persistence;
- exposure or over-broad access to the Tesla command-signing private key;
- secrets, credentials, authorization codes, tokens, or private keys appearing in source, logs, CI output, container layers, Terraform output/state, or build artifacts;
- overly broad Google Cloud IAM permissions;
- accidental public exposure of internal services;
- insecure Cloud Run, Compute Engine, Pub/Sub, BigQuery, Firestore, Secret Manager, or Artifact Registry configuration;
- unsafe Tesla command execution or insufficient current-request authorization;
- destructive or intentionally excluded Tesla Fleet API operations becoming externally exposed;
- gaps between `docs/fleet-api-coverage.md` and the implemented typed Tesla client or MCP surface;
- SQL injection or unsafe construction of BigQuery queries;
- historical analytics escaping the authenticated user's permitted BigQuery dataset;
- BigQuery DDL/DML, export, external-query, remote-function, or other write/escape capabilities being reachable through the read-only analytics interface;
- telemetry from one vehicle or user being attributed to another;
- downstream telemetry thinning, sampling, or frequency-based deduplication that violates the documented permanent raw-history policy;
- loss or mutation of source timestamps or other information needed to reconstruct historical telemetry faithfully;
- SSRF or unsafe outbound HTTP behavior;
- insecure webhook signing, destination validation, retry, or replay handling;
- replay, concurrency, race-condition, or idempotency defects;
- missing validation at trust boundaries;
- unnecessary vehicle wake-ups or unexpectedly expensive Fleet API behavior;
- new dependencies or infrastructure that materially expand the attack surface;
- deviations from the documented architecture, security model, onboarding flow, data model, or implementation roadmap.

## Trust model

Assume all caller-controlled input may be malicious, malformed, stale, or intentionally misleading.

Authorization must be enforced server-side.

Never treat possession of a VIN, Tesla vehicle ID, internal vehicle ID, user ID, email address, dataset name, or other identifier as proof of authorization.

Before executing a vehicle command, the implementation must resolve and verify from trusted server-side state that:

1. the platform identity is authenticated and currently approved;
2. the requested Tesla connection belongs to that approved user;
3. the target vehicle belongs to that Tesla connection/user; and
4. the requested operation is allowed by the documented MCP and Fleet API policy.

Historical analytics must never allow one approved user to access another approved user's BigQuery data.

The Tesla command-signing private key is a high-impact secret. Flag any design that exposes it to more runtime identities or services than strictly necessary.

## Review behavior

Do not manufacture findings merely to produce review comments.

Prioritize security, correctness, data isolation, and architectural violations over formatting or stylistic preferences.

For every meaningful issue, provide:

1. severity;
2. affected file and line(s);
3. the concrete failure, exploit scenario, or operational consequence;
4. why the current implementation permits it; and
5. a recommended remediation.

If an architectural or security question cannot be proven from the diff alone, say what assumption needs verification rather than silently assuming it is safe.

Explicitly identify blocking findings. If no blocking issue is found, say so.
