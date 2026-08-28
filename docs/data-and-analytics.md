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

Phase 9 defines the following dependency-ordered logical views. `add-user`
installs them for a new or repaired user, and the dedicated main-merge analytics
reconciler updates the complete set in every active user's existing private
dataset whenever the source definitions change:

Definitions are packaged `.sql.j2` resources under
`analytics/view_definitions/sql`, not Python string literals. A strict TOML
manifest records dependency order, descriptions, sources, and the one shared
category-sample template. Jinja runs with `StrictUndefined` and exposes only
allowlisted `ref()`/`source()` helpers plus reviewed generated field fragments.
After rendering, SQLGlot parses BigQuery SQL and verifies that every physical
reference stays inside the trusted per-user project/dataset and appears in the
manifest dependency contract. SQLFluff checks the template tree in PR CI. The
small `analytics.views` module is only a compatibility import for existing
callers.

The typed `Value` mapping was rechecked on 2026-08-24 against Tesla's current
official `fleet-telemetry/protos/vehicle_data.proto`. Known scalar enum oneofs
are promoted to `string_value`; structured `time_value`/`tire_location_value`
and every future/unknown representation remain intact in `value_json`.

| View | Interpretation |
|---|---|
| `telemetry_field_catalog` | All 239 fields in the pinned Tesla schema with category/type/description, broad-v4 inclusion, interval/delta/include-fields policy, exclusion reason, profile/schema versions, and the client-capability target used to expand that policy. `configured` describes the full policy, not proof that a particular vehicle supports or emitted the field. |
| `telemetry_observations` | Expands each `V` payload datum into typed numeric, string/enum, boolean, location, and complete JSON values. Exact Pub/Sub redeliveries are de-duplicated by `pubsub_message_id` here; Tesla-marked resends remain observations. |
| `charging_samples` | Sparse wide charging rows keyed by the exact emitted message timestamp and delivery ID. |
| `climate_samples` | Sparse wide climate rows keyed by the exact emitted message timestamp and delivery ID. |
| `driving_samples` | Sparse wide driving rows suitable for speed/acceleration/braking graphs. |
| `location_samples` | Sparse wide navigation/location rows; each configured `Location` value has separate latitude/longitude columns. |
| `media_samples` | Sparse wide media rows for direct graphing or inspection of emitted playback metadata/state. |
| `vehicle_state_changes` | Orders valid values by vehicle/field/source time and exposes the previous typed value. |
| `drive_metric_boundaries` | Selects Odometer, EnergyRemaining, SOC, and Location at each Gear boundary with metric-specific exact/as-of/stationary/fallback semantics, source timestamp, signed age, and message provenance. |
| `drives` | Reconstructs forward/reverse Gear intervals and summarizes boundary-correct odometer distance, energy, SOC, efficiency, speed, endpoints, sample gaps, config provenance, and boundary quality. |
| `charge_sessions` | Uses DetailedChargeState authoritatively with coarse fallback, same-session SOC, stationary-validated odometer/location, measured counter plus bounded piecewise power-tail energy, battery/wall efficiency, distance since prior charge, observation ages, and inference methods. |
| `drive_path_points` | Produces route points whose cumulative GPS distance is scaled to boundary-correct odometer distance, with carried speed and SOC. |
| `drive_fsd_segments` | Allocates cumulative FSD-counter deltas into manual/FSD/uncertain route segments with confidence, method, and transition-distance bounds. |
| `drive_fsd_summary` | Aggregates total, FSD, manual, and uncertain mileage and FSD percentage per drive. |
| `drive_path` | Joins route points to inferred FSD state for future map/API rendering. |
| `telemetry_capability_diagnostics` | Compares incoming client-version history/first-seen time, receiver/profile metadata, and seven-day synchronized Gear, charge, and FSD payload evidence. |
| `media_history` | Carries emitted media changes forward and groups contiguous title/artist/album/station/source/playback-state intervals, including playback position and volume. |
| `daily_vehicle_summary` | UTC daily per-vehicle drive, efficiency, charging, SOC, temperature, and maximum-speed summary. |

These are rebuildable views, not additional sources of truth. Their session
boundaries are only as complete as the emitted source changes; open sessions
remain explicitly marked. Query callers should filter `source_timestamp`,
`started_at`, or `summary_date` to preserve raw partition pruning through the
view chain. `semantic_events` is intentionally absent until Phase 10 produces
real semantic events.

