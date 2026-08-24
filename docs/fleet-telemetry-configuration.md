# Fleet Telemetry configuration

This document is the Phase 8 source of truth for selecting, applying, and
maintaining the per-vehicle Fleet Telemetry configuration. The raw retention
rules in [`data-and-analytics.md`](data-and-analytics.md) remain unchanged:
frequency is controlled only here, at the Tesla source, and every valid record
received by Woodhouse is retained.

The schema and behavior were audited on 2026-08-23 against Tesla's current
Fleet Telemetry overview, Available Data catalog, `vehicle_data.proto`, Vehicle
Endpoints documentation, and the pinned receiver version `v0.9.4`.

## Canonical profile

The only default profile is `broad-v1`. The name is retained for compatibility,
but its policy is now **Tessie baseline plus explicit Woodhouse decisions**, not
"subscribe to nearly everything." It is capability-projected per vehicle; it
is not a plan, quota, or storage tier.

- Source catalog: 239 documented fields.
- Operator-supplied Tessie baseline: 93 fields.
- Woodhouse deviations: 14 overrides, 40 additions, and 2 removals.
- Configured for a Fleet Telemetry 1.2.0+ passenger vehicle: 131 fields.
- Fleet Telemetry 1.0/1.1 projection: 129 fields; the two HW4 self-driving
  counters are omitted because Tesla introduced them in client 1.2.0.
- Fleet Telemetry 1.3.0+ additionally uses reciprocal `include_fields` for two
  analytical pairs: qualifying speed carries acceleration and meaningful
  acceleration changes carry current speed; either self-driving mileage
  counter carries its matching total/self-driving counter. Earlier clients
  keep the supported top-level fields independently configured and omit only
  that synchronization.
- Remaining exclusions: 108 fields, comprising the 2 Tessie removals and 106
  explicit decisions not to add a non-baseline catalog field.
- `delivery_policy` is `latest`, so unacknowledged buffered data is resent.
- Alert types are `service`, `customer`, and `service-fix`.
- Location-bearing fields require the owner's `vehicle_location` grant.
- The profile requires Fleet Telemetry client 1.0.0 or later because it
  depends on location `minimum_delta` and `delivery_policy=latest`.

The checked-in field catalog is
[`fleet_streaming_fields_v0_9_4.csv`](../packages/tesla-client/src/tesla_personal_platform/tesla_client/data/fleet_streaming_fields_v0_9_4.csv).
The operator-supplied field snapshot is transcribed exactly in
[`fleet_telemetry_tessie_baseline.toml`](../packages/tesla-client/src/tesla_personal_platform/tesla_client/data/fleet_telemetry_tessie_baseline.toml).
Woodhouse decisions, with inline rationales, live in
[`fleet_telemetry_woodhouse.toml`](../packages/tesla-client/src/tesla_personal_platform/tesla_client/data/fleet_telemetry_woodhouse.toml).
The validating loader is
[`telemetry.py`](../packages/tesla-client/src/tesla_personal_platform/tesla_client/telemetry.py).
The loader refuses to start if a field is unknown, a deviation lacks a
rationale, sections overlap, or any of the 239 catalog fields lacks an explicit
decision. The operator screen renders the Tessie comparison, exact
desired/current field maps, and hashes before it enables Apply.

The source `api-response.json` is not checked in. Its hostname, CA material,
JWS claims/signature, sync state, identifiers, and timestamps are transport or
potentially sensitive runtime data. Only the 93 field interval/delta settings
were transcribed into the baseline.

### Tessie overrides

These are every cadence/delta difference for a field already in Tessie:

| Field(s) | Tessie | Woodhouse | Why |
|---|---:|---:|---|
| `Location` | 30 s / 3 m | 10 s / 10 m | Better trip shape at Tesla's economical example cadence, while filtering GPS drift more strongly. |
| `VehicleSpeed` | 30 s / no delta | 1 s / 1 mph | Preserve one-mph cruising changes plus acceleration/deceleration traces and approximate launch timing. On client 1.3+, include longitudinal acceleration in the same payload. |
| `GpsHeading` | 60 s / 1° | 10 s / 5° | Align heading with trip cadence and ignore small directional jitter. |
| Six media metadata/state fields | 60 s | 10 s | Capture short tracks, skips, and state/source changes; unchanged values do not emit. |
| `TpmsSoftWarnings`, `TpmsHardWarnings` | 1,800 s | 60 s | Warning transitions are rare, so improved latency adds little normal volume. |
| `HvacLeftTemperatureRequest` | 1 s | 5 s | Setpoint history does not need one-second polling or a numeric delta. |
| `Odometer` | 30 s / 0.01 mi | 60 s / 0.01 mi | Retain Tessie's precision at a cheaper cadence. |
| `SelfDrivingMilesSinceReset` | 30 s / 1 mi | 30 s / 1 mi, paired | Retain Tessie's Tesla-required threshold; on client 1.3+, carry the total-mile denominator in the same payload. |

