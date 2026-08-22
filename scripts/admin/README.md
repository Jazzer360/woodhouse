# Admin scripts

Administrative entry points are trusted, explicit operator workflows.

- Phase 2: infrastructure/bootstrap helpers only if Terraform cannot express the operation cleanly.
- Phase 3: idempotent `add-user` and `disable-user` workflows (implemented).
- Phase 4: idempotent Tesla partner registration/verification.
- Phases 7-8: explicit telemetry validation and repair operations.

Run Phase 3 user commands from the repository root after creating ADC for the
keyless `tpp-user-admin` service account:

```bash
uv run python scripts/admin/add-user --project-id woodhouse-506215 --email homer@example.com
uv run python scripts/admin/disable-user --project-id woodhouse-506215 --email homer@example.com
```

The commands never accept or print a token, secret, service-account key, or
Tesla credential. See [`docs/deployment.md`](../../docs/deployment.md#manual-add-homer-workflow)
for permissions, impersonation, dataset access, and recovery behavior.