The view definitions persist, but their result rows do not. BigQuery expands
and evaluates each logical view when it is queried; no application process
iterates over the dataset when a view is created. BigQuery can push compatible
filters into the underlying source-time partitions and can reuse an eligible
query-result cache, but callers must not treat a logical view as a precomputed
table. The session/window views may still scan substantial raw history as the
dataset grows. Query-job byte metadata and the MCP dry-run ceiling provide the
signal for promoting a repeatedly expensive derivation to an incremental,
partitioned rebuildable table. Native materialized views may be used only when
the derivation fits BigQuery's restricted materialized-view SQL; raw history
remains authoritative either way.

The reconciler creates or updates every source-defined view, records a short
definition hash in its labels, and then removes stale objects only when they are
BigQuery views carrying the complete Woodhouse managed-analytics label set.
Raw tables, user-created views, and other unmanaged objects are never deletion
candidates. It temporarily adds only its dedicated service identity as a
dataset reader for BigQuery SQL validation and restores the exact prior ACL in
`finally`, including on failure. Active tenant dataset IDs come only from the
trusted Firestore allowlist; the build accepts no caller-supplied user or
dataset selector.

BigQuery validates logical-view SQL while the metadata mutation is submitted;
that validation is planning, not execution over the user's rows. The reconciler
allows up to 120 seconds for that validation so BigQuery can return a specific
semantic planner error instead of masking it as a short client deadline. View
SQL must still avoid unsupported correlated table subqueries and express
nearest-point/existence logic with joins, windows, or aggregates. A definition
that cannot dry-run successfully fails the merge delivery before stale-view
cleanup; raising this deadline is not a substitute for simplifying an invalid
or excessively complex view graph.

### Session boundary rules

Drive identity remains Gear-defined. `Soc` and `EnergyRemaining` use
backward-looking state-as-of semantics at both boundaries: the selected value
is the most recent valid observation whose source timestamp is at or before the
Gear transition. An exact synchronized Gear payload wins naturally. These two
cached state fields are not rejected solely because of age, and their signed
observation offsets therefore cannot be positive. Odometer and Location retain
their separate physical-boundary behavior: exact Gear payload, stationary
observation outside the drive, then a bounded interior fallback. Five-minute
outer windows, adjacent drive boundaries, and 90-second interior fallbacks
continue to constrain only those physical metrics.

For charging, `DetailedChargeStateCharging` starts active charging and
`DetailedChargeStateDisconnected`, `NoPower`, `Complete`, or `Stopped` ends it;
`Starting` is transitional. Once a recent detailed state is present it remains
authoritative, so a coarse `ChargeState=Init` cannot terminate an active
detailed session. Coarse `ChargeState` is used before detailed data exists or
when an old terminal detailed state is followed by a new coarse charging state
after the bounded fallback interval. Unknown values do not create transitions.

Same-session SOC and `EnergyRemaining` remain eligible through the stop
transition even when the terminal message omits them. The view retains each
selected SOC observation's source time, signed boundary offset, and inference
method; it never rounds SOC to Tessie's display precision.

Charge odometer and location use a separate stationary invariant. If an exact
or two-minute-near value is absent, the view pairs the nearest valid observation
within 30 minutes before charge start with the nearest one within 30 minutes
after charge end. It carries the state only when odometers agree within 0.01
mile or locations are within 100 meters and there is no intervening driving
Gear or speed above 1 mph. A large unbounded gap, disagreement, or movement
leaves the value unavailable. `distance_since_previous_charge_miles` uses these
reconstructed start/end odometers.

Tesla defines `ACChargingEnergyIn` as charger-measured AC input energy that must
be ignored during DC charging, while `DCChargingEnergyIn` is battery-measured
energy usable for both AC and DC sessions. Each public counter column is the
measured session delta. When the last applicable counter precedes the stop by
at most five minutes, counter order is monotonic, and applicable power covers
the whole missing interval, the view seeds the tail with the most recent valid
power state in the same continuous session and integrates later power changes
piecewise to the authoritative stop. The tail is exposed separately and added
once to the measured counter. Counter resets, missing initial power coverage,
an invalid counter/power signal in the tail, wrong charging mode, and longer
gaps never receive an inferred tail; provenance
distinguishes measured-only, bounded-tail, unavailable-tail, and anomalous
results.

