# Data and Analytics Architecture

## 1. Core rule

**Throttle at the source; preserve at the destination.**

Tesla Fleet Telemetry config determines transmission frequency. Once a valid event reaches our telemetry endpoint, store it indefinitely.

No downstream time sampling, value thinning, or retention expiration by default.

---

## 2. Ingestion path

```text
Vehicle
  -> Fleet Telemetry
  -> Telemetry VM
  -> Pub/Sub raw topic
  -> Telemetry Processor
  -> authenticated owner's BigQuery dataset
```

Pub/Sub is a durability buffer, not a filter.

Production fleet topics are provenance boundaries. Only the telemetry-edge
service account may publish to them, and the processor binds each delivery to
the exact Terraform-owned subscription and expected record type. The official
receiver supplies a VIN derived from a verified Tesla client certificate rather
than trusting the payload VIN. The separate synthetic topic is operator-only;
deliveries from it require the guarded fixture marker and never route to a user
dataset.

The processor must acknowledge Pub/Sub only after the raw event has been durably accepted for persistence according to the chosen implementation.

---

## 3. Per-user BigQuery dataset

Each manually approved platform user has one dataset:

```text
tesla_u_<opaque_user_id>
```

No default dataset/table expiration.

All of that user's vehicles are in the same dataset and identified by `vehicle_id`.

This allows questions such as:

- compare my two cars;
- show all trips regardless of vehicle;
- show only Woodhouse history.

---

## 4. Raw telemetry table

The trusted vehicle registry receives `telemetry_config_version` and
`telemetry_config_hash` only after Tesla reports the exact per-vehicle desired
configuration as `synced=true` with no relevant telemetry errors. The field
selection hash and server trust-profile ID/hash are stored separately so a
compatible leaf renewal cannot masquerade as a field/frequency change. See
[`fleet-telemetry-configuration.md`](fleet-telemetry-configuration.md).
If an apply request times out before Tesla reports synchronization but the
vehicle later adopts the exact desired configuration, use the onboarding
**Verify and record provenance** action. It performs read-only Tesla config and
error checks, requires an exact desired/current match, and repairs only the
trusted registry metadata; it does not submit a configuration or wake the car.

Recommended baseline:

```text
raw_telemetry_events
  source_timestamp TIMESTAMP
  ingested_at TIMESTAMP
  processed_at TIMESTAMP
  vehicle_id STRING
  vin STRING
  tesla_vehicle_id STRING
  record_type STRING
  payload JSON
  telemetry_config_version STRING
  telemetry_config_hash STRING
  transport_message_id STRING
  pubsub_message_id STRING
  pubsub_publish_time TIMESTAMP
  pubsub_delivery_attempt INTEGER
  telemetry_client_version STRING
  telemetry_receiver_version STRING
  receiver_record_version INTEGER
```

Partition by:

```text
DATE(source_timestamp)
```

Cluster by:

```text
vehicle_id, record_type
```

Preserve the complete decoded event payload.

`source_timestamp`, `ingested_at`, and `processed_at` are separate clocks.
`transport_message_id` is Tesla's transaction ID when present;
`pubsub_message_id` identifies the Google transport delivery. The receiver and
client version fields make protocol changes diagnosable. Configuration
version/hash come only from trusted registry state, never from a publisher.

Do not throw away fields after promoting commonly queried values to typed columns/views.

---

## 5. Every valid event means every observation

If Tesla emits a record to our server, we keep it.

Do not discard an event because:

- the same field was stored seconds ago;
- the value changed only slightly;
- a downstream derived table does not currently use it;
- storage is expected to grow.

Exact transport redeliveries may appear more than once in the raw table. Preserve message IDs so analytical views may de-duplicate retry deliveries without losing original provenance.

`payload.isResend=true` is Tesla's vehicle-to-receiver resend marker under
`delivery_policy=latest`; it is independent of Google Pub/Sub delivery.
`pubsub_delivery_attempt` is nullable because Google supplies
`deliveryAttempt` only for subscriptions with a dead-letter policy. Woodhouse
does not infer attempt 1 when the field is absent. Diagnose push delivery using
Cloud Run request status/latency plus Pub/Sub `push_request_count`,
`push_request_latencies`, and `oldest_unacked_message_age`; the durable row's
Pub/Sub message ID remains the retry-correlation key.

