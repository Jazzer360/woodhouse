# Telemetry Repair

**Status:** Phase 7 receiver/storage procedure; per-vehicle configuration repair
is completed in Phase 8 and re-verified during Phase 11.

## Triage order

1. Do not change a real vehicle configuration until the receiver, transport,
   and storage layers are independently healthy.
2. Check VM guest attribute `telemetry-edge/status`, then local receiver
   `/status` and Prometheus metrics through IAP.
3. Check the four subscriptions
   `tpp-raw-telemetry-{v,alerts,connectivity,errors}-processor` for oldest
   unacknowledged age. Do not purge, seek, detach, or recreate a subscription as
   a repair shortcut.
4. Check Cloud Run `telemetry-processor` revision readiness and structured logs
   for `telemetry_persistence_retry`, `telemetry_processing_failed`, and
   `unknown_vehicle_telemetry`. Also inspect
   `telemetry_config_provenance_missing`; it means owned rows were preserved but
   lacked a complete trusted profile version/hash.
5. Run the guarded non-vehicle verification only after the deployed TLS path is
   healthy. It proves duplicate preservation, retry/no-loss behavior, and
   unknown-VIN quarantine without touching a car.

## Unknown VIN

Records in `tesla_system_quarantine.raw_unknown_telemetry` must never be copied
into a user dataset by guessing. Compare the VIN's SHA-256 index document,
authoritative `vehicles/{vehicle_id}` record, active `allowed_users` binding,
and actual Tesla account discovery. Repair the registry only through the
reviewed onboarding/admin path. Preserve quarantine rows permanently even after
the mapping is repaired; a future explicit replay tool may append corrected
copies while retaining provenance.

## Retry and backlog

The processor acknowledges only after BigQuery accepts a row. A `503` is a
negative acknowledgement and Pub/Sub redelivers. Exact redeliveries are valid
raw provenance and are not manually removed. Diagnose BigQuery permissions,
table existence, invalid schema changes, Cloud Run auth audience, and Firestore
availability. Re-run `add-user` for the affected approved user to repair the
dataset/table/ACL contract; it must not delete history.

This remains safe during Cloud Run cold starts: the subscriptions use a
60-second acknowledgement deadline, and only an HTTP success acknowledges the
push. A null `pubsub_delivery_attempt` is expected without a dead-letter policy
and is not evidence that acknowledgement failed. Use Pub/Sub push response-class
and latency metrics plus Cloud Run request logs for that proof.

Tesla's `payload.isResend=true` is a separate vehicle-to-receiver condition. It
means the vehicle resent buffered data that the Fleet Telemetry server had not
acknowledged under `delivery_policy=latest`; preserve the row and source
timestamp normally.

If Tesla reports the exact configuration `synced=true` after the original
apply path timed out, use **Verify and record provenance** from the vehicle's
telemetry page. This reads and validates the current Tesla config/errors and
repairs only the trusted profile version/hash. Do not reapply a vehicle
configuration merely to repair missing provenance.

## Missing configuration provenance

Do not update old raw rows or invent a version/hash from payload data. Confirm
the affected vehicle's trusted registry record contains both
`telemetry_config_version` and `telemetry_config_hash`, then use **Verify and
record provenance** if Tesla currently reports the exact desired configuration
as synchronized and error-free. Confirm the processor revision can read the
registry and that subsequent owned rows contain both fields. The
`telemetry_capability_diagnostics` seven-day `recent_message_count` and
`messages_with_profile_provenance` values show whether the repair is taking
effect; older missing rows remain truthful historical evidence.

## Receiver diagnostics

The edge identity can publish only to the receiver's four Terraform-owned
topics and cannot reach Firestore, BigQuery, Tesla OAuth, command secrets, or
the operator fixture topic. Receiver logs are JSON but must not be changed to a
payload-verbose mode in production. Use record type, transaction/message IDs,
client/receiver version, source/ingestion timestamps, and backlog metrics for
diagnosis without dumping location payloads into logs.

Phase 8 adds desired/current per-vehicle config inspection, sync/error checks,
and explicit apply/remove repair. Never use configuration throttling to hide a
downstream persistence problem.