Thus, Woodhouse is more responsive than Tessie for trip shape, media changes,
and TPMS warning transitions; less aggressive for the HVAC setpoint and
odometer. The higher location delta makes the faster location interval less
sensitive to stationary jitter.

### Woodhouse additions and removals

The 40 additions are deliberately narrow groups rather than whole Tesla
categories:

- 12 charging/session fields for energy-in, charge state/rate, BMS state, port
  conditions, preconditioning, scheduling, and completion estimates;
- 10 climate state fields for HVAC mode/fan, right setpoint, defrost, seat
  climate/ventilation, and cold-weather loads;
- 5 selected driving fields: one-second longitudinal acceleration, one-second
  brake state, cruise set speed, drive-ready state, and coarse traffic delay;
- 5 location/context fields: GPS validity, Tesla's home/work/favorite
  classification, and a 25-meter-gated route origin;
- 2 safety/self-driving fields: driver seat belt and the counter denominator;
- 6 rare vehicle-state fields for guest/service mode, hazards, key count, and
  software-update duration.

Woodhouse retains Tessie's `MediaAudioVolume` at 60 seconds because correlating
volume changes with the current track can provide a weak but useful engagement
signal. It removes `MediaAudioVolumeIncrement` and `MediaAudioVolumeMax` because
those static UI range values add no comparable historical value.

The 106 non-baseline omissions are individually commented in the Woodhouse
TOML. Major choices include detailed powertrain engineering signals,
lateral acceleration and pedal-position streams, turn-signal/high-beam transitions,
route geometry, static preferences/configuration, redundant fields, unsupported
hardware families, Tesla's documented broken `RouteLastUpdated`, and its
misreported `PassengerSeatBelt`. These can be reconsidered only with a concrete
analytical use and a versioned, operator-reviewed change.

## Interval and minimum-delta policy

Tesla emits a configured value only after its interval has elapsed **and** the
value has changed. A short interval for a boolean or enum therefore reduces
transition latency without producing a constant sample stream. Numeric and
location deltas add a second noise threshold.

| Selected signal family | Normal interval | Delta policy and rationale |
|---|---:|---|
| Body, security, safety, gear | Tessie 1-5 s | Discrete change only; unchanged values do not emit. |
| Location | 10 s | 10 m; route origin is 60 s / 25 m. |
| Speed and longitudinal acceleration | 1 s | 1 mph and 1 m/s² respectively; on client 1.3+ each field includes the other when it independently qualifies. |
| Charging and battery | Mostly 30-120 s | Physical measurements use explicit defensible deltas; discrete state/settings do not receive artificial deltas. |
| Climate | Tessie baseline plus 5 s added states | Temperatures inherit Tessie's deltas; modes, setpoints, fan levels, and switches are treated as changed values. |
| Media | 10-60 s | Metadata/state is 10 s; elapsed/duration retain Tessie's slower, delta-gated behavior. |
| TPMS | Pressure 900 s; warnings 60 s | Pressure inherits Tessie's 0.1 threshold; warning state prioritizes transition latency. |
| Low-rate diagnostics/security inventory | 60-300 s | Only selected high-value fields are included. |

`minimum_delta` is opt-in, not inferred from Tesla's numeric type. It is used
for noisy physical values, distance/location, monotonic energy/odometer
measurements, or a counter where the threshold has a clear unit. Numeric
settings, enum-like levels, counts, and durations generally rely on Tesla's
normal changed-value behavior. This avoids the earlier blanket-delta policy.

High-volume considerations shown at the checkpoint:

- Location can emit at most once per 10 seconds after 10 meters of movement;
- speed and longitudinal acceleration can each emit at most once per second
  after crossing their respective delta; synchronized acceleration is another
  billable signal whenever qualifying speed publishes on client 1.3+;
- detailed powertrain, lateral acceleration, and pedal-position telemetry are omitted;
- charging measurements normally emit at most once per 60 seconds and usually
  require a measurement delta;
- slow/discrete fields normally emit only on a real state change or on the
  vehicle's initial telemetry snapshot.

