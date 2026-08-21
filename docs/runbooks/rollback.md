# Rollback

**Status:** Phase 2 infrastructure baseline; complete application and telemetry-edge procedures during their implementation phases and verify during Phase 11.

## Shared-infrastructure rollback

1. Identify the last known-good merged commit; never repair production by editing a VM or resource manually.
2. Revert the faulty infrastructure commit in Git.
3. Run a fresh Terraform plan from that merged state against the GCS backend.
4. Review carefully for stateful deletion or replacement. Do not approve deletion of Firestore, the quarantine dataset/table, the state bucket, or durable Pub/Sub data as a routine rollback.
5. Apply the reviewed plan and verify Cloud Run, Pub/Sub backlog, VM status, and logging/monitoring.

Cloud Run images are owned by the application delivery flow rather than Terraform. When commit-addressed application images exist, redeploy the last known-good commit image and verify `/healthz`; do not use `latest`.

Telemetry-edge has no receiver container in Phase 2. Phase 7 must extend this runbook with exact image-digest rollback, certificate validation, restart health checks, and proof that buffered/raw telemetry is not discarded during recovery.