The first production aggregate review on 2026-08-24 found 4,350 rows with
4,350 distinct Pub/Sub message IDs and no exact duplicate delivery signatures.
Nine `V` rows were Tesla-marked resends. All processor requests visible in
Cloud Run returned success; source-to-ingest latency was 47 ms median / 75 ms
p95 and processor time was 173 ms p95. The 60-second push acknowledgement
deadline therefore had substantial observed margin. Re-check these metrics as
volume and processor behavior evolve rather than assuming the first-day result
is permanent.

The processor uses BigQuery streaming inserts with no insert ID. A successful
insert response is the durable-acceptance boundary; only then does the
authenticated push handler return `204`. BigQuery or registry-transient
failures return a non-2xx response so Pub/Sub redelivers. This is deliberately
at-least-once, not exactly-once.

---

## 6. Source vs ingestion timestamp

Tesla can buffer messages during connectivity loss and later deliver them.

Always preserve both:

- `source_timestamp` -> when the vehicle observation occurred;
- `ingested_at` -> when our infrastructure received it.

Historical chronology uses source time. Transport health diagnostics use both.

---

## 7. Derived analytical layer

Raw truth remains permanent. Build derived views/materialized tables on top.

Phase 9 provisions the following dependency-ordered logical views in every
approved user's existing private dataset whenever `add-user` is run:

The typed `Value` mapping was rechecked on 2026-08-24 against Tesla's current
official `fleet-telemetry/protos/vehicle_data.proto`. Known scalar enum oneofs
are promoted to `string_value`; structured `time_value`/`tire_location_value`
and every future/unknown representation remain intact in `value_json`.

| View | Interpretation |
|---|---|
| `telemetry_observations` | Expands each `V` payload datum into typed numeric, string/enum, boolean, location, and complete JSON values. Exact Pub/Sub redeliveries are de-duplicated by `pubsub_message_id` here; Tesla-marked resends remain observations. |
| `vehicle_state_changes` | Orders valid values by vehicle/field/source time and exposes the previous typed value. |
| `drives` | Reconstructs forward/reverse Gear intervals and summarizes time, odometer distance, energy, speed, endpoints, sample count/gaps, and config provenance. |
| `charge_sessions` | Reconstructs charging-state intervals and summarizes SOC, AC/DC energy counters, power, voltage, location, and sample count. |
| `media_history` | Carries emitted media changes forward and groups contiguous title/artist/album/station/source/playback-state intervals, including playback position and volume. |
| `daily_vehicle_summary` | UTC daily per-vehicle drive, efficiency, charging, SOC, temperature, and maximum-speed summary. |

These are rebuildable views, not additional sources of truth. Their session
boundaries are only as complete as the emitted source changes; open sessions
remain explicitly marked. Query callers should filter `source_timestamp`,
`started_at`, or `summary_date` to preserve raw partition pruning through the
view chain. `semantic_events` is intentionally absent until Phase 10 produces
real semantic events.

Initial useful concepts:

```text
drives
charge_sessions
media_history
acceleration_events
semantic_events
vehicle_state_changes
daily_vehicle_summary
```

Possible later concepts:

```text
trips
geofence_visits
parked_energy_intervals
efficiency_by_temperature
self_driving_summary
charging_cost_summary
```

If derivation logic changes, rebuild from raw history rather than attempting to repair old derived rows manually.

---

## 8. Media history is first-class

Configure and retain available Tesla media telemetry such as:

- title;
- artist;
- album;
- station;
- playback source;
- playback status;
- duration;
- elapsed position.

Derived `media_history` should reconstruct track/playback intervals from emitted changes.

This enables questions such as:

> What did I listen to on the Colorado trip?

The model can locate the trip by drive/location data, then join the time window against `media_history`.

No dedicated playlist endpoint is required.

---

## 9. Acceleration and launch analysis

Raw `VehicleSpeed`, `LongitudinalAcceleration`, `BrakePedal`, and `Gear`
observations support rebuildable acceleration/deceleration analysis. Fleet
Telemetry 1.3.0+ reciprocally includes longitudinal acceleration with
qualifying speed and current speed with meaningful acceleration changes. This
is synchronized delivery, not a claim that separate vehicle sensors sampled at
the exact same instant. Independent one-second parent timers may yield useful
paired observations in adjacent 500-millisecond collector buckets, but derived
logic must never assume or promise a sub-second cadence.

Derived `acceleration_events` should:

- order observations by source timestamp and retain ingestion timestamps only
  for transport diagnostics;