Tesla currently charges $1 per 150,000 streaming signals and applies a $10
monthly developer discount across Fleet API usage categories. If telemetry
were the only charge, that is 1.5 million signals/month, approximately 50,000
per day; Woodhouse intentionally leaves margin for commands, wakes, and live
reads. This is a target, not a guarantee: actual cost depends on supported
fields and how often values change across every configured vehicle. Monitor the
[Tesla Fleet API dashboard](https://developer.tesla.com/) after the first live
apply and compare billing usage with receiver metrics/raw counts before adding
fields or shortening intervals. Tesla's
[billing-limit behavior](https://developer.tesla.com/docs/fleet-api/billing-and-limits)
can remove telemetry configurations at the limit and does not automatically
restore them, so alert below the limit. A field/frequency change always creates
a versioned explicit operator diff; it is never automatic maintenance.

## Acceleration/deceleration trace policy

The profile deliberately spends additional signal budget on reconstructable
driving dynamics:

- `VehicleSpeed`: 1 second, 1 mph minimum delta;
- `LongitudinalAcceleration`: 1 second, 1 m/s² minimum delta;
- `BrakePedal`: 1 second, change-based boolean;
- on Fleet Telemetry client 1.3.0+, `VehicleSpeed.include_fields` contains
  `LongitudinalAcceleration` and `LongitudinalAcceleration.include_fields`
  contains `VehicleSpeed`.

Tesla guarantees that an included field is carried in the same payload whenever
its parent publishes, even when the included value has not changed. This gives
speed and acceleration a shared delivery event, but it does not guarantee the
underlying sensors were sampled atomically at an identical physical instant.
Both remain top-level fields because included-field delivery does not reset the
included field's timer. Their independent timers allow a meaningful
acceleration change to add a current-speed observation between regular speed
events, when their triggers happen in different collector buckets. This can
produce useful payloads about 500 milliseconds apart, but sub-second resolution
is opportunistic rather than guaranteed. Clients before 1.3.0 safely omit both
include relationships while retaining both top-level fields.

At the current streaming price, top-level speed and its included acceleration
can contribute up to 120 signals/minute. Independently triggered acceleration
plus its included speed can contribute another 120/minute in the conservative
case where Tesla bills both values and the two triggers do not coalesce; brake
transitions are normally negligible. That 240-signal ceiling is $0.096/driving
hour. Actual volume should be materially lower because changes must cross their
deltas and 500-millisecond collector buckets may coalesce activity, but it must
be measured across all enrolled vehicles.

`minimum_delta=1` does not round or quantize speed to whole one-mph steps. Tesla
sends the actual current value once its change from the last published speed
crosses the threshold. The threshold does mean sub-one-mph cruising variation
can remain unreported; one mph is the selected balance for time-weighted cruise
statistics. No `resend_interval_seconds` is configured because unchanged speed
can be carried forward analytically until the next state transition without
paying for periodic repeats.

Phase 9 analytics may derive acceleration events and approximate 0-60 runs from
the permanent raw observations. It must use source timestamps, require a
stationary start and continuous forward acceleration, interpolate the 60 mph
crossing between surrounding speed values, and report sample count, maximum
gap, capability mode, and an uncertainty/confidence label. A gap over roughly
1.5 seconds or a rolling start should invalidate a high-confidence result. This
is useful personal telemetry, not drag-strip timing equipment; do not present
tenths of a second without the accompanying uncertainty evidence.

## Self-driving mileage policy

`SelfDrivingMilesSinceReset` is Tesla's cumulative count of miles driven using
FSD since Tesla last reset its self-driving statistics. It is not a live
indicator that FSD is currently engaged. Tesla documents the field as HW4-only,
introduced in Fleet Telemetry client 1.2.0, and requires
`minimum_delta >= 1`. Woodhouse therefore retains Tessie's 30-second,
one-mile configuration and adds `MilesSinceReset` at the same cadence and
threshold as the required total-mile denominator.

On client 1.3.0+, the two fields reciprocally include each other so a change to
either counter carries the matching value in the same payload. Tesla permits
these two statistic fields to include only each other, which the declarative
profile loader enforces. Client 1.2 retains both independent top-level fields
without `include_fields`; earlier clients omit both unsupported fields.

Tesla may reset these counters after some software updates, car-computer
replacement, a factory reset, or other internal triggers. Analytics must treat
any decrease in either counter as a new reset epoch and must never subtract
across that boundary. FSD share is derived from paired counter **deltas within
one epoch** (`delta(SelfDrivingMilesSinceReset) / delta(MilesSinceReset)`), not
from a single instantaneous ratio. Missing counters on an unsupported vehicle
mean unavailable, not zero. The one-mile delta makes this suitable for
cumulative and multi-mile analysis, not precise trip-level engagement timing.

## Per-vehicle lifecycle

The existing authenticated onboarding screen is the administrative path. It
derives `user_id` from the session and vehicle ownership from Firestore; it
never accepts a caller-provided VIN or owner.

1. Open `/onboarding` and select **Inspect telemetry configuration** for one
   paired vehicle.
2. Woodhouse reads current config and telemetry errors, then renders the
   complete redacted desired/current diff. CA PEM is represented by its SHA-256
   and certificate count.
3. Apply requires the exact displayed config hash plus a checked confirmation.
   The request contains one VIN and passes through Tesla's official Vehicle
   Command Proxy, which converts it to the signed JWS endpoint.
4. Woodhouse polls the GET endpoint until `synced=true`, confirms the returned
   configuration is equivalent, inspects telemetry errors, and only then
   persists `telemetry_config_version`, full config hash, field-config hash,
   and trust-profile ID/hash in the trusted vehicle record.
5. Repair/reapply repeats the guarded exact-hash flow. Remove has its own
   explicit confirmation and never runs from CI.

One vehicle's failure does not prevent inspection or repair of another. Every
apply, repair, automated transport reconciliation, and removal has an attempted
audit row written before the Tesla call and a terminal result/error category.
The browser control path distinguishes an out-of-boundary vehicle (`403`), a
Tesla connection requiring renewed authorization (`401`), safe configuration
conflicts (`409`), and onboarding/upstream failures (`502`). Audit rows preserve
stable `cross_user_access_denied`, `reauthorization_required`, and
`onboarding_error` categories instead of collapsing these operator actions into
generic failures.

## Server trust and certificate renewal

`telemetry-server-ca-profile` is a Secret Manager value containing CA
certificates only. It must never contain the expiring server leaf or a private
key. `telemetry_trust_profile_id` is the public versioned identifier for those
exact certificates. The canonical hash sorts certificate DER encodings, so PEM
spacing and order do not create false drift.

Before any ACME candidate can become the active edge certificate, the renewal
job:

1. validates SAN, lifetime, private-key match, and full chain;
2. validates the leaf plus its presented intermediates against the exact
   configured CA profile using normal OpenSSL verification and Tesla's
   `-partial_chain` fallback behavior;
3. records the trust-profile ID/hash in the atomic certificate release;
4. when that profile differs from the active release, requires a separate
   `telemetry-trust-readiness` manifest with the exact ID/hash and required
   vehicle count;
5. refuses activation and leaves the previous release served if validation or
   readiness fails.

Routine compatible leaf renewal never reads Tesla OAuth state and never calls
Tesla. A public-CA intermediate/root transition may be a genuine trust-profile
migration even though normal browsers would accept it.

### Transport migration

A hostname, port, or CA-profile change is separate from `broad-v1`. The
control-plane reconciler retains the exact persisted field hash, requires an
explicit per-vehicle transport-maintenance opt-in, updates a selected canary
first, waits for `synced=true`, inspects errors, and then processes remaining
vehicles independently. Its result is `ready_for_server_cutover` only when all
required vehicles report the new exact transport profile. Any opt-out, field
drift, error, or timeout returns `blocked`; the old server trust must remain
active. Only after results for every approved user's required vehicles are
accounted for may the operator publish the matching readiness manifest. This
separate global gate prevents a per-user reconciliation from authorizing a
fleet-wide server cutover.

First enrollment, removal, and every field/frequency change remain explicit
regardless of transport-maintenance opt-in. CI builds and tests configuration
code but never invokes any real Tesla configuration endpoint.

## First-vehicle operator checkpoint

Do not apply a real vehicle configuration merely because Phase 8 code is
deployed.

Before the first apply:

- create and validate the CA-only Secret Manager profile;
- enable `enable_fleet_telemetry_control` and set the versioned
  `telemetry_trust_profile_id` in Terraform;
- confirm the telemetry hostname/443 endpoint passes Tesla's pinned
  `check_server_cert.sh` using that exact CA PEM;
- confirm the selected vehicle is active, Virtual Key paired, Fleet Telemetry
  client 1.0.0+, and the Tesla grant contains `vehicle_device_data` plus
  `vehicle_location`;
- inspect and save the exact desired/current diff shown by Woodhouse;
- select one vehicle and explicitly approve its displayed config hash.

After apply, require `synced=true`, no relevant telemetry errors, and at least
one genuine observation from that VIN in the correct user's
`raw_telemetry_events`. Verify both source and ingestion timestamps, the
persisted configuration version/hash, and no dataset/table expiration. Until
all of that evidence exists, the live Phase 8 checkpoint has not passed.
