# Admin scripts

Administrative entry points are trusted, explicit operator workflows.

- Phase 2: infrastructure/bootstrap helpers only if Terraform cannot express the operation cleanly.
- Phase 3: idempotent `add-user` and `disable-user` workflows (implemented).
- Phase 6.1: guarded `reset-user-identity` for provider migration/recovery.
- Phase 4: idempotent Tesla partner registration/verification (implemented as
  `register-partner`).
- Phase 7: guarded synthetic telemetry validation (implemented).
- Phase 8: explicit per-vehicle configuration operations (implemented through
  the authenticated `/onboarding` operator screen so no local admin process can
  read Tesla tokens or the command-signing key).

Run Phase 3 user commands from the repository root after creating ADC for the
keyless `tpp-user-admin` service account:

```bash
uv run python scripts/admin/add-user --project-id woodhouse-506215 --email homer@example.com
uv run python scripts/admin/disable-user --project-id woodhouse-506215 --email homer@example.com
```

After `add-user`, the user can visit
`https://woodhouse.derekjass.com/onboarding` to sign in, authorize Tesla, and
pair each vehicle. An account already bound to the legacy direct-Google issuer
must be reset exactly once before its first Auth0-brokered login:

```bash
uv run python scripts/admin/reset-user-identity \
  --project-id woodhouse-506215 \
  --email homer@example.com \
  --confirm-user-id usr_REPLACE_WITH_EXISTING_ID
```

The confirmation protects against resetting the wrong tenant. The operation
preserves dataset, vehicle, Tesla connection, and historical data.

After Phase 9 is deployed, re-run `add-user` once for every existing account.
The same idempotent command creates/repairs that user's non-expiring,
source-time-partitioned `raw_telemetry_events` table and all rebuildable
analytical views without deleting data.

The commands never accept or print a token, secret, service-account key, or
Tesla credential. See [`docs/deployment.md`](../../docs/deployment.md#manual-add-homer-workflow)
for permissions, impersonation, dataset access, and recovery behavior.

Prepare (but do not automatically upload) the Phase 8 CA-only vehicle trust
profile from the live public chain:

```bash
uv run python scripts/admin/prepare-telemetry-trust-profile \
  --hostname telemetry.woodhouse.derekjass.com \
  --profile-id lets-encrypt-current-2026-08 \
  --output /tmp/woodhouse-telemetry-ca-profile.pem
```

It drops the expiring leaf and refuses to overwrite an existing path. Follow
the inspection, Secret Manager, Terraform, exact-diff, and first-vehicle steps
in [`docs/deployment.md`](../../docs/deployment.md#phase-8-fleet-telemetry-configuration-checkpoint).

After the Tesla client secret and public key have enabled Secret Manager
versions, register or verify the application without copying either value into
the command line:

```bash
gcloud auth application-default login \
  --impersonate-service-account=tpp-partner-admin@woodhouse-506215.iam.gserviceaccount.com
uv run python scripts/admin/register-partner \
  --project-id woodhouse-506215 \
  --client-id TESLA_APPLICATION_CLIENT_ID \
  --domain woodhouse.derekjass.com \
  --region na
```

The client ID and domain are public application identifiers. The command reads
the secret inputs directly from Secret Manager and prints only regional status.

After the Phase 7 receiver, TLS, and processor are deployed—but before any real
vehicle telemetry configuration—run the isolated proof as an operator listed in
Terraform `admin_principals`:

```bash
gcloud auth application-default login
uv run python scripts/admin/verify-telemetry-pipeline \
  --project-id woodhouse-506215 \
  --confirm-non-vehicle-fixtures
```

The required confirmation is intentionally explicit. The command publishes
only marked non-vehicle fixtures, proves duplicates and a persistence retry are
preserved, proves an unknown marker is quarantined, and reads only the
restricted system dataset. It never selects or configures a Tesla.