- identify stationary, forward-gear starts and reject rolling starts;
- use independent longitudinal-acceleration changes to estimate event onset;
- interpolate threshold crossings between surrounding speed observations;
- retain acceleration and braking sign/magnitude plus brake-pedal context;
- record sample count, largest source-time gap, telemetry client/config hash,
  and a quality/uncertainty classification;
- reject or clearly downgrade attempts with missing/out-of-order samples rather
  than inventing precision.

Approximate 0-60 results are personal analytical estimates, not certified
performance measurements. Raw observations remain authoritative and permanent
if the detection/interpolation method changes later.

---

## 10. Self-driving mileage analysis

`SelfDrivingMilesSinceReset` is an HW4 cumulative statistic, not a live FSD
engagement state. Analyze it together with `MilesSinceReset`, its total-mile
denominator. Fleet Telemetry 1.3.0+ can deliver either counter with the other in
the same payload; client 1.2 can deliver them as independent observations.

A derived `self_driving_summary` should:

- align counter observations by source timestamp and vehicle/config version;
- begin a new reset epoch whenever either cumulative counter decreases;
- calculate FSD miles and share from counter deltas only within one epoch;
- preserve the source observations used, their time span, and any pairing gap;
- classify missing HW4/client support as unavailable rather than zero; and
- avoid claiming trip-level FSD engagement or exact transition times from
  one-mile-delta cumulative counters.

Tesla may reset the statistics after software updates, computer replacement,
factory reset, or other triggers. Raw observations remain authoritative so
epoch and pairing logic can be rebuilt if Tesla's behavior changes.

---

## 11. Generic analytics MCP tools

### `get_analytics_schema`

Return only the authenticated user's analytical namespace.

Include:

- tables/views;
- fields/types/descriptions;
- likely join keys;
- partition fields;
- example queries where helpful.

### `run_analytics_query`

The model authors SQL specific to the question.

Security requirements:

- Standard SQL only;
- one read-only statement;
- `SELECT` / `WITH` only;
- current user's dataset as server-set default;
- reject explicit project/dataset references;
- reject DML/DDL/EXPORT/external/remote operations;
- validate with a parser/AST;
- dry-run first;
- timeout and result-size bounds;
- maximum-bytes safety cap.

The safety cap prevents accidental giant queries. It is not a per-user commercial quota.

The implemented bounds are one parsed/canonicalized BigQuery statement, 32 KiB
of SQL text, a 1 GiB `maximumBytesBilled` ceiling, a 15-second dry-run request,
a 30-second execution/job timeout, 200 returned rows, and 512 KiB of serialized
row data. The AST must resolve every physical table to one unqualified name in
the static analytics catalog. CTEs and subqueries are allowed; project/dataset
qualification, unknown objects, DML/DDL, scripts, `EXPORT DATA`,
`EXTERNAL_QUERY`, and remote/user-defined functions are rejected. A narrow
allowlist exists only for deterministic BigQuery geography constructors that
SQLGlot 30.17 represents generically. The canonical AST rendering—not the
caller's original SQL string—is sent to BigQuery.

Successful operational logs contain the opaque authenticated `user_id`,
correlation/job IDs, duration, processed/billed bytes, referenced in-scope
object names, returned-row count/bytes, and truncation. Failure logs contain a
safe category/type and the same non-row metadata. SQL text and result rows are
not copied to general logs.

---

## 12. Current state does not belong in the history path

Ordinary questions about current vehicle state should use Tesla Fleet API via MCP.

BigQuery may have a very recent event but is not the authoritative realtime command/read path.

Use BigQuery for history, trends, correlations, reconstructed sessions, and open-ended analysis.

---

## 13. Unknown vehicle routing

The processor resolves raw telemetry VIN against the Firestore vehicle registry.

If no owner mapping exists:

- never guess;
- do not write to a user's dataset;
- preserve the record in a restricted system/quarantine path;
- log/alert for repair.

The restricted system dataset also contains `raw_synthetic_telemetry`, used
only by the guarded Phase 7 operator check. Its explicit fixture marker cannot
claim a user or vehicle and never writes to a per-user dataset.

---

## 14. Future broader personal analytics MCP

Design BigQuery schemas cleanly because Tesla data may later be queried beside other personal datasets (music APIs, finance, health/wellness, home energy, etc.).

The Tesla-specific MCP should still keep its own analytics tools now; a future general personal analytics MCP can point at the same warehouse or authorized views later.

Do not make the Tesla platform dependent on that future project.