Tesla's current Fleet Telemetry
[system-behavior documentation](https://developer.tesla.com/docs/fleet-api/fleet-telemetry)
and [available-data definitions](https://developer.tesla.com/docs/fleet-api/fleet-telemetry/available-data)
were rechecked on 2026-08-28: changed fields enter a 500-millisecond collector
bucket after their own interval/delta rules permit emission. The five
`*_samples` views therefore
group only fields in the same actual source message; they do not time-bucket,
interpolate, or carry a previous value forward. A metric `NULL` means it was not
validly emitted in that message. `observed_fields` distinguishes a missing
field from an emitted field, and `invalid_fields` identifies fields Tesla
explicitly marked invalid. This preserves honest timestamps while providing
dashboard-friendly wide columns. Use `telemetry_observations` for universal
long-form analysis and joins across all categories.

Initial useful concepts:

```text
drives
charge_sessions
drive_fsd_segments
drive_fsd_summary
drive_path
telemetry_capability_diagnostics
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

The `broad-v4` profile requests `MediaAudioVolume` and the five change-oriented
now-playing metadata/duration fields at a one-second interval. The continuously
advancing `MediaNowPlayingElapsed` uses a 15-second interval: enough independent
playback-position detail for song-relative analysis without a steady
one-signal-per-second stream. On Fleet Telemetry 1.3.0+, a volume change also
carries all six now-playing fields in the same payload so reaction analysis has
exact track and playback-position context. Tesla's normal changed-value rule
still applies to each independently configured field.

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

## 10. Self-driving mileage and segment analysis

`SelfDrivingMilesSinceReset` is an HW4 cumulative statistic, not a live FSD
engagement state. Analyze it together with `MilesSinceReset`, its total-mile
denominator. Fleet Telemetry 1.3.0+ can deliver either counter with the other in
the same payload; client 1.2 can deliver them as independent observations.

A reset-aware counter timeline:

- align counter observations by source timestamp and vehicle/config version;
- begin a new reset epoch whenever either cumulative counter decreases;
- calculate FSD miles and share from counter deltas only within one epoch;
- preserve the source observations used, their time span, and any pairing gap;
- classify missing HW4/client support as unavailable rather than zero; and
- pairs same-message values exactly on client 1.3 and carries the nearest other
  counter at reduced confidence for client 1.2 history;
- maps milestones to `drive_path_points` distance;
- implements nearest route-point selection with explicit joins and ordered
  aggregation rather than per-row correlated table subqueries;
- expands manual/FSD/tail alternatives from typed arrays in one pass rather
  than repeatedly referencing the same deep CTE chain, keeping logical-view
  planning bounded as history grows;
- treats each positive FSD delta as certifying the preceding distance in that
  counter bucket;
- allocates a mixed bucket as a manual prefix followed by the certified FSD
  distance, with the transition bounded by the whole bucket;
- carries the final inferred state to the Gear boundary only at reduced
  confidence; and
- emits `uncertain`, not zero/manual, when counter/reset/path evidence is insufficient.

`drive_fsd_summary` aggregates these segments. `drive_path` attaches state,
confidence, and inference method to route points. Neither view claims exact FSD
engagement events: one-mile counter gating and sparse independent 1.2 emissions
make the transition bounds part of the result.

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

The schema includes `telemetry_field_catalog`, so a model can inspect every
pinned Tesla field and the exact Woodhouse collection decision without relying
on a truncated `SELECT DISTINCT field_name` discovery query. It also exposes
the five wide exact-emission sample views for straightforward dashboard SQL;
the raw table and long-form `telemetry_observations` remain available for novel
queries not covered by derived views.

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
a 30-second execution/job timeout, 1,000 returned rows, and 1 MiB of serialized
row data. These response bounds limit only the rows transferred back into the
MCP/model context; BigQuery may aggregate or correlate many more input rows
within the byte and time ceilings. Prefer SQL-side filtering, aggregation, and
statistical calculation over returning a large raw extract. The AST must
resolve every physical table to one unqualified name in
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
