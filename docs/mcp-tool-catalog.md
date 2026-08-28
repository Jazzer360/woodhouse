# MCP Tool Catalog

**Status:** official MCP Python SDK v2, Streamable HTTP at `/mcp`.

Woodhouse exposes 13 semantic tools. The SDK owns MCP protocol negotiation,
transport sessions, OAuth middleware, tool schemas, and validation of the
Pydantic request models. There is no application-owned JSON-RPC parser, schema
generator, or generic Tesla HTTP passthrough.

RFC 9728 protected-resource metadata is available at
`/.well-known/oauth-protected-resource` and
`/.well-known/oauth-protected-resource/mcp`. The SDK verifies the bearer token
before a tool handler runs. Woodhouse then resolves the immutable OIDC identity
through the manual allowlist and derives `user_id` and `dataset_id` on the
server. Tools never accept those values, an email, a VIN ownership assertion,
or a Tesla credential.

## Public tools

| Tool | Purpose | Input model |
|---|---|---|
| `get_tesla_account` | Account metadata and owned-vehicle discovery | `action`: `feature_config`, `me`, `orders`, or `list_vehicles` |
| `get_vehicle_status` | Live per-vehicle reads without implicit wake | `action` selects vehicle metadata, Fleet status, telemetry status/errors, mobile capability, nearby charging, alerts, release notes, service data, or targeted vehicle data |
| `get_charging_records` | Charging history and invoice reads | `action`: `charging_history` or `charging_invoice` |
| `control_vehicle_access` | Locks, openings, HomeLink, keyless drive, lights, horn, and Guest Mode | typed `action` plus only the family fields needed by that operation |
| `control_vehicle_climate` | Cabin, seat, steering-wheel, keeper, and overheat controls | typed `action` plus climate fields |
| `control_vehicle_charging` | Charging state, limits/current, port, and supported schedule controls | typed `action` plus charging/schedule fields |
| `control_vehicle_media` | Playback, favorites, volume, and boombox | typed `action` plus media fields |
| `control_vehicle_navigation` | Coordinates, structured destinations, Superchargers, and waypoints | typed `action` plus navigation fields |
| `control_vehicle_security` | Sentry, PIN, valet, parental, and speed-limit controls | typed `action`, required PIN/settings fields, and current-turn intent |
| `control_vehicle_settings` | Software update, vehicle name, sunroof, and calendar integration | typed `action` plus setting fields |
| `wake_vehicle` | Explicitly wake an owned vehicle | optional internal `vehicle_id` |
| `get_analytics_schema` | Describe the authenticated user's historical catalog | no input |
| `run_analytics_query` | Run one bounded read-only Standard SQL query | `sql` only |

The complete Tesla endpoint/command coverage remains in
[`fleet-api-coverage.md`](fleet-api-coverage.md). Each matrix row marked `MCP`
maps to exactly one private operation policy and then to one of the semantic
families above. The private mapping records required Tesla scope, vehicle
scope, wake behavior, risk, retry policy, audit behavior, typed client request,
and exclusions. It is not itself advertised as an MCP tool surface.

## Vehicle selection and current state

Vehicle-family requests accept an optional opaque internal `vehicle_id`.
Woodhouse resolves it against trusted Firestore ownership. Omission succeeds
only when exactly one active eligible vehicle exists; zero vehicles fails and
multiple vehicles return `vehicle_ambiguous`. VINs and last-used-vehicle guesses
are forbidden.

Current state comes from Fleet API. A normal read never wakes the car. The
caller must invoke `wake_vehicle` explicitly when a read requires an awake
vehicle.

## Command safety and audit

Normal reversible controls require clear user intent. Security-sensitive
actions require `explicit_current_turn_intent=true`; the caller may set it only
when the current user turn unambiguously requests that exact action. Examples
include unlock, trunk/frunk, windows, HomeLink, keyless driving, PIN/valet,
parental/speed-limit changes, and software-update or sunroof operations marked
security-sensitive by the coverage matrix.

Before a command, the service checks ownership, required Tesla scopes, and live
connectivity. When the policy permits automatic wake, it records and sends at
most one wake request, polls for at most 60 seconds, then dispatches the intended
command exactly once. Commands are never retried after dispatch because a
missing response does not prove non-execution.

Every attempted write—success or failure—creates a Firestore audit record with:

- timestamp and correlation ID;
- server-derived user and internal vehicle IDs;
- private operation name and `source=chatgpt-mcp`;
- redacted parameters;
- result and safe error category.

PINs, passwords, coordinates, tokens, VINs, calendar/navigation payloads, and
response bodies are not written to audit or application logs.

## Historical analytics

Historical questions use only `get_analytics_schema` and
`run_analytics_query`. Dataset selection is server-derived. SQLGlot validates a
single Standard SQL `SELECT`/`WITH` AST, rejects qualified cross-dataset names,
DML/DDL, scripts, exports, external queries/connections, and remote functions,
then BigQuery dry-runs with byte and result limits before execution.

The model composes novel drive, charging, efficiency, media, location, and
cross-vehicle questions from the catalog. New statistics do not require new
MCP endpoints.

## Intentionally absent

There is no `call_tesla_api`, arbitrary method/path/body tool, signed-command
tool, raw SQL dataset selector, driver/share administration, data erasure,
admin PIN reset, partner administration, or direct Fleet Telemetry mutation.
Coverage rows marked `Internal`, `Excluded`, `Compatibility`, `Business-only`,
or out of scope remain off the public surface.

## First live operator checkpoint

Automated tests never contact a real vehicle. For the first deployed smoke test:

1. Connect the MCP client to `https://woodhouse.derekjass.com/mcp` and complete
   the configured authorization-code + PKCE login.
2. Confirm `tools/list` returns exactly the 13 tools above and no generic Tesla
   passthrough.
3. Run one read-only status request against an owned vehicle. With multiple
   vehicles, first verify omission returns an ambiguity.
4. Only after the read succeeds, obtain explicit current-turn approval for one
   operator-selected low-risk reversible command. Do not choose unlock,
   openings, HomeLink, keyless driving, or security/PIN controls for the smoke
   test.
5. Verify Tesla's safe result and the corresponding redacted audit record.
