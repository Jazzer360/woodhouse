# Tesla Fleet API Coverage Contract

**Audit date:** 2026-08-22
**Vehicle command count on Tesla's current page:** 72

**Phase 5 full audit:** 2026-08-22. The current official Vehicle Endpoints,
Vehicle Commands, User Endpoints, Partner Endpoints, and Charging Endpoints
pages and their published endpoint schema were compared with every row below.
The documented surface remains 22 vehicle endpoint operations, 4 user
operations, 4 partner operations, 3 charging operations, and 72 vehicle
commands. No row additions, removals, method changes, or path changes were
required. `charging_sessions` remains business-fleet-only, and
`set_scheduled_charging`, `set_scheduled_departure`, and direct
`fleet_telemetry_config_jws` remain compatibility-only/not recommended.

This document is a completeness contract for the typed Tesla client and MCP surface.

**Implementation status:** Phase 5 implements and mocks every `Required` typed-client
row, plus the three `Compatibility` rows directed by this matrix. Phase 6 remains
responsible for Vehicle Command Proxy integration and intentional typed MCP exposure;
Phase 8 remains responsible for applying broad Fleet Telemetry configuration. Typed
support in this package does not make `Internal` or `Excluded` operations MCP-callable.

Before implementing or declaring Fleet API work complete, re-audit the current official pages:

- https://developer.tesla.com/docs/fleet-api/endpoints/vehicle-endpoints
- https://developer.tesla.com/docs/fleet-api/endpoints/vehicle-commands
- https://developer.tesla.com/docs/fleet-api/endpoints/user-endpoints
- https://developer.tesla.com/docs/fleet-api/endpoints/partner-endpoints
- https://developer.tesla.com/docs/fleet-api/endpoints/charging-endpoints

Classification:

- **Required / MCP** — typed client + typed MCP exposure (possibly grouped logically).
- **Required / Internal** — typed client required, but not a direct arbitrary MCP tool.
- **Required / Excluded** — typed client support may be useful, intentionally not MCP-exposed initially.
- **Compatibility** — support only where useful for older/deprecated behavior; prefer Tesla's recommended replacement.
- **Business-only / Out of scope** — not required for personal Tesla accounts.

Tesla Energy-product APIs are intentionally outside this vehicle-focused project unless added later.

---

## Vehicle endpoints

| Endpoint | Method | Path | Implementation | Exposure | Notes |
|---|---|---|---|---|---|
| drivers | GET | /api/1/vehicles/{vin}/drivers | Required | MCP | Read-only driver list; owner-limited upstream. |
| drivers remove | DELETE | /api/1/vehicles/{vin}/drivers | Required | Excluded | Driver/access administration; typed client support, not MCP initially. |
| fleet_status | POST | /api/1/vehicles/fleet_status | Required | MCP | Key/protocol/telemetry capability state. |
| fleet_telemetry_config create | POST | /api/1/vehicles/fleet_telemetry_config | Required | Internal | Use through Vehicle Command Proxy for config create/update. |
| fleet_telemetry_config delete | DELETE | /api/1/vehicles/{vin}/fleet_telemetry_config | Required | Internal | Admin/config repair. |
| fleet_telemetry_config get | GET | /api/1/vehicles/{vin}/fleet_telemetry_config | Required | MCP | Expose as telemetry status/config read. |
| fleet_telemetry_config_jws | POST | /api/1/vehicles/fleet_telemetry_config_jws | Compatibility | Internal | Tesla does not recommend direct use; proxy handles recommended path. |
| fleet_telemetry_errors | GET | /api/1/vehicles/{vin}/fleet_telemetry_errors | Required | MCP | Telemetry diagnostics. |
| list | GET | /api/1/vehicles | Required | MCP | Vehicle enumeration; paginated. |
| mobile_enabled | GET | /api/1/vehicles/{vin}/mobile_enabled | Required | MCP | Read current mobile-access capability. |
| nearby_charging_sites | GET | /api/1/vehicles/{vin}/nearby_charging_sites | Required | MCP | Nearby charging. |
| recent_alerts | GET | /api/1/vehicles/{vin}/recent_alerts | Required | MCP | Recent vehicle alerts. |
| release_notes | GET | /api/1/vehicles/{vin}/release_notes | Required | MCP | Firmware release notes. |
| service_data | GET | /api/1/vehicles/{vin}/service_data | Required | MCP | Service status/info. |
| share_invites | GET | /api/1/vehicles/{vin}/invitations | Required | Excluded | Vehicle-sharing administration; client support only initially. |
| share_invites create | POST | /api/1/vehicles/{vin}/invitations | Required | Excluded | Vehicle-sharing administration. |
| share_invites redeem | POST | /api/1/invitations/redeem | Required | Excluded | Vehicle-sharing administration. |
| share_invites revoke | POST | /api/1/vehicles/{vin}/invitations/{id}/revoke | Required | Excluded | Vehicle-sharing administration. |
| signed_command | POST | /api/1/vehicles/{vin}/signed_command | Required | Internal | Generic transport used by official Vehicle Command Protocol/proxy; never MCP passthrough. |
| vehicle | GET | /api/1/vehicles/{vin} | Required | MCP | Vehicle metadata. |
| vehicle_data | GET | /api/1/vehicles/{vin}/vehicle_data | Required | MCP | Targeted live data; avoid broad polling. |
| wake_up | POST | /api/1/vehicles/{vin}/wake_up | Required | MCP | Explicit wake tool; use intentionally. |

