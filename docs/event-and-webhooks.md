# Event and Webhook Model

## 1. Purpose

Direct Fleet Telemetry is valuable not only for historical storage but for realtime triggers that a polling-only integration cannot provide cleanly.

The webhook/event subsystem should stay lightweight and optional.

---

## 2. Raw history always wins

Event processing must never determine whether a raw telemetry record is retained.

```text
raw telemetry -> durable history
             \\-> semantic event derivation -> webhooks
```

A bug in webhook code may delay webhooks. It must not lose historical telemetry.

---

## 3. Minimal state

The telemetry processor may keep per-vehicle last-known event state in Firestore solely to detect transitions.

Examples:

```text
last lock state
last charge state
last gear/drive state
last known geofence membership
last connectivity state
last SOC threshold bucket
```

Do not turn Firestore into a duplicate historical telemetry warehouse.

---

## 4. Initial semantic events

Suggested initial events:

```text
charging.started
charging.stopped
charging.completed
vehicle.locked
vehicle.unlocked
drive.started
drive.ended
vehicle.online
vehicle.offline
geofence.arrived
geofence.departed
battery.soc_threshold_crossed
```

Add events only when there is a real use case.

---

## 5. Buffered telemetry / stale action prevention

Tesla may buffer telemetry while connectivity is unavailable and send it later.

Use `source_timestamp` to distinguish historical delivery from a fresh actionable event.

A stale state transition should still be stored historically, but should not necessarily fire a realtime webhook.

Implement an explicit actionability window and document it.

---

## 6. Webhook delivery

Webhook subscription fields:

```text
owner_user_id
subscription_id
event_types
target_url
enabled
secret_reference
created_at
last_success_at
failure_count
```

Delivery:

- JSON payload;
- stable event ID;
- occurred/source timestamp;
- delivery timestamp;
- HMAC-SHA256 signature;
- bounded exponential retry for transient errors;
- dead-letter after exhaustion;
- idempotent replay by event ID.

---

## 7. SSRF guardrails

Even for manually trusted users, webhook URLs are a network security boundary.

Reject by default:

- loopback;
- link-local;
- cloud metadata addresses;
- RFC1918 private networks unless deliberately enabled;
- non-HTTPS in production.

Validate resolved addresses and redirect behavior.
