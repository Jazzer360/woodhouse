# Admin scripts

Administrative entry points will be added here in the phases that define their trusted behavior.

- Phase 2: infrastructure/bootstrap helpers only if Terraform cannot express the operation cleanly.
- Phase 3: idempotent `add-user` and `disable-user` workflows.
- Phase 4: idempotent Tesla partner registration/verification.
- Phases 7-8: explicit telemetry validation and repair operations.

Phase 1 intentionally contains no executable admin command, credential input, cloud mutation, OAuth flow, or vehicle action.