## User endpoints

| Endpoint | Method | Path | Implementation | Exposure | Notes |
|---|---|---|---|---|---|
| feature_config | GET | /api/1/users/feature_config | Required | MCP | Read-only user feature config. |
| me | GET | /api/1/users/me | Required | MCP | User/account summary. |
| orders | GET | /api/1/users/orders | Required | MCP | Active Tesla orders; harmless account read. |
| region | GET | /api/1/users/region | Required | Internal | Determine correct Fleet API base URL; may also be exposed diagnostically. |

## Partner endpoints

| Endpoint | Method | Path | Implementation | Exposure | Notes |
|---|---|---|---|---|---|
| fleet_telemetry_error_vins | GET | /api/1/partner_accounts/fleet_telemetry_error_vins | Required | Internal | Application telemetry diagnostics. |
| fleet_telemetry_errors | GET | /api/1/partner_accounts/fleet_telemetry_errors | Required | Internal | Application telemetry diagnostics. |
| public_key | GET | /api/1/partner_accounts/public_key?domain={domain} | Required | Internal | Verify app registration/key. |
| register | POST | /api/1/partner_accounts | Required | Internal | One-time/idempotent app registration admin flow. |

## Charging endpoints

| Endpoint | Method | Path | Implementation | Exposure | Notes |
|---|---|---|---|---|---|
| charging_history | GET | /api/1/dx/charging/history | Required | MCP | Tesla charging history. |
| charging_invoice | GET | /api/1/dx/charging/invoice/{id} | Required | MCP | Charging invoice PDF/resource. |
| charging_sessions | GET | /api/1/dx/charging/sessions | Business-only | Out of scope | Tesla documents this as business fleet-owner only. |

## Vehicle commands — all 72 currently documented commands

