# Rollback

**Status:** Phase 7 application and telemetry-edge procedure; re-verify during Phase 11.

## Shared-infrastructure rollback

1. Identify the last known-good merged commit; never repair production by editing a VM or resource manually.
2. Revert the faulty infrastructure commit in Git.
3. Run a fresh Terraform plan from that merged state against the GCS backend.
4. Review carefully for stateful deletion or replacement. Do not approve deletion of Firestore, the quarantine dataset/table, the state bucket, or durable Pub/Sub data as a routine rollback.
5. Apply the reviewed plan and verify Cloud Run, Pub/Sub backlog, VM status, and logging/monitoring.

Cloud Run images are owned by the application delivery flow rather than Terraform. When commit-addressed application images exist, redeploy the last known-good commit image and do not use `latest`. Verify `/health` for revisions that expose it. For an older Phase 3 image, verify Cloud Run reports the revision Ready and that an unauthenticated `POST /mcp` returns the application's JSON `401`; the public Google Front End reserves `/healthz`, so its `404` is not an application health result.

## Telemetry-edge rollback

Every delivery writes the requested commit and exact Artifact Registry digest
to VM metadata. The startup script retains the previously deployed digest. If
the new receiver fails local `/status`, it removes the failed container,
restarts the previous digest, and reports
`failed:<requested-commit>:rolled-back` through guest attributes. Cloud Build
then fails even though service was restored.

For an explicit rollback, revert the faulty merged commit and run the edge
trigger from `main`. Do not type a floating tag or manually edit the VM. Verify:

- deployed metadata contains an Artifact Registry `@sha256:` image;
- guest status names the requested commit as successful;
- local `/status` and Prometheus endpoints answer through IAP;
- the public certificate validates for the telemetry hostname;
- all raw subscription backlogs begin draining;
- the guarded synthetic proof reaches BigQuery.

Do not purge Pub/Sub during rollback. Buffered vehicle and Pub/Sub deliveries
may produce duplicates; preserving those rows is the intended at-least-once
contract.
