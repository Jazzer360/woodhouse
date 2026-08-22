# MCP Tool Catalog

**Status:** Phase 6 typed live surface.

The gateway implements stateless MCP Streamable HTTP JSON-RPC at `POST /mcp`.
It publishes RFC 9728 protected-resource metadata and uses OAuth 2.1
authorization-code + PKCE through the configured authorization server. Every
tool declares `securitySchemes: [{type: oauth2, scopes: [mcp:access]}]`.
Unauthenticated tool calls return an MCP `mcp/www_authenticate` challenge;
invalid bearer requests also receive an HTTP `WWW-Authenticate` challenge.
Issuer, audience, expiry, signature, and scope are validated before the manual
allowlist. Tools never
accept a user ID, email, dataset ID, VIN, ownership claim, Tesla token, or
arbitrary HTTP method/path. Current state always comes from Fleet API.

## Common vehicle-selection rule

Vehicle-scoped tools accept an optional opaque internal `vehicle_id`. The
gateway resolves it against trusted Firestore ownership. Omission succeeds only
when the authenticated user has exactly one active vehicle; zero vehicles fails,
and two or more returns `vehicle_ambiguous` with the eligible opaque IDs. The
gateway never guesses from last use or accepts a VIN.

## Read tools

Reads are not command-audited. Safe GET reads use the typed client's bounded
retry policy. `requires_awake` means the gateway does not wake the vehicle; an
asleep/unavailable response is returned and `tesla_wake_up` must be requested
separately.

| Coverage row | MCP tool | Scope | Vehicle | Wake | Retry |
|---|---|---|---|---|---|
| `drivers` | `tesla_drivers` | `vehicle_device_data` | selected | never | safe read |
| `fleet_status` | `tesla_fleet_status` | `vehicle_device_data` | selected | never | safe read |
| `fleet_telemetry_config get` | `tesla_fleet_telemetry_config_get` | `vehicle_device_data` | selected | never | safe read |
| `fleet_telemetry_errors` | `tesla_fleet_telemetry_errors` | `vehicle_device_data` | selected | never | safe read |
| `list` | `tesla_list_vehicles` | `vehicle_device_data` | all owned | never | safe read |
| `mobile_enabled` | `tesla_mobile_enabled` | `vehicle_device_data` | selected | requires awake | safe read |
| `nearby_charging_sites` | `tesla_nearby_charging_sites` | `vehicle_device_data` | selected | requires awake | safe read |
| `recent_alerts` | `tesla_recent_alerts` | `vehicle_device_data` | selected | requires awake | safe read |
| `release_notes` | `tesla_release_notes` | `vehicle_device_data` | selected | requires awake | safe read |
| `service_data` | `tesla_service_data` | `vehicle_device_data` | selected | requires awake | safe read |
| `vehicle` | `tesla_vehicle` | `vehicle_device_data` | selected | never | safe read |
| `vehicle_data` | `tesla_vehicle_data` | `vehicle_device_data`; `vehicle_location` when `location_data` is requested | selected | requires awake | safe read |
| `feature_config` | `tesla_feature_config` | `user_data` | account | never | safe read |
| `me` | `tesla_me` | `user_data` | account | never | safe read |
| `orders` | `tesla_orders` | `user_data` | account | never | safe read |
| `charging_history` | `tesla_charging_history` | `vehicle_charging_cmds` | selected | never | safe read |
| `charging_invoice` | `tesla_charging_invoice` | `vehicle_charging_cmds` | account-owned invoice | never | safe read |

`tesla_vehicle_data` requires one or more explicitly named data sections; it is
not a broad polling operation. `tesla_list_vehicles` calls Tesla live and
intersects the result with the authenticated user's trusted registry before
returning internal IDs.

## Write tools and audit

`wake_up` maps to `tesla_wake_up`. It uses `vehicle_device_data`, is an explicit
write, is never retried, and receives the same audit treatment as commands.

Every MCP vehicle-command row maps one-to-one to `tesla_<matrix-command-name>`.
The name is an allowlisted registry entry bound to one typed client method and
input schema; this naming convention is not a generic passthrough. All command
tools:

- are vehicle-scoped and require both their command scope and
  `vehicle_device_data` for the live-state/wake preflight;
- fetch the live vehicle state first and, when it is not online, audit and send
  one automatic `tesla_wake_up`, then poll every 10 seconds for at most 60
  seconds before sending the command;
- send the requested command exactly once and never retry it after dispatch,
  because a missing response does not prove non-execution;
- route through the instance-local, non-ingress official Vehicle Command Proxy;
- create an `attempted` Firestore audit record before contacting Tesla, then
  finalize it as `success`, `rejected`, or `failure`;
- record timestamp, server-derived user ID, internal vehicle ID, tool name,
  redacted arguments, result/error category, correlation ID, and
  `source=chatgpt-mcp`;
- never audit PINs, passwords, location coordinates, tokens, VINs, calendar
  payloads, or response bodies.

