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

The only default profile is `broad-v1`. It is capability-projected per vehicle;
it is not a plan, quota, or storage tier.

- Source catalog: 239 documented fields.
- Configured for a Fleet Telemetry 1.2.0+ passenger vehicle: 225 fields.
- Fleet Telemetry 1.0/1.1 projection: 223 fields; the two HW4 self-driving
  counters are omitted because Tesla introduced them in client 1.2.0.
- `delivery_policy` is `latest`, so unacknowledged buffered data is resent.
- Alert types are `service`, `customer`, and `service-fix`.
- Location-bearing fields require the owner's `vehicle_location` grant.
- The broad profile requires Fleet Telemetry client 1.0.0 or later because it
  depends on location `minimum_delta` and `delivery_policy=latest`.

The checked-in field catalog is
[`fleet_streaming_fields_v0_9_4.csv`](../packages/tesla-client/src/tesla_personal_platform/tesla_client/data/fleet_streaming_fields_v0_9_4.csv).
The configuration builder is
[`telemetry.py`](../packages/tesla-client/src/tesla_personal_platform/tesla_client/telemetry.py).
The operator screen always renders the exact desired/current field maps and
hashes before it enables Apply.

### Intentional exclusions

Only these documented fields are excluded:

- `LifetimeEnergyUsedDrive` and the 11 `Semitruck*` fields: Tesla documents
  them as Semi-only; Woodhouse's current tenant model is passenger vehicles.
- `RouteLastUpdated`: Tesla documents it as broken and not returning data.
- `PassengerSeatBelt`: Tesla documents it as incorrectly reporting the
  second-row-center state. Keeping a knowingly mislabeled safety signal would
  be worse than an explicit omission.

Cybertruck, Powershare, tonneau, off-road, and other hardware-specific fields
remain included. A multi-vehicle platform must not infer model capability from
the first vehicle; unsupported fields simply do not emit for that vehicle.
Experimental, deprecated, and `Unknown` proto members are not in the 239-field
documented catalog and are not subscribed.

### Tessie baseline and deviations

The operator-supplied Tessie response contained 93 fields. It was used only as
a cadence and delta reference; its hostname, CA, issuer, audience, and response
metadata are not stored in this repository. Woodhouse keeps useful Tessie
choices such as change-based 1-5 second security/state observation, 30-second
battery values, temperature deltas, TPMS throttling, and location drift.

Woodhouse deliberately expands beyond Tessie to preserve useful charging,
climate, navigation, powertrain, safety, service, preferences, configuration,
connectivity, and self-driving history. In particular, Tessie's sample omitted
all 35 documented powertrain fields and most of the available driving and
vehicle-configuration fields.

## Interval and minimum-delta policy

Tesla emits a configured value only after its interval has elapsed **and** the
value has changed. A short interval for a boolean or enum therefore reduces
transition latency without producing a constant sample stream. Numeric and
location deltas add a second noise threshold.

| Signal family | Normal interval | Delta policy and rationale |
|---|---:|---|
| Body, security, safety, gear | 1-5 s | Discrete change only; low latency matters and unchanged values are not emitted. |
| Location | 5 s | 10 m movement; useful route shape without GPS jitter. Origin/destination use 25 m. |
| Speed and driving dynamics | 5 s | Speed 1 mph; acceleration/pedal values use small meaningful deltas. |
| Powertrain | 10 s | Per-value deltas (normally 0.5, with current/torque/voltage overrides) suppress sensor noise while retaining diagnostic resolution. |
| Charging and battery | 30 s | Normally 0.1 units; SOC/BatteryLevel 0.5%, voltage 2 V, pack current/voltage 1. |
| Climate numeric values | 30 s | Normally 0.5 degrees/units; discrete modes use 5 s. |
| Media | 5-15 s | Metadata/playback is change-based; elapsed time uses a 5,000 ms delta and duration 1,000 ms. |
| TPMS | 60-300 s | Warning state is prompt; pressure uses 0.05 bar drift. |
| Service | 60-300 s | Slow-changing diagnostics; numeric noise is delta-gated. |
| User preferences | 30-60 s | Change-based and operationally non-urgent. |
| Vehicle configuration | 300 s | Change-based static metadata. |

Every documented integer, real, and Location field in the desired profile has
a `minimum_delta`. Integer settings/counters default to 1. Real-valued defaults
are category-specific, with named overrides in `telemetry.py`. This prevents
small numeric drift from dominating the stream while keeping the field itself.

High-volume considerations shown at the checkpoint:

- the 35 powertrain fields can each emit at most once per 10 seconds when their
  value also crosses its delta;
- Location can emit at most once per 5 seconds after at least 10 m movement;
- driving values can emit at most once per 5 seconds;
- slow/discrete fields normally emit only on a real state change or on the
  vehicle's initial telemetry snapshot.

Tesla bills telemetry by signals, not only payloads. The exact cost depends on
which supported values actually change. After the first real observation, use
Cloud Monitoring and raw row counts to measure actual volume before changing
the versioned profile. A field/frequency change always creates a new explicit
profile version and operator diff; it is never an automatic maintenance edit.

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