| Command | Method | Path | Implementation | Exposure | Risk | Notes |
|---|---|---|---|---|---|---|
| actuate_trunk | POST | /api/1/vehicles/{vin}/command/actuate_trunk | Required | MCP | Tier 2 |  |
| add_charge_schedule | POST | /api/1/vehicles/{vin}/command/add_charge_schedule | Required | MCP | Tier 1 |  |
| add_precondition_schedule | POST | /api/1/vehicles/{vin}/command/add_precondition_schedule | Required | MCP | Tier 1 |  |
| adjust_volume | POST | /api/1/vehicles/{vin}/command/adjust_volume | Required | MCP | Tier 1 |  |
| auto_conditioning_start | POST | /api/1/vehicles/{vin}/command/auto_conditioning_start | Required | MCP | Tier 1 |  |
| auto_conditioning_stop | POST | /api/1/vehicles/{vin}/command/auto_conditioning_stop | Required | MCP | Tier 1 |  |
| cancel_software_update | POST | /api/1/vehicles/{vin}/command/cancel_software_update | Required | MCP | Tier 2 |  |
| charge_max_range | POST | /api/1/vehicles/{vin}/command/charge_max_range | Required | MCP | Tier 1 |  |
| charge_port_door_close | POST | /api/1/vehicles/{vin}/command/charge_port_door_close | Required | MCP | Tier 1 |  |
| charge_port_door_open | POST | /api/1/vehicles/{vin}/command/charge_port_door_open | Required | MCP | Tier 1 |  |
| charge_standard | POST | /api/1/vehicles/{vin}/command/charge_standard | Required | MCP | Tier 1 |  |
| charge_start | POST | /api/1/vehicles/{vin}/command/charge_start | Required | MCP | Tier 1 |  |
| charge_stop | POST | /api/1/vehicles/{vin}/command/charge_stop | Required | MCP | Tier 1 |  |
| clear_pin_to_drive_admin | POST | /api/1/vehicles/{vin}/command/clear_pin_to_drive_admin | Required | Excluded | Admin-sensitive | Typed client support may exist, but do not MCP-expose initially. |
| door_lock | POST | /api/1/vehicles/{vin}/command/door_lock | Required | MCP | Tier 1 |  |
| door_unlock | POST | /api/1/vehicles/{vin}/command/door_unlock | Required | MCP | Tier 2 | Security-sensitive unlock. |
| erase_user_data | POST | /api/1/vehicles/{vin}/command/erase_user_data | Required | Excluded | Admin-sensitive | Typed client support may exist, but do not MCP-expose initially. |
| flash_lights | POST | /api/1/vehicles/{vin}/command/flash_lights | Required | MCP | Tier 1 |  |
| guest_mode | POST | /api/1/vehicles/{vin}/command/guest_mode | Required | MCP | Tier 2 |  |
| honk_horn | POST | /api/1/vehicles/{vin}/command/honk_horn | Required | MCP | Tier 1 |  |
| media_next_fav | POST | /api/1/vehicles/{vin}/command/media_next_fav | Required | MCP | Tier 1 |  |
| media_next_track | POST | /api/1/vehicles/{vin}/command/media_next_track | Required | MCP | Tier 1 |  |
| media_prev_fav | POST | /api/1/vehicles/{vin}/command/media_prev_fav | Required | MCP | Tier 1 |  |
| media_prev_track | POST | /api/1/vehicles/{vin}/command/media_prev_track | Required | MCP | Tier 1 |  |
| media_toggle_playback | POST | /api/1/vehicles/{vin}/command/media_toggle_playback | Required | MCP | Tier 1 |  |
| media_volume_down | POST | /api/1/vehicles/{vin}/command/media_volume_down | Required | MCP | Tier 1 |  |
| media_volume_up | POST | /api/1/vehicles/{vin}/command/media_volume_up | Required | MCP | Tier 1 |  |
| navigation_gps_request | POST | /api/1/vehicles/{vin}/command/navigation_gps_request | Required | MCP | Tier 1 |  |
| navigation_request | POST | /api/1/vehicles/{vin}/command/navigation_request | Required | MCP | Tier 1 |  |
| navigation_sc_request | POST | /api/1/vehicles/{vin}/command/navigation_sc_request | Required | MCP | Tier 1 |  |
| navigation_waypoints_request | POST | /api/1/vehicles/{vin}/command/navigation_waypoints_request | Required | MCP | Tier 1 |  |
| parental_controls_activate | POST | /api/1/vehicles/{vin}/command/parental_controls_activate | Required | MCP | Tier 2 |  |
| parental_controls_clear_pin_admin | POST | /api/1/vehicles/{vin}/command/parental_controls_clear_pin_admin | Required | Excluded | Admin-sensitive | Typed client support may exist, but do not MCP-expose initially. |
| parental_controls_deactivate | POST | /api/1/vehicles/{vin}/command/parental_controls_deactivate | Required | MCP | Tier 2 |  |
| parental_controls_enable_setting | POST | /api/1/vehicles/{vin}/command/parental_controls_enable_setting | Required | MCP | Tier 2 |  |
| parental_controls_set_speed_limit | POST | /api/1/vehicles/{vin}/command/parental_controls_set_speed_limit | Required | MCP | Tier 2 |  |
| remote_auto_seat_climate_request | POST | /api/1/vehicles/{vin}/command/remote_auto_seat_climate_request | Required | MCP | Tier 1 |  |
| remote_auto_steering_wheel_heat_climate_request | POST | /api/1/vehicles/{vin}/command/remote_auto_steering_wheel_heat_climate_request | Required | MCP | Tier 1 |  |
| remote_boombox | POST | /api/1/vehicles/{vin}/command/remote_boombox | Required | MCP | Tier 1 |  |
| remote_seat_cooler_request | POST | /api/1/vehicles/{vin}/command/remote_seat_cooler_request | Required | MCP | Tier 1 |  |
| remote_seat_heater_request | POST | /api/1/vehicles/{vin}/command/remote_seat_heater_request | Required | MCP | Tier 1 |  |
| remote_start_drive | POST | /api/1/vehicles/{vin}/command/remote_start_drive | Required | MCP | Tier 2 | Explicitly distinguish from climate start; keyless driving/security-sensitive. |
| remote_steering_wheel_heat_level_request | POST | /api/1/vehicles/{vin}/command/remote_steering_wheel_heat_level_request | Required | MCP | Tier 1 |  |
| remote_steering_wheel_heater_request | POST | /api/1/vehicles/{vin}/command/remote_steering_wheel_heater_request | Required | MCP | Tier 1 |  |
| remove_charge_schedule | POST | /api/1/vehicles/{vin}/command/remove_charge_schedule | Required | MCP | Tier 1 |  |
| remove_precondition_schedule | POST | /api/1/vehicles/{vin}/command/remove_precondition_schedule | Required | MCP | Tier 1 |  |
| reset_pin_to_drive_pin | POST | /api/1/vehicles/{vin}/command/reset_pin_to_drive_pin | Required | Excluded | Admin-sensitive | Typed client support may exist, but do not MCP-expose initially. |
| reset_valet_pin | POST | /api/1/vehicles/{vin}/command/reset_valet_pin | Required | Excluded | Admin-sensitive | Typed client support may exist, but do not MCP-expose initially. |
| schedule_software_update | POST | /api/1/vehicles/{vin}/command/schedule_software_update | Required | MCP | Tier 2 |  |
| set_bioweapon_mode | POST | /api/1/vehicles/{vin}/command/set_bioweapon_mode | Required | MCP | Tier 1 |  |
| set_cabin_overheat_protection | POST | /api/1/vehicles/{vin}/command/set_cabin_overheat_protection | Required | MCP | Tier 1 |  |
| set_charge_limit | POST | /api/1/vehicles/{vin}/command/set_charge_limit | Required | MCP | Tier 1 |  |
| set_charging_amps | POST | /api/1/vehicles/{vin}/command/set_charging_amps | Required | MCP | Tier 1 |  |
| set_climate_keeper_mode | POST | /api/1/vehicles/{vin}/command/set_climate_keeper_mode | Required | MCP | Tier 1 |  |
| set_cop_temp | POST | /api/1/vehicles/{vin}/command/set_cop_temp | Required | MCP | Tier 1 |  |
| set_pin_to_drive | POST | /api/1/vehicles/{vin}/command/set_pin_to_drive | Required | MCP | Tier 2 |  |
| set_preconditioning_max | POST | /api/1/vehicles/{vin}/command/set_preconditioning_max | Required | MCP | Tier 1 |  |
| set_scheduled_charging | POST | /api/1/vehicles/{vin}/command/set_scheduled_charging | Compatibility | Internal | Compatibility | Tesla says newer schedule commands are preferred. |
| set_scheduled_departure | POST | /api/1/vehicles/{vin}/command/set_scheduled_departure | Compatibility | Internal | Compatibility | Tesla says newer schedule commands are preferred. |
| set_sentry_mode | POST | /api/1/vehicles/{vin}/command/set_sentry_mode | Required | MCP | Tier 1 |  |
| set_temps | POST | /api/1/vehicles/{vin}/command/set_temps | Required | MCP | Tier 1 |  |
| set_valet_mode | POST | /api/1/vehicles/{vin}/command/set_valet_mode | Required | MCP | Tier 2 |  |
| set_vehicle_name | POST | /api/1/vehicles/{vin}/command/set_vehicle_name | Required | MCP | Tier 1 |  |
| speed_limit_activate | POST | /api/1/vehicles/{vin}/command/speed_limit_activate | Required | MCP | Tier 2 |  |
| speed_limit_clear_pin | POST | /api/1/vehicles/{vin}/command/speed_limit_clear_pin | Required | MCP | Tier 2 |  |
| speed_limit_clear_pin_admin | POST | /api/1/vehicles/{vin}/command/speed_limit_clear_pin_admin | Required | Excluded | Admin-sensitive | Typed client support may exist, but do not MCP-expose initially. |
| speed_limit_deactivate | POST | /api/1/vehicles/{vin}/command/speed_limit_deactivate | Required | MCP | Tier 2 |  |
| speed_limit_set_limit | POST | /api/1/vehicles/{vin}/command/speed_limit_set_limit | Required | MCP | Tier 2 |  |
| sun_roof_control | POST | /api/1/vehicles/{vin}/command/sun_roof_control | Required | MCP | Tier 2 |  |
| trigger_homelink | POST | /api/1/vehicles/{vin}/command/trigger_homelink | Required | MCP | Tier 2 | Security/physical-access sensitive; consider location state. |
| upcoming_calendar_entries | POST | /api/1/vehicles/{vin}/command/upcoming_calendar_entries | Required | MCP | Tier 1 |  |
| window_control | POST | /api/1/vehicles/{vin}/command/window_control | Required | MCP | Tier 2 |  |


---

## Coverage completion rules

Phase 5 (typed client) is not complete until:

1. the matrix is re-audited against Tesla's current docs;
2. every **Required** endpoint has a typed client method and tests;
3. compatibility behavior follows the matrix;
4. every **Excluded** endpoint remains unavailable through the ChatGPT-facing MCP unless the architecture is deliberately revised;
5. a test or generated report detects when a Required matrix row lacks implementation.

Phase 6 (live MCP surface) is not complete until every **MCP** endpoint/command has a
typed MCP tool or an intentional grouped tool mapping documented in
`docs/mcp-tool-catalog.md`. Each **Internal** row must have a concrete internal call
path in its owning phase or a documented reason it remains staged.

Do not use this matrix to freeze Tesla's API forever. Update it whenever Tesla changes the official surface.