Charging command scope `vehicle_charging_cmds` applies to:

```text
add_charge_schedule
charge_max_range
charge_port_door_close
charge_port_door_open
charge_standard
charge_start
charge_stop
remove_charge_schedule
set_charge_limit
set_charging_amps
```

Every other exposed command uses `vehicle_cmds`.

### Risk classes

Normal reversible operations require clear user intent and no redundant second
confirmation. They include climate, charging, schedule, media, navigation,
lock, lights/horn, Sentry, cabin settings, and other Tier 1 rows in the coverage
matrix.

The following security-sensitive tools require
`explicit_current_turn_intent=true`. The tool description instructs the MCP
client to set it only for an unambiguous request for that exact operation in the
current user turn:

```text
tesla_actuate_trunk
tesla_cancel_software_update
tesla_door_unlock
tesla_guest_mode
tesla_parental_controls_activate
tesla_parental_controls_deactivate
tesla_parental_controls_enable_setting
tesla_parental_controls_set_speed_limit
tesla_remote_start_drive
tesla_schedule_software_update
tesla_set_pin_to_drive
tesla_set_valet_mode
tesla_speed_limit_activate
tesla_speed_limit_clear_pin
tesla_speed_limit_deactivate
tesla_speed_limit_set_limit
tesla_sun_roof_control
tesla_trigger_homelink
tesla_window_control
```

## Intentionally absent

All `Internal`, `Excluded`, compatibility-only, business-only, and out-of-scope
rows remain absent. In particular, there is no generic `call_tesla_api`, signed
command, driver/share administration, data-erasure, admin PIN reset, direct
telemetry configuration, partner administration, or arbitrary URL/body tool.

The machine-checkable coverage test parses `docs/fleet-api-coverage.md` and
requires every `Exposure=MCP` row to have exactly one registry mapping while
also asserting the excluded command set is absent.

## First live MCP operator checkpoint

Run this only after the Phase 6 revision and command-proxy sidecar are deployed.
Do not paste any token or secret into chat.

1. Configure the MCP client with endpoint
   `https://woodhouse.derekjass.com/mcp`, Streamable HTTP transport, and a fresh
   Google OIDC ID token for the configured audience as its bearer credential.
   If using the local PowerShell smoke client, enter the token without echo:

   ```powershell
   $secureToken = Read-Host "Paste a fresh Google ID token locally" -AsSecureString
   $pointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secureToken)
   try {
     $token = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($pointer)
   } finally {
     [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pointer)
   }
   $headers = @{
     Authorization = "Bearer $token"
     "Content-Type" = "application/json"
     Accept = "application/json, text/event-stream"
   }
   ```

2. Verify MCP initialization and `tools/list`. The result must identify
   `tesla-personal-platform`, list the typed tools, and contain no generic Tesla
   passthrough.

3. Make one real read-only call first. For the currently registered vehicle:

   ```powershell
   $readBody = @{
     jsonrpc = "2.0"
     id = 1
     method = "tools/call"
     params = @{
       name = "tesla_vehicle"
       arguments = @{ vehicle_id = "<owned-internal-vehicle-id>" }
     }
   } | ConvertTo-Json -Depth 6
   $readResult = Invoke-RestMethod -Method Post -Uri "https://woodhouse.derekjass.com/mcp" -Headers $headers -Body $readBody
   $readResult.result.structuredContent | Format-List
   ```

   Stop if this fails, returns another user's vehicle, or reports stale
   historical data instead of a Fleet API response.

4. If the account has multiple vehicles, deliberately omit `vehicle_id` from
   `tesla_vehicle`. Verify `isError=true` and `error=vehicle_ambiguous`. Do not
   continue if the gateway guesses a vehicle.

5. Only after the read succeeds, ask the operator to choose and explicitly
   approve one low-risk reversible command in the current turn. Suitable smoke
   choices include climate start/stop, charge start/stop when plugged in, charge
   limit, door lock, or a media control. Do not suggest or run unlock, remote
   start/keyless driving, trunk/frunk, HomeLink, window,
   PIN/valet/parental/speed-limit, or another security-sensitive command for
   smoke testing. If the vehicle is asleep, verify the result includes a
   `wake_correlation_id` in addition to the command `correlation_id`.

6. Call only the operator-selected typed tool. Verify Tesla's returned
   `successful` value and retain its returned `correlation_id`. Do not retry an
   indeterminate command.

7. In Google Cloud Console, open Firestore Studio, collection
   `tesla_command_audits`, and locate the record whose `correlation_id` matches.
   Verify its server-derived user/vehicle IDs, tool, source, final result, and
   redacted parameters. No PIN, token, private key, raw location, or VIN may be
   present.

The checkpoint passes only when the read is ownership-correct, any applicable
ambiguity test rejects guessing, the explicitly chosen low-risk command succeeds
or returns a clear Tesla rejection, and the matching redacted audit exists.
