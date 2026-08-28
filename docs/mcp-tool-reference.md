# MCP Tool Reference

**Generated from the live typed registry. Do not edit by hand.** Run
`uv run python scripts/dev/generate-mcp-tool-reference.py` after changing a tool.

This is the argument-level reference for ChatGPT-facing Woodhouse tools. Tesla
paths are shown for traceability, but callers cannot supply VINs, paths, methods,
tokens, user IDs, or dataset IDs. Woodhouse derives identity and ownership.
The endpoint mapping and behavior were re-audited against Tesla's official Fleet
API documentation on 2026-08-24.

## Common behavior

- OAuth requires `mcp:access`; the server separately enforces the Tesla scope shown.
- `vehicle_id` is an opaque internal ID. Omission works only for one eligible vehicle.
- Read tools never wake implicitly. Command tools perform one state check and at most
  one automatic wake, then dispatch the command exactly once.
- A missing command response is indeterminate and is never retried automatically.
- Security-sensitive tools require `explicit_current_turn_intent=true`.
- Expected safe errors include `vehicle_ambiguous`, `vehicle_unavailable`,
  `vehicle_not_owned`, `reauthorization_required`, validation errors, Tesla rejection,
  and indeterminate transport failure. Use `correlation_id` for redacted logs/audit.

## Tool index (84)

| Tool | Tesla operation | Scope | Risk | Wake |
|---|---|---|---|---|
| [`tesla_actuate_trunk`](#tesla-actuate-trunk) | `POST /api/1/vehicles/{vin}/command/actuate_trunk` | `vehicle_cmds` | `security_sensitive` | `auto_if_needed` |
| [`tesla_add_charge_schedule`](#tesla-add-charge-schedule) | `POST /api/1/vehicles/{vin}/command/add_charge_schedule` | `vehicle_charging_cmds` | `normal` | `auto_if_needed` |
| [`tesla_add_precondition_schedule`](#tesla-add-precondition-schedule) | `POST /api/1/vehicles/{vin}/command/add_precondition_schedule` | `vehicle_cmds` | `normal` | `auto_if_needed` |
| [`tesla_adjust_volume`](#tesla-adjust-volume) | `POST /api/1/vehicles/{vin}/command/adjust_volume` | `vehicle_cmds` | `normal` | `auto_if_needed` |
| [`tesla_auto_conditioning_start`](#tesla-auto-conditioning-start) | `POST /api/1/vehicles/{vin}/command/auto_conditioning_start` | `vehicle_cmds` | `normal` | `auto_if_needed` |
| [`tesla_auto_conditioning_stop`](#tesla-auto-conditioning-stop) | `POST /api/1/vehicles/{vin}/command/auto_conditioning_stop` | `vehicle_cmds` | `normal` | `auto_if_needed` |
| [`tesla_cancel_software_update`](#tesla-cancel-software-update) | `POST /api/1/vehicles/{vin}/command/cancel_software_update` | `vehicle_cmds` | `security_sensitive` | `auto_if_needed` |
| [`tesla_charge_max_range`](#tesla-charge-max-range) | `POST /api/1/vehicles/{vin}/command/charge_max_range` | `vehicle_charging_cmds` | `normal` | `auto_if_needed` |
| [`tesla_charge_port_door_close`](#tesla-charge-port-door-close) | `POST /api/1/vehicles/{vin}/command/charge_port_door_close` | `vehicle_charging_cmds` | `normal` | `auto_if_needed` |
| [`tesla_charge_port_door_open`](#tesla-charge-port-door-open) | `POST /api/1/vehicles/{vin}/command/charge_port_door_open` | `vehicle_charging_cmds` | `normal` | `auto_if_needed` |
| [`tesla_charge_standard`](#tesla-charge-standard) | `POST /api/1/vehicles/{vin}/command/charge_standard` | `vehicle_charging_cmds` | `normal` | `auto_if_needed` |
| [`tesla_charge_start`](#tesla-charge-start) | `POST /api/1/vehicles/{vin}/command/charge_start` | `vehicle_charging_cmds` | `normal` | `auto_if_needed` |
| [`tesla_charge_stop`](#tesla-charge-stop) | `POST /api/1/vehicles/{vin}/command/charge_stop` | `vehicle_charging_cmds` | `normal` | `auto_if_needed` |
| [`tesla_charging_history`](#tesla-charging-history) | `GET /api/1/dx/charging/history` | `vehicle_charging_cmds` | `read_only` | `never` |
| [`tesla_charging_invoice`](#tesla-charging-invoice) | `GET /api/1/dx/charging/invoice/{invoice_id}` | `vehicle_charging_cmds` | `read_only` | `never` |
| [`tesla_door_lock`](#tesla-door-lock) | `POST /api/1/vehicles/{vin}/command/door_lock` | `vehicle_cmds` | `normal` | `auto_if_needed` |
| [`tesla_door_unlock`](#tesla-door-unlock) | `POST /api/1/vehicles/{vin}/command/door_unlock` | `vehicle_cmds` | `security_sensitive` | `auto_if_needed` |
| [`tesla_drivers`](#tesla-drivers) | `GET /api/1/vehicles/{vin}/drivers` | `vehicle_device_data` | `read_only` | `never` |
| [`tesla_feature_config`](#tesla-feature-config) | `GET /api/1/users/feature_config` | `user_data` | `read_only` | `never` |
| [`tesla_flash_lights`](#tesla-flash-lights) | `POST /api/1/vehicles/{vin}/command/flash_lights` | `vehicle_cmds` | `normal` | `auto_if_needed` |
| [`tesla_fleet_status`](#tesla-fleet-status) | `POST /api/1/vehicles/fleet_status` | `vehicle_device_data` | `read_only` | `never` |
| [`tesla_fleet_telemetry_config_get`](#tesla-fleet-telemetry-config-get) | `GET /api/1/vehicles/{vin}/fleet_telemetry_config` | `vehicle_device_data` | `read_only` | `never` |
| [`tesla_fleet_telemetry_errors`](#tesla-fleet-telemetry-errors) | `GET /api/1/vehicles/{vin}/fleet_telemetry_errors` | `vehicle_device_data` | `read_only` | `never` |
| [`tesla_guest_mode`](#tesla-guest-mode) | `POST /api/1/vehicles/{vin}/command/guest_mode` | `vehicle_cmds` | `security_sensitive` | `auto_if_needed` |
| [`tesla_honk_horn`](#tesla-honk-horn) | `POST /api/1/vehicles/{vin}/command/honk_horn` | `vehicle_cmds` | `normal` | `auto_if_needed` |
| [`tesla_list_vehicles`](#tesla-list-vehicles) | `GET /api/1/vehicles` | `vehicle_device_data` | `read_only` | `never` |
| [`tesla_me`](#tesla-me) | `GET /api/1/users/me` | `user_data` | `read_only` | `never` |
| [`tesla_media_next_fav`](#tesla-media-next-fav) | `POST /api/1/vehicles/{vin}/command/media_next_fav` | `vehicle_cmds` | `normal` | `auto_if_needed` |
| [`tesla_media_next_track`](#tesla-media-next-track) | `POST /api/1/vehicles/{vin}/command/media_next_track` | `vehicle_cmds` | `normal` | `auto_if_needed` |
| [`tesla_media_prev_fav`](#tesla-media-prev-fav) | `POST /api/1/vehicles/{vin}/command/media_prev_fav` | `vehicle_cmds` | `normal` | `auto_if_needed` |
| [`tesla_media_prev_track`](#tesla-media-prev-track) | `POST /api/1/vehicles/{vin}/command/media_prev_track` | `vehicle_cmds` | `normal` | `auto_if_needed` |
| [`tesla_media_toggle_playback`](#tesla-media-toggle-playback) | `POST /api/1/vehicles/{vin}/command/media_toggle_playback` | `vehicle_cmds` | `normal` | `auto_if_needed` |
| [`tesla_media_volume_down`](#tesla-media-volume-down) | `POST /api/1/vehicles/{vin}/command/media_volume_down` | `vehicle_cmds` | `normal` | `auto_if_needed` |
| [`tesla_media_volume_up`](#tesla-media-volume-up) | `POST /api/1/vehicles/{vin}/command/media_volume_up` | `vehicle_cmds` | `normal` | `auto_if_needed` |
| [`tesla_mobile_enabled`](#tesla-mobile-enabled) | `GET /api/1/vehicles/{vin}/mobile_enabled` | `vehicle_device_data` | `read_only` | `requires_awake` |
| [`tesla_navigation_gps_request`](#tesla-navigation-gps-request) | `POST /api/1/vehicles/{vin}/command/navigation_gps_request` | `vehicle_cmds` | `normal` | `auto_if_needed` |
| [`tesla_navigation_request`](#tesla-navigation-request) | `POST /api/1/vehicles/{vin}/command/navigation_request` | `vehicle_cmds` | `normal` | `auto_if_needed` |
| [`tesla_navigation_sc_request`](#tesla-navigation-sc-request) | `POST /api/1/vehicles/{vin}/command/navigation_sc_request` | `vehicle_cmds` | `normal` | `auto_if_needed` |
| [`tesla_navigation_waypoints_request`](#tesla-navigation-waypoints-request) | `POST /api/1/vehicles/{vin}/command/navigation_waypoints_request` | `vehicle_cmds` | `normal` | `auto_if_needed` |
| [`tesla_nearby_charging_sites`](#tesla-nearby-charging-sites) | `GET /api/1/vehicles/{vin}/nearby_charging_sites` | `vehicle_device_data` | `read_only` | `requires_awake` |
| [`tesla_orders`](#tesla-orders) | `GET /api/1/users/orders` | `user_data` | `read_only` | `never` |
| [`tesla_parental_controls_activate`](#tesla-parental-controls-activate) | `POST /api/1/vehicles/{vin}/command/parental_controls_activate` | `vehicle_cmds` | `security_sensitive` | `auto_if_needed` |
| [`tesla_parental_controls_deactivate`](#tesla-parental-controls-deactivate) | `POST /api/1/vehicles/{vin}/command/parental_controls_deactivate` | `vehicle_cmds` | `security_sensitive` | `auto_if_needed` |
| [`tesla_parental_controls_enable_setting`](#tesla-parental-controls-enable-setting) | `POST /api/1/vehicles/{vin}/command/parental_controls_enable_setting` | `vehicle_cmds` | `security_sensitive` | `auto_if_needed` |
| [`tesla_parental_controls_set_speed_limit`](#tesla-parental-controls-set-speed-limit) | `POST /api/1/vehicles/{vin}/command/parental_controls_set_speed_limit` | `vehicle_cmds` | `security_sensitive` | `auto_if_needed` |
| [`tesla_recent_alerts`](#tesla-recent-alerts) | `GET /api/1/vehicles/{vin}/recent_alerts` | `vehicle_device_data` | `read_only` | `requires_awake` |
| [`tesla_release_notes`](#tesla-release-notes) | `GET /api/1/vehicles/{vin}/release_notes` | `vehicle_device_data` | `read_only` | `requires_awake` |
| [`tesla_remote_auto_seat_climate_request`](#tesla-remote-auto-seat-climate-request) | `POST /api/1/vehicles/{vin}/command/remote_auto_seat_climate_request` | `vehicle_cmds` | `normal` | `auto_if_needed` |
| [`tesla_remote_auto_steering_wheel_heat_climate_request`](#tesla-remote-auto-steering-wheel-heat-climate-request) | `POST /api/1/vehicles/{vin}/command/remote_auto_steering_wheel_heat_climate_request` | `vehicle_cmds` | `normal` | `auto_if_needed` |
| [`tesla_remote_boombox`](#tesla-remote-boombox) | `POST /api/1/vehicles/{vin}/command/remote_boombox` | `vehicle_cmds` | `normal` | `auto_if_needed` |
| [`tesla_remote_seat_cooler_request`](#tesla-remote-seat-cooler-request) | `POST /api/1/vehicles/{vin}/command/remote_seat_cooler_request` | `vehicle_cmds` | `normal` | `auto_if_needed` |
| [`tesla_remote_seat_heater_request`](#tesla-remote-seat-heater-request) | `POST /api/1/vehicles/{vin}/command/remote_seat_heater_request` | `vehicle_cmds` | `normal` | `auto_if_needed` |
| [`tesla_remote_start_drive`](#tesla-remote-start-drive) | `POST /api/1/vehicles/{vin}/command/remote_start_drive` | `vehicle_cmds` | `security_sensitive` | `auto_if_needed` |
| [`tesla_remote_steering_wheel_heat_level_request`](#tesla-remote-steering-wheel-heat-level-request) | `POST /api/1/vehicles/{vin}/command/remote_steering_wheel_heat_level_request` | `vehicle_cmds` | `normal` | `auto_if_needed` |
| [`tesla_remote_steering_wheel_heater_request`](#tesla-remote-steering-wheel-heater-request) | `POST /api/1/vehicles/{vin}/command/remote_steering_wheel_heater_request` | `vehicle_cmds` | `normal` | `auto_if_needed` |
| [`tesla_remove_charge_schedule`](#tesla-remove-charge-schedule) | `POST /api/1/vehicles/{vin}/command/remove_charge_schedule` | `vehicle_charging_cmds` | `normal` | `auto_if_needed` |
| [`tesla_remove_precondition_schedule`](#tesla-remove-precondition-schedule) | `POST /api/1/vehicles/{vin}/command/remove_precondition_schedule` | `vehicle_cmds` | `normal` | `auto_if_needed` |
| [`tesla_schedule_software_update`](#tesla-schedule-software-update) | `POST /api/1/vehicles/{vin}/command/schedule_software_update` | `vehicle_cmds` | `security_sensitive` | `auto_if_needed` |
| [`tesla_service_data`](#tesla-service-data) | `GET /api/1/vehicles/{vin}/service_data` | `vehicle_device_data` | `read_only` | `requires_awake` |
| [`tesla_set_bioweapon_mode`](#tesla-set-bioweapon-mode) | `POST /api/1/vehicles/{vin}/command/set_bioweapon_mode` | `vehicle_cmds` | `normal` | `auto_if_needed` |
| [`tesla_set_cabin_overheat_protection`](#tesla-set-cabin-overheat-protection) | `POST /api/1/vehicles/{vin}/command/set_cabin_overheat_protection` | `vehicle_cmds` | `normal` | `auto_if_needed` |
| [`tesla_set_charge_limit`](#tesla-set-charge-limit) | `POST /api/1/vehicles/{vin}/command/set_charge_limit` | `vehicle_charging_cmds` | `normal` | `auto_if_needed` |
| [`tesla_set_charging_amps`](#tesla-set-charging-amps) | `POST /api/1/vehicles/{vin}/command/set_charging_amps` | `vehicle_charging_cmds` | `normal` | `auto_if_needed` |
| [`tesla_set_climate_keeper_mode`](#tesla-set-climate-keeper-mode) | `POST /api/1/vehicles/{vin}/command/set_climate_keeper_mode` | `vehicle_cmds` | `normal` | `auto_if_needed` |
| [`tesla_set_cop_temp`](#tesla-set-cop-temp) | `POST /api/1/vehicles/{vin}/command/set_cop_temp` | `vehicle_cmds` | `normal` | `auto_if_needed` |
| [`tesla_set_pin_to_drive`](#tesla-set-pin-to-drive) | `POST /api/1/vehicles/{vin}/command/set_pin_to_drive` | `vehicle_cmds` | `security_sensitive` | `auto_if_needed` |
| [`tesla_set_preconditioning_max`](#tesla-set-preconditioning-max) | `POST /api/1/vehicles/{vin}/command/set_preconditioning_max` | `vehicle_cmds` | `normal` | `auto_if_needed` |
| [`tesla_set_sentry_mode`](#tesla-set-sentry-mode) | `POST /api/1/vehicles/{vin}/command/set_sentry_mode` | `vehicle_cmds` | `normal` | `auto_if_needed` |
| [`tesla_set_temps`](#tesla-set-temps) | `POST /api/1/vehicles/{vin}/command/set_temps` | `vehicle_cmds` | `normal` | `auto_if_needed` |
| [`tesla_set_valet_mode`](#tesla-set-valet-mode) | `POST /api/1/vehicles/{vin}/command/set_valet_mode` | `vehicle_cmds` | `security_sensitive` | `auto_if_needed` |
| [`tesla_set_vehicle_name`](#tesla-set-vehicle-name) | `POST /api/1/vehicles/{vin}/command/set_vehicle_name` | `vehicle_cmds` | `normal` | `auto_if_needed` |
| [`tesla_speed_limit_activate`](#tesla-speed-limit-activate) | `POST /api/1/vehicles/{vin}/command/speed_limit_activate` | `vehicle_cmds` | `security_sensitive` | `auto_if_needed` |
| [`tesla_speed_limit_clear_pin`](#tesla-speed-limit-clear-pin) | `POST /api/1/vehicles/{vin}/command/speed_limit_clear_pin` | `vehicle_cmds` | `security_sensitive` | `auto_if_needed` |
| [`tesla_speed_limit_deactivate`](#tesla-speed-limit-deactivate) | `POST /api/1/vehicles/{vin}/command/speed_limit_deactivate` | `vehicle_cmds` | `security_sensitive` | `auto_if_needed` |
| [`tesla_speed_limit_set_limit`](#tesla-speed-limit-set-limit) | `POST /api/1/vehicles/{vin}/command/speed_limit_set_limit` | `vehicle_cmds` | `security_sensitive` | `auto_if_needed` |
| [`tesla_sun_roof_control`](#tesla-sun-roof-control) | `POST /api/1/vehicles/{vin}/command/sun_roof_control` | `vehicle_cmds` | `security_sensitive` | `auto_if_needed` |
| [`tesla_trigger_homelink`](#tesla-trigger-homelink) | `POST /api/1/vehicles/{vin}/command/trigger_homelink` | `vehicle_cmds` | `security_sensitive` | `auto_if_needed` |
| [`tesla_upcoming_calendar_entries`](#tesla-upcoming-calendar-entries) | `POST /api/1/vehicles/{vin}/command/upcoming_calendar_entries` | `vehicle_cmds` | `normal` | `auto_if_needed` |
| [`tesla_vehicle`](#tesla-vehicle) | `GET /api/1/vehicles/{vin}` | `vehicle_device_data` | `read_only` | `never` |
| [`tesla_vehicle_data`](#tesla-vehicle-data) | `GET /api/1/vehicles/{vin}/vehicle_data` | `vehicle_device_data` | `read_only` | `requires_awake` |
| [`tesla_wake_up`](#tesla-wake-up) | `POST /api/1/vehicles/{vin}/wake_up` | `vehicle_device_data` | `normal` | `explicit` |
| [`tesla_window_control`](#tesla-window-control) | `POST /api/1/vehicles/{vin}/command/window_control` | `vehicle_cmds` | `security_sensitive` | `auto_if_needed` |
| [`get_analytics_schema`](#get-analytics-schema) | `BigQuery read-only` | `mcp:access` | `read_only` | `never` |
| [`run_analytics_query`](#run-analytics-query) | `BigQuery read-only` | `mcp:access` | `read_only` | `never` |

## Detailed tools

### `tesla_actuate_trunk`

Open the front trunk or actuate the rear trunk/liftgate selected by `which_trunk`.

- Tesla operation: `POST /api/1/vehicles/{vin}/command/actuate_trunk`
- Tesla scope: `vehicle_cmds`
- Vehicle wake: `auto_if_needed`
- Risk: `security_sensitive`
- Retry: `never`
- Audit: `redacted_attempt_and_result`

Arguments:

| Name | Required | Type/constraints | Meaning |
|---|---:|---|---|
| `vehicle_id` | no | string | Opaque Woodhouse vehicle ID. Omit only when exactly one eligible vehicle exists. |
| `which_trunk` | yes | string; one of `front`, `rear` | Tesla request field. |
| `explicit_current_turn_intent` | yes | boolean; must be `true` | Set true only when the user unambiguously requested this exact security-sensitive operation in the current turn. |

Result: a sanitized structured result with `correlation_id`. Commands also return Tesla's success/reason outcome and, when an automatic wake was needed, `wake_correlation_id`.

### `tesla_add_charge_schedule`

Create or replace a location-bound recurring or one-time charging schedule.

- Tesla operation: `POST /api/1/vehicles/{vin}/command/add_charge_schedule`
- Tesla scope: `vehicle_charging_cmds`
- Vehicle wake: `auto_if_needed`
- Risk: `normal`
- Retry: `never`
- Audit: `redacted_attempt_and_result`

Arguments:

| Name | Required | Type/constraints | Meaning |
|---|---:|---|---|
| `vehicle_id` | no | string | Opaque Woodhouse vehicle ID. Omit only when exactly one eligible vehicle exists. |
| `lat` | yes | number | Latitude; sensitive and excluded from command audit parameters. |
| `lon` | yes | number | Longitude; sensitive and excluded from command audit parameters. |
| `id` | yes | integer | Tesla schedule or Supercharger identifier, depending on the tool. |
| `days_of_week` | yes | string | Tesla schedule weekday encoding. |
| `start_enabled` | yes | boolean | Tesla request field. |
| `start_time` | yes | integer | RFC 3339 timestamp for history reads, or minutes after local midnight for schedules. |
| `end_enabled` | yes | boolean | Tesla request field. |
| `end_time` | yes | integer | RFC 3339 timestamp for history reads, or minutes after local midnight for schedules. |
| `one_time` | yes | boolean | Tesla request field. |
| `enabled` | yes | boolean | Tesla request field. |

Result: a sanitized structured result with `correlation_id`. Commands also return Tesla's success/reason outcome and, when an automatic wake was needed, `wake_correlation_id`.

### `tesla_add_precondition_schedule`

Create or replace a location-bound recurring or one-time preconditioning schedule.

- Tesla operation: `POST /api/1/vehicles/{vin}/command/add_precondition_schedule`
- Tesla scope: `vehicle_cmds`
- Vehicle wake: `auto_if_needed`
- Risk: `normal`
- Retry: `never`
- Audit: `redacted_attempt_and_result`

Arguments:

| Name | Required | Type/constraints | Meaning |
|---|---:|---|---|
| `vehicle_id` | no | string | Opaque Woodhouse vehicle ID. Omit only when exactly one eligible vehicle exists. |
| `lat` | yes | number | Latitude; sensitive and excluded from command audit parameters. |
| `lon` | yes | number | Longitude; sensitive and excluded from command audit parameters. |
| `id` | yes | integer | Tesla schedule or Supercharger identifier, depending on the tool. |
| `days_of_week` | yes | string | Tesla schedule weekday encoding. |
| `precondition_time` | yes | integer | Preconditioning time in minutes after local midnight. |
| `one_time` | yes | boolean | Tesla request field. |
| `enabled` | yes | boolean | Tesla request field. |

Result: a sanitized structured result with `correlation_id`. Commands also return Tesla's success/reason outcome and, when an automatic wake was needed, `wake_correlation_id`.

### `tesla_adjust_volume`

Set the in-cabin media playback volume; Tesla may require an occupant and mobile access.

- Tesla operation: `POST /api/1/vehicles/{vin}/command/adjust_volume`
- Tesla scope: `vehicle_cmds`
- Vehicle wake: `auto_if_needed`
- Risk: `normal`
- Retry: `never`
- Audit: `redacted_attempt_and_result`

Arguments:

| Name | Required | Type/constraints | Meaning |
|---|---:|---|---|
| `vehicle_id` | no | string | Opaque Woodhouse vehicle ID. Omit only when exactly one eligible vehicle exists. |
| `volume` | yes | number | Tesla request field. |

Result: a sanitized structured result with `correlation_id`. Commands also return Tesla's success/reason outcome and, when an automatic wake was needed, `wake_correlation_id`.

### `tesla_auto_conditioning_start`

Start cabin climate preconditioning.

- Tesla operation: `POST /api/1/vehicles/{vin}/command/auto_conditioning_start`
- Tesla scope: `vehicle_cmds`
- Vehicle wake: `auto_if_needed`
- Risk: `normal`
- Retry: `never`
- Audit: `redacted_attempt_and_result`

Arguments:

| Name | Required | Type/constraints | Meaning |
|---|---:|---|---|
| `vehicle_id` | no | string | Opaque Woodhouse vehicle ID. Omit only when exactly one eligible vehicle exists. |

Result: a sanitized structured result with `correlation_id`. Commands also return Tesla's success/reason outcome and, when an automatic wake was needed, `wake_correlation_id`.

### `tesla_auto_conditioning_stop`

Stop cabin climate preconditioning.

- Tesla operation: `POST /api/1/vehicles/{vin}/command/auto_conditioning_stop`
- Tesla scope: `vehicle_cmds`
- Vehicle wake: `auto_if_needed`
- Risk: `normal`
- Retry: `never`
- Audit: `redacted_attempt_and_result`

Arguments:

| Name | Required | Type/constraints | Meaning |
|---|---:|---|---|
| `vehicle_id` | no | string | Opaque Woodhouse vehicle ID. Omit only when exactly one eligible vehicle exists. |

Result: a sanitized structured result with `correlation_id`. Commands also return Tesla's success/reason outcome and, when an automatic wake was needed, `wake_correlation_id`.

### `tesla_cancel_software_update`

Cancel an update-install countdown before installation has begun.

- Tesla operation: `POST /api/1/vehicles/{vin}/command/cancel_software_update`
- Tesla scope: `vehicle_cmds`
- Vehicle wake: `auto_if_needed`
- Risk: `security_sensitive`
- Retry: `never`
- Audit: `redacted_attempt_and_result`

Arguments:

| Name | Required | Type/constraints | Meaning |
|---|---:|---|---|
| `vehicle_id` | no | string | Opaque Woodhouse vehicle ID. Omit only when exactly one eligible vehicle exists. |
| `explicit_current_turn_intent` | yes | boolean; must be `true` | Set true only when the user unambiguously requested this exact security-sensitive operation in the current turn. |

Result: a sanitized structured result with `correlation_id`. Commands also return Tesla's success/reason outcome and, when an automatic wake was needed, `wake_correlation_id`.

### `tesla_charge_max_range`

Select Tesla's max-range charging mode for exceptional long-trip use.

- Tesla operation: `POST /api/1/vehicles/{vin}/command/charge_max_range`
- Tesla scope: `vehicle_charging_cmds`
- Vehicle wake: `auto_if_needed`
- Risk: `normal`
- Retry: `never`
- Audit: `redacted_attempt_and_result`

Arguments:

| Name | Required | Type/constraints | Meaning |
|---|---:|---|---|
| `vehicle_id` | no | string | Opaque Woodhouse vehicle ID. Omit only when exactly one eligible vehicle exists. |

Result: a sanitized structured result with `correlation_id`. Commands also return Tesla's success/reason outcome and, when an automatic wake was needed, `wake_correlation_id`.

### `tesla_charge_port_door_close`

Close a motorized charge-port door when no cable or vehicle state blocks it.

- Tesla operation: `POST /api/1/vehicles/{vin}/command/charge_port_door_close`
- Tesla scope: `vehicle_charging_cmds`
- Vehicle wake: `auto_if_needed`
- Risk: `normal`
- Retry: `never`
- Audit: `redacted_attempt_and_result`

Arguments:

| Name | Required | Type/constraints | Meaning |
|---|---:|---|---|
| `vehicle_id` | no | string | Opaque Woodhouse vehicle ID. Omit only when exactly one eligible vehicle exists. |

Result: a sanitized structured result with `correlation_id`. Commands also return Tesla's success/reason outcome and, when an automatic wake was needed, `wake_correlation_id`.

### `tesla_charge_port_door_open`

Open the charge-port door while the vehicle is parked and eligible.

- Tesla operation: `POST /api/1/vehicles/{vin}/command/charge_port_door_open`
- Tesla scope: `vehicle_charging_cmds`
- Vehicle wake: `auto_if_needed`
- Risk: `normal`
- Retry: `never`
- Audit: `redacted_attempt_and_result`

Arguments:

| Name | Required | Type/constraints | Meaning |
|---|---:|---|---|
| `vehicle_id` | no | string | Opaque Woodhouse vehicle ID. Omit only when exactly one eligible vehicle exists. |

Result: a sanitized structured result with `correlation_id`. Commands also return Tesla's success/reason outcome and, when an automatic wake was needed, `wake_correlation_id`.

### `tesla_charge_standard`

Return charging to the vehicle's standard/default charge-limit mode.

- Tesla operation: `POST /api/1/vehicles/{vin}/command/charge_standard`
- Tesla scope: `vehicle_charging_cmds`
- Vehicle wake: `auto_if_needed`
- Risk: `normal`
- Retry: `never`
- Audit: `redacted_attempt_and_result`

Arguments:

| Name | Required | Type/constraints | Meaning |
|---|---:|---|---|
| `vehicle_id` | no | string | Opaque Woodhouse vehicle ID. Omit only when exactly one eligible vehicle exists. |

Result: a sanitized structured result with `correlation_id`. Commands also return Tesla's success/reason outcome and, when an automatic wake was needed, `wake_correlation_id`.

### `tesla_charge_start`

Start charging when a powered cable is connected and charging is eligible.

- Tesla operation: `POST /api/1/vehicles/{vin}/command/charge_start`
- Tesla scope: `vehicle_charging_cmds`
- Vehicle wake: `auto_if_needed`
- Risk: `normal`
- Retry: `never`
- Audit: `redacted_attempt_and_result`

Arguments:

| Name | Required | Type/constraints | Meaning |
|---|---:|---|---|
| `vehicle_id` | no | string | Opaque Woodhouse vehicle ID. Omit only when exactly one eligible vehicle exists. |

Result: a sanitized structured result with `correlation_id`. Commands also return Tesla's success/reason outcome and, when an automatic wake was needed, `wake_correlation_id`.

### `tesla_charge_stop`

Stop an active charging session.

- Tesla operation: `POST /api/1/vehicles/{vin}/command/charge_stop`
- Tesla scope: `vehicle_charging_cmds`
- Vehicle wake: `auto_if_needed`
- Risk: `normal`
- Retry: `never`
- Audit: `redacted_attempt_and_result`

Arguments:

| Name | Required | Type/constraints | Meaning |
|---|---:|---|---|
| `vehicle_id` | no | string | Opaque Woodhouse vehicle ID. Omit only when exactly one eligible vehicle exists. |

Result: a sanitized structured result with `correlation_id`. Commands also return Tesla's success/reason outcome and, when an automatic wake was needed, `wake_correlation_id`.

### `tesla_charging_history`

Read charging history for one owned vehicle.

- Tesla operation: `GET /api/1/dx/charging/history`
- Tesla scope: `vehicle_charging_cmds`
- Vehicle wake: `never`
- Risk: `read_only`
- Retry: `safe_read`
- Audit: `none`

Arguments:

| Name | Required | Type/constraints | Meaning |
|---|---:|---|---|
| `vehicle_id` | no | string | Opaque Woodhouse vehicle ID. Omit only when exactly one eligible vehicle exists. |
| `start_time` | no | string; date-time | RFC 3339 timestamp for history reads, or minutes after local midnight for schedules. |
| `end_time` | no | string; date-time | RFC 3339 timestamp for history reads, or minutes after local midnight for schedules. |
| `page` | no | integer; >= 1 | Tesla request field. |
| `page_size` | no | integer; >= 1 and <= 100 | Requested page size, at most 100. |
| `sort_by` | no | string | Tesla request field. |
| `sort_order` | no | string; one of `asc`, `desc` | Tesla request field. |

Result: a sanitized structured result with `correlation_id`. The Tesla response remains live Fleet API data, not BigQuery history.

### `tesla_charging_invoice`

Read one charging invoice owned by the authenticated Tesla account.

- Tesla operation: `GET /api/1/dx/charging/invoice/{invoice_id}`
- Tesla scope: `vehicle_charging_cmds`
- Vehicle wake: `never`
- Risk: `read_only`
- Retry: `safe_read`
- Audit: `none`

Arguments:

| Name | Required | Type/constraints | Meaning |
|---|---:|---|---|
| `invoice_id` | yes | string | Tesla request field. |

Result: a sanitized structured result with `correlation_id`. The Tesla response remains live Fleet API data, not BigQuery history.

### `tesla_door_lock`

Lock the vehicle.

- Tesla operation: `POST /api/1/vehicles/{vin}/command/door_lock`
- Tesla scope: `vehicle_cmds`
- Vehicle wake: `auto_if_needed`
- Risk: `normal`
- Retry: `never`
- Audit: `redacted_attempt_and_result`

Arguments:

| Name | Required | Type/constraints | Meaning |
|---|---:|---|---|
| `vehicle_id` | no | string | Opaque Woodhouse vehicle ID. Omit only when exactly one eligible vehicle exists. |

Result: a sanitized structured result with `correlation_id`. Commands also return Tesla's success/reason outcome and, when an automatic wake was needed, `wake_correlation_id`.

### `tesla_door_unlock`

Unlock the vehicle; this is a security-sensitive physical-access operation.

- Tesla operation: `POST /api/1/vehicles/{vin}/command/door_unlock`
- Tesla scope: `vehicle_cmds`
- Vehicle wake: `auto_if_needed`
- Risk: `security_sensitive`
- Retry: `never`
- Audit: `redacted_attempt_and_result`

Arguments:

| Name | Required | Type/constraints | Meaning |
|---|---:|---|---|
| `vehicle_id` | no | string | Opaque Woodhouse vehicle ID. Omit only when exactly one eligible vehicle exists. |
| `explicit_current_turn_intent` | yes | boolean; must be `true` | Set true only when the user unambiguously requested this exact security-sensitive operation in the current turn. |

Result: a sanitized structured result with `correlation_id`. Commands also return Tesla's success/reason outcome and, when an automatic wake was needed, `wake_correlation_id`.

### `tesla_drivers`

List drivers authorized for the selected vehicle.

- Tesla operation: `GET /api/1/vehicles/{vin}/drivers`
- Tesla scope: `vehicle_device_data`
- Vehicle wake: `never`
- Risk: `read_only`
- Retry: `safe_read`
- Audit: `none`

Arguments:

| Name | Required | Type/constraints | Meaning |
|---|---:|---|---|
| `vehicle_id` | no | string | Opaque Woodhouse vehicle ID. Omit only when exactly one eligible vehicle exists. |

Result: a sanitized structured result with `correlation_id`. The Tesla response remains live Fleet API data, not BigQuery history.

### `tesla_feature_config`

Read the authenticated Tesla user's feature config.

- Tesla operation: `GET /api/1/users/feature_config`
- Tesla scope: `user_data`
- Vehicle wake: `never`
- Risk: `read_only`
- Retry: `safe_read`
- Audit: `none`

Arguments:

| Name | Required | Type/constraints | Meaning |
|---|---:|---|---|
| _none_ | — | — | The authenticated account is derived from OAuth. |

Result: a sanitized structured result with `correlation_id`. The Tesla response remains live Fleet API data, not BigQuery history.

### `tesla_flash_lights`

Briefly flash the exterior lights while the vehicle is parked.

- Tesla operation: `POST /api/1/vehicles/{vin}/command/flash_lights`
- Tesla scope: `vehicle_cmds`
- Vehicle wake: `auto_if_needed`
- Risk: `normal`
- Retry: `never`
- Audit: `redacted_attempt_and_result`

Arguments:

| Name | Required | Type/constraints | Meaning |
|---|---:|---|---|
| `vehicle_id` | no | string | Opaque Woodhouse vehicle ID. Omit only when exactly one eligible vehicle exists. |

Result: a sanitized structured result with `correlation_id`. Commands also return Tesla's success/reason outcome and, when an automatic wake was needed, `wake_correlation_id`.

### `tesla_fleet_status`

Read command-key and protocol status.

- Tesla operation: `POST /api/1/vehicles/fleet_status`
- Tesla scope: `vehicle_device_data`
- Vehicle wake: `never`
- Risk: `read_only`
- Retry: `safe_read`
- Audit: `none`

Arguments:

| Name | Required | Type/constraints | Meaning |
|---|---:|---|---|
| `vehicle_id` | no | string | Opaque Woodhouse vehicle ID. Omit only when exactly one eligible vehicle exists. |

Result: a sanitized structured result with `correlation_id`. The Tesla response remains live Fleet API data, not BigQuery history.

### `tesla_fleet_telemetry_config_get`

Read the selected vehicle telemetry configuration.

- Tesla operation: `GET /api/1/vehicles/{vin}/fleet_telemetry_config`
- Tesla scope: `vehicle_device_data`
- Vehicle wake: `never`
- Risk: `read_only`
- Retry: `safe_read`
- Audit: `none`

Arguments:

| Name | Required | Type/constraints | Meaning |
|---|---:|---|---|
| `vehicle_id` | no | string | Opaque Woodhouse vehicle ID. Omit only when exactly one eligible vehicle exists. |

Result: a sanitized structured result with `correlation_id`. The Tesla response remains live Fleet API data, not BigQuery history.

### `tesla_fleet_telemetry_errors`

Read telemetry delivery errors for the selected vehicle.

- Tesla operation: `GET /api/1/vehicles/{vin}/fleet_telemetry_errors`
- Tesla scope: `vehicle_device_data`
- Vehicle wake: `never`
- Risk: `read_only`
- Retry: `safe_read`
- Audit: `none`

Arguments:

| Name | Required | Type/constraints | Meaning |
|---|---:|---|---|
| `vehicle_id` | no | string | Opaque Woodhouse vehicle ID. Omit only when exactly one eligible vehicle exists. |

Result: a sanitized structured result with `correlation_id`. The Tesla response remains live Fleet API data, not BigQuery history.

### `tesla_guest_mode`

Enable or disable Tesla Guest Mode and its restricted-access behavior.

- Tesla operation: `POST /api/1/vehicles/{vin}/command/guest_mode`
- Tesla scope: `vehicle_cmds`
- Vehicle wake: `auto_if_needed`
- Risk: `security_sensitive`
- Retry: `never`
- Audit: `redacted_attempt_and_result`

Arguments:

| Name | Required | Type/constraints | Meaning |
|---|---:|---|---|
| `vehicle_id` | no | string | Opaque Woodhouse vehicle ID. Omit only when exactly one eligible vehicle exists. |
| `enable` | yes | boolean | Tesla request field. |
| `explicit_current_turn_intent` | yes | boolean; must be `true` | Set true only when the user unambiguously requested this exact security-sensitive operation in the current turn. |

Result: a sanitized structured result with `correlation_id`. Commands also return Tesla's success/reason outcome and, when an automatic wake was needed, `wake_correlation_id`.

### `tesla_honk_horn`

Sound the horn while the vehicle is parked.

- Tesla operation: `POST /api/1/vehicles/{vin}/command/honk_horn`
- Tesla scope: `vehicle_cmds`
- Vehicle wake: `auto_if_needed`
- Risk: `normal`
- Retry: `never`
- Audit: `redacted_attempt_and_result`

Arguments:

| Name | Required | Type/constraints | Meaning |
|---|---:|---|---|
| `vehicle_id` | no | string | Opaque Woodhouse vehicle ID. Omit only when exactly one eligible vehicle exists. |

Result: a sanitized structured result with `correlation_id`. Commands also return Tesla's success/reason outcome and, when an automatic wake was needed, `wake_correlation_id`.

### `tesla_list_vehicles`

List current Tesla vehicles intersected with the authenticated user's registry.

- Tesla operation: `GET /api/1/vehicles`
- Tesla scope: `vehicle_device_data`
- Vehicle wake: `never`
- Risk: `read_only`
- Retry: `safe_read`
- Audit: `none`

Arguments:

| Name | Required | Type/constraints | Meaning |
|---|---:|---|---|
| _none_ | — | — | The authenticated account is derived from OAuth. |

Result: a sanitized structured result with `correlation_id`. The Tesla response remains live Fleet API data, not BigQuery history.

### `tesla_me`

Read the authenticated Tesla account summary.

- Tesla operation: `GET /api/1/users/me`
- Tesla scope: `user_data`
- Vehicle wake: `never`
- Risk: `read_only`
- Retry: `safe_read`
- Audit: `none`

Arguments:

| Name | Required | Type/constraints | Meaning |
|---|---:|---|---|
| _none_ | — | — | The authenticated account is derived from OAuth. |

Result: a sanitized structured result with `correlation_id`. The Tesla response remains live Fleet API data, not BigQuery history.

### `tesla_media_next_fav`

Move to the next favorite in the active media source.

- Tesla operation: `POST /api/1/vehicles/{vin}/command/media_next_fav`
- Tesla scope: `vehicle_cmds`
- Vehicle wake: `auto_if_needed`
- Risk: `normal`
- Retry: `never`
- Audit: `redacted_attempt_and_result`

Arguments:

| Name | Required | Type/constraints | Meaning |
|---|---:|---|---|
| `vehicle_id` | no | string | Opaque Woodhouse vehicle ID. Omit only when exactly one eligible vehicle exists. |

Result: a sanitized structured result with `correlation_id`. Commands also return Tesla's success/reason outcome and, when an automatic wake was needed, `wake_correlation_id`.

### `tesla_media_next_track`

Move to the next media track.

- Tesla operation: `POST /api/1/vehicles/{vin}/command/media_next_track`
- Tesla scope: `vehicle_cmds`
- Vehicle wake: `auto_if_needed`
- Risk: `normal`
- Retry: `never`
- Audit: `redacted_attempt_and_result`

Arguments:

| Name | Required | Type/constraints | Meaning |
|---|---:|---|---|
| `vehicle_id` | no | string | Opaque Woodhouse vehicle ID. Omit only when exactly one eligible vehicle exists. |

Result: a sanitized structured result with `correlation_id`. Commands also return Tesla's success/reason outcome and, when an automatic wake was needed, `wake_correlation_id`.

### `tesla_media_prev_fav`

Move to the previous favorite in the active media source.

- Tesla operation: `POST /api/1/vehicles/{vin}/command/media_prev_fav`
- Tesla scope: `vehicle_cmds`
- Vehicle wake: `auto_if_needed`
- Risk: `normal`
- Retry: `never`
- Audit: `redacted_attempt_and_result`

Arguments:

| Name | Required | Type/constraints | Meaning |
|---|---:|---|---|
| `vehicle_id` | no | string | Opaque Woodhouse vehicle ID. Omit only when exactly one eligible vehicle exists. |

Result: a sanitized structured result with `correlation_id`. Commands also return Tesla's success/reason outcome and, when an automatic wake was needed, `wake_correlation_id`.

### `tesla_media_prev_track`

Move to the previous media track.

- Tesla operation: `POST /api/1/vehicles/{vin}/command/media_prev_track`
- Tesla scope: `vehicle_cmds`
- Vehicle wake: `auto_if_needed`
- Risk: `normal`
- Retry: `never`
- Audit: `redacted_attempt_and_result`

Arguments:

| Name | Required | Type/constraints | Meaning |
|---|---:|---|---|
| `vehicle_id` | no | string | Opaque Woodhouse vehicle ID. Omit only when exactly one eligible vehicle exists. |

Result: a sanitized structured result with `correlation_id`. Commands also return Tesla's success/reason outcome and, when an automatic wake was needed, `wake_correlation_id`.

### `tesla_media_toggle_playback`

Toggle the active media source between playing and paused.

- Tesla operation: `POST /api/1/vehicles/{vin}/command/media_toggle_playback`
- Tesla scope: `vehicle_cmds`
- Vehicle wake: `auto_if_needed`
- Risk: `normal`
- Retry: `never`
- Audit: `redacted_attempt_and_result`

Arguments:

| Name | Required | Type/constraints | Meaning |
|---|---:|---|---|
| `vehicle_id` | no | string | Opaque Woodhouse vehicle ID. Omit only when exactly one eligible vehicle exists. |

Result: a sanitized structured result with `correlation_id`. Commands also return Tesla's success/reason outcome and, when an automatic wake was needed, `wake_correlation_id`.

### `tesla_media_volume_down`

Lower media volume by one vehicle-defined step.

- Tesla operation: `POST /api/1/vehicles/{vin}/command/media_volume_down`
- Tesla scope: `vehicle_cmds`
- Vehicle wake: `auto_if_needed`
- Risk: `normal`
- Retry: `never`
- Audit: `redacted_attempt_and_result`

Arguments:

| Name | Required | Type/constraints | Meaning |
|---|---:|---|---|
| `vehicle_id` | no | string | Opaque Woodhouse vehicle ID. Omit only when exactly one eligible vehicle exists. |

Result: a sanitized structured result with `correlation_id`. Commands also return Tesla's success/reason outcome and, when an automatic wake was needed, `wake_correlation_id`.

### `tesla_media_volume_up`

Raise media volume by one vehicle-defined step.

- Tesla operation: `POST /api/1/vehicles/{vin}/command/media_volume_up`
- Tesla scope: `vehicle_cmds`
- Vehicle wake: `auto_if_needed`
- Risk: `normal`
- Retry: `never`
- Audit: `redacted_attempt_and_result`

Arguments:

| Name | Required | Type/constraints | Meaning |
|---|---:|---|---|
| `vehicle_id` | no | string | Opaque Woodhouse vehicle ID. Omit only when exactly one eligible vehicle exists. |

Result: a sanitized structured result with `correlation_id`. Commands also return Tesla's success/reason outcome and, when an automatic wake was needed, `wake_correlation_id`.

### `tesla_mobile_enabled`

Read current mobile-access capability without waking the vehicle.

- Tesla operation: `GET /api/1/vehicles/{vin}/mobile_enabled`
- Tesla scope: `vehicle_device_data`
- Vehicle wake: `requires_awake`
- Risk: `read_only`
- Retry: `safe_read`
- Audit: `none`

Arguments:

| Name | Required | Type/constraints | Meaning |
|---|---:|---|---|
| `vehicle_id` | no | string | Opaque Woodhouse vehicle ID. Omit only when exactly one eligible vehicle exists. |

Result: a sanitized structured result with `correlation_id`. The Tesla response remains live Fleet API data, not BigQuery history.

### `tesla_navigation_gps_request`

Start navigation to coordinates, optionally at a specified stop order.

- Tesla operation: `POST /api/1/vehicles/{vin}/command/navigation_gps_request`
- Tesla scope: `vehicle_cmds`
- Vehicle wake: `auto_if_needed`
- Risk: `normal`
- Retry: `never`
- Audit: `redacted_attempt_and_result`

Arguments:

| Name | Required | Type/constraints | Meaning |
|---|---:|---|---|
| `vehicle_id` | no | string | Opaque Woodhouse vehicle ID. Omit only when exactly one eligible vehicle exists. |
| `lat` | yes | number | Latitude; sensitive and excluded from command audit parameters. |
| `lon` | yes | number | Longitude; sensitive and excluded from command audit parameters. |
| `order` | no | integer | Optional navigation stop order. |

Result: a sanitized structured result with `correlation_id`. Commands also return Tesla's success/reason outcome and, when an automatic wake was needed, `wake_correlation_id`.

### `tesla_navigation_request`

Send Tesla's structured destination object to in-vehicle navigation.

- Tesla operation: `POST /api/1/vehicles/{vin}/command/navigation_request`
- Tesla scope: `vehicle_cmds`
- Vehicle wake: `auto_if_needed`
- Risk: `normal`
- Retry: `never`
- Audit: `redacted_attempt_and_result`

Arguments:

| Name | Required | Type/constraints | Meaning |
|---|---:|---|---|
| `vehicle_id` | no | string | Opaque Woodhouse vehicle ID. Omit only when exactly one eligible vehicle exists. |
| `type` | yes | string | Tesla request field. |
| `value` | yes | object | Tesla structured navigation destination object; treated as sensitive. |
| `locale` | yes | string | Tesla request field. |
| `timestamp_ms` | yes | string | Navigation request timestamp represented as milliseconds. |

Result: a sanitized structured result with `correlation_id`. Commands also return Tesla's success/reason outcome and, when an automatic wake was needed, `wake_correlation_id`.

### `tesla_navigation_sc_request`

Start navigation to a Tesla Supercharger identified by Tesla's numeric ID.

- Tesla operation: `POST /api/1/vehicles/{vin}/command/navigation_sc_request`
- Tesla scope: `vehicle_cmds`
- Vehicle wake: `auto_if_needed`
- Risk: `normal`
- Retry: `never`
- Audit: `redacted_attempt_and_result`

Arguments:

| Name | Required | Type/constraints | Meaning |
|---|---:|---|---|
| `vehicle_id` | no | string | Opaque Woodhouse vehicle ID. Omit only when exactly one eligible vehicle exists. |
| `id` | yes | integer | Tesla schedule or Supercharger identifier, depending on the tool. |
| `order` | no | integer | Optional navigation stop order. |

Result: a sanitized structured result with `correlation_id`. Commands also return Tesla's success/reason outcome and, when an automatic wake was needed, `wake_correlation_id`.

### `tesla_navigation_waypoints_request`

Send an encoded waypoint list to in-vehicle navigation.

- Tesla operation: `POST /api/1/vehicles/{vin}/command/navigation_waypoints_request`
- Tesla scope: `vehicle_cmds`
- Vehicle wake: `auto_if_needed`
- Risk: `normal`
- Retry: `never`
- Audit: `redacted_attempt_and_result`

Arguments:

| Name | Required | Type/constraints | Meaning |
|---|---:|---|---|
| `vehicle_id` | no | string | Opaque Woodhouse vehicle ID. Omit only when exactly one eligible vehicle exists. |
| `waypoints` | yes | string | Tesla-compatible encoded waypoint list; treated as sensitive. |

Result: a sanitized structured result with `correlation_id`. Commands also return Tesla's success/reason outcome and, when an automatic wake was needed, `wake_correlation_id`.

### `tesla_nearby_charging_sites`

Read nearby charging sites without implicitly waking the vehicle.

- Tesla operation: `GET /api/1/vehicles/{vin}/nearby_charging_sites`
- Tesla scope: `vehicle_device_data`
- Vehicle wake: `requires_awake`
- Risk: `read_only`
- Retry: `safe_read`
- Audit: `none`

Arguments:

| Name | Required | Type/constraints | Meaning |
|---|---:|---|---|
| `vehicle_id` | no | string | Opaque Woodhouse vehicle ID. Omit only when exactly one eligible vehicle exists. |
| `count` | no | integer; >= 1 | Tesla request field. |
| `radius` | no | number; >= 0 | Tesla request field. |
| `detail` | no | boolean | Tesla request field. |

Result: a sanitized structured result with `correlation_id`. The Tesla response remains live Fleet API data, not BigQuery history.

### `tesla_orders`

Read active orders for the authenticated Tesla account.

- Tesla operation: `GET /api/1/users/orders`
- Tesla scope: `user_data`
- Vehicle wake: `never`
- Risk: `read_only`
- Retry: `safe_read`
- Audit: `none`

Arguments:

| Name | Required | Type/constraints | Meaning |
|---|---:|---|---|
| _none_ | — | — | The authenticated account is derived from OAuth. |

Result: a sanitized structured result with `correlation_id`. The Tesla response remains live Fleet API data, not BigQuery history.

### `tesla_parental_controls_activate`

Activate configured Parental Controls with the existing four-digit PIN.

- Tesla operation: `POST /api/1/vehicles/{vin}/command/parental_controls_activate`
- Tesla scope: `vehicle_cmds`
- Vehicle wake: `auto_if_needed`
- Risk: `security_sensitive`
- Retry: `never`
- Audit: `redacted_attempt_and_result`

Arguments:

| Name | Required | Type/constraints | Meaning |
|---|---:|---|---|
| `vehicle_id` | no | string | Opaque Woodhouse vehicle ID. Omit only when exactly one eligible vehicle exists. |
| `pin` | yes | string | Exactly four digits; never logged or stored in command audit. |
| `explicit_current_turn_intent` | yes | boolean; must be `true` | Set true only when the user unambiguously requested this exact security-sensitive operation in the current turn. |

Result: a sanitized structured result with `correlation_id`. Commands also return Tesla's success/reason outcome and, when an automatic wake was needed, `wake_correlation_id`.

### `tesla_parental_controls_deactivate`

Deactivate Parental Controls using the current four-digit PIN.

- Tesla operation: `POST /api/1/vehicles/{vin}/command/parental_controls_deactivate`
- Tesla scope: `vehicle_cmds`
- Vehicle wake: `auto_if_needed`
- Risk: `security_sensitive`
- Retry: `never`
- Audit: `redacted_attempt_and_result`

Arguments:

| Name | Required | Type/constraints | Meaning |
|---|---:|---|---|
| `vehicle_id` | no | string | Opaque Woodhouse vehicle ID. Omit only when exactly one eligible vehicle exists. |
| `pin` | yes | string | Exactly four digits; never logged or stored in command audit. |
| `explicit_current_turn_intent` | yes | boolean; must be `true` | Set true only when the user unambiguously requested this exact security-sensitive operation in the current turn. |

Result: a sanitized structured result with `correlation_id`. Commands also return Tesla's success/reason outcome and, when an automatic wake was needed, `wake_correlation_id`.

### `tesla_parental_controls_enable_setting`

Enable or disable one parental setting before Parental Controls is activated.

- Tesla operation: `POST /api/1/vehicles/{vin}/command/parental_controls_enable_setting`
- Tesla scope: `vehicle_cmds`
- Vehicle wake: `auto_if_needed`
- Risk: `security_sensitive`
- Retry: `never`
- Audit: `redacted_attempt_and_result`

Arguments:

| Name | Required | Type/constraints | Meaning |
|---|---:|---|---|
| `vehicle_id` | no | string | Opaque Woodhouse vehicle ID. Omit only when exactly one eligible vehicle exists. |
| `setting` | yes | string | Tesla request field. |
| `enable` | yes | boolean | Tesla request field. |
| `explicit_current_turn_intent` | yes | boolean; must be `true` | Set true only when the user unambiguously requested this exact security-sensitive operation in the current turn. |

Result: a sanitized structured result with `correlation_id`. Commands also return Tesla's success/reason outcome and, when an automatic wake was needed, `wake_correlation_id`.

### `tesla_parental_controls_set_speed_limit`

Set the Parental Controls maximum speed in miles per hour before activation.

- Tesla operation: `POST /api/1/vehicles/{vin}/command/parental_controls_set_speed_limit`
- Tesla scope: `vehicle_cmds`
- Vehicle wake: `auto_if_needed`
- Risk: `security_sensitive`
- Retry: `never`
- Audit: `redacted_attempt_and_result`

Arguments:

| Name | Required | Type/constraints | Meaning |
|---|---:|---|---|
| `vehicle_id` | no | string | Opaque Woodhouse vehicle ID. Omit only when exactly one eligible vehicle exists. |
| `limit_mph` | yes | integer | Maximum speed in miles per hour. |
| `explicit_current_turn_intent` | yes | boolean; must be `true` | Set true only when the user unambiguously requested this exact security-sensitive operation in the current turn. |

Result: a sanitized structured result with `correlation_id`. Commands also return Tesla's success/reason outcome and, when an automatic wake was needed, `wake_correlation_id`.

### `tesla_recent_alerts`

Read recent vehicle alerts without implicitly waking the vehicle.

- Tesla operation: `GET /api/1/vehicles/{vin}/recent_alerts`
- Tesla scope: `vehicle_device_data`
- Vehicle wake: `requires_awake`
- Risk: `read_only`
- Retry: `safe_read`
- Audit: `none`

Arguments:

| Name | Required | Type/constraints | Meaning |
|---|---:|---|---|
| `vehicle_id` | no | string | Opaque Woodhouse vehicle ID. Omit only when exactly one eligible vehicle exists. |

Result: a sanitized structured result with `correlation_id`. The Tesla response remains live Fleet API data, not BigQuery history.

### `tesla_release_notes`

Read firmware release notes without implicitly waking the vehicle.

- Tesla operation: `GET /api/1/vehicles/{vin}/release_notes`
- Tesla scope: `vehicle_device_data`
- Vehicle wake: `requires_awake`
- Risk: `read_only`
- Retry: `safe_read`
- Audit: `none`

Arguments:

| Name | Required | Type/constraints | Meaning |
|---|---:|---|---|
| `vehicle_id` | no | string | Opaque Woodhouse vehicle ID. Omit only when exactly one eligible vehicle exists. |
| `staged` | no | boolean | Tesla request field. |
| `language` | no | string | Tesla request field. |

Result: a sanitized structured result with `correlation_id`. The Tesla response remains live Fleet API data, not BigQuery history.

### `tesla_remote_auto_seat_climate_request`

Configure automatic heating/cooling for a selected seat while climate is running.

- Tesla operation: `POST /api/1/vehicles/{vin}/command/remote_auto_seat_climate_request`
- Tesla scope: `vehicle_cmds`
- Vehicle wake: `auto_if_needed`
- Risk: `normal`
- Retry: `never`
- Audit: `redacted_attempt_and_result`

Arguments:

| Name | Required | Type/constraints | Meaning |
|---|---:|---|---|
| `vehicle_id` | no | string | Opaque Woodhouse vehicle ID. Omit only when exactly one eligible vehicle exists. |
| `auto_seat_position` | yes | integer | Tesla seat-position integer. |
| `auto_climate_on` | yes | boolean | Whether automatic seat climate should be active. |

Result: a sanitized structured result with `correlation_id`. Commands also return Tesla's success/reason outcome and, when an automatic wake was needed, `wake_correlation_id`.

### `tesla_remote_auto_steering_wheel_heat_climate_request`

Enable or disable automatic steering-wheel heating while climate is running.

- Tesla operation: `POST /api/1/vehicles/{vin}/command/remote_auto_steering_wheel_heat_climate_request`
- Tesla scope: `vehicle_cmds`
- Vehicle wake: `auto_if_needed`
- Risk: `normal`
- Retry: `never`
- Audit: `redacted_attempt_and_result`

Arguments:

| Name | Required | Type/constraints | Meaning |
|---|---:|---|---|
| `vehicle_id` | no | string | Opaque Woodhouse vehicle ID. Omit only when exactly one eligible vehicle exists. |
| `on` | yes | boolean | Tesla request field. |

Result: a sanitized structured result with `correlation_id`. Commands also return Tesla's success/reason outcome and, when an automatic wake was needed, `wake_correlation_id`.

### `tesla_remote_boombox`

Play a supported external-speaker sound: `0` random fart or `2000` locate ping.

- Tesla operation: `POST /api/1/vehicles/{vin}/command/remote_boombox`
- Tesla scope: `vehicle_cmds`
- Vehicle wake: `auto_if_needed`
- Risk: `normal`
- Retry: `never`
- Audit: `redacted_attempt_and_result`

Arguments:

| Name | Required | Type/constraints | Meaning |
|---|---:|---|---|
| `vehicle_id` | no | string | Opaque Woodhouse vehicle ID. Omit only when exactly one eligible vehicle exists. |
| `sound` | yes | integer | External-speaker sound ID: 0 random fart; 2000 locate ping. |

Result: a sanitized structured result with `correlation_id`. Commands also return Tesla's success/reason outcome and, when an automatic wake was needed, `wake_correlation_id`.

### `tesla_remote_seat_cooler_request`

Set cooling level for a selected seat while climate is running.

- Tesla operation: `POST /api/1/vehicles/{vin}/command/remote_seat_cooler_request`
- Tesla scope: `vehicle_cmds`
- Vehicle wake: `auto_if_needed`
- Risk: `normal`
- Retry: `never`
- Audit: `redacted_attempt_and_result`

Arguments:

| Name | Required | Type/constraints | Meaning |
|---|---:|---|---|
| `vehicle_id` | no | string | Opaque Woodhouse vehicle ID. Omit only when exactly one eligible vehicle exists. |
| `seat_position` | yes | integer | Tesla seat-position integer. |
| `seat_cooler_level` | yes | integer | Tesla seat-cooling level integer. |

Result: a sanitized structured result with `correlation_id`. Commands also return Tesla's success/reason outcome and, when an automatic wake was needed, `wake_correlation_id`.

### `tesla_remote_seat_heater_request`

Set heating level for a selected seat while climate is running.

- Tesla operation: `POST /api/1/vehicles/{vin}/command/remote_seat_heater_request`
- Tesla scope: `vehicle_cmds`
- Vehicle wake: `auto_if_needed`
- Risk: `normal`
- Retry: `never`
- Audit: `redacted_attempt_and_result`

Arguments:

| Name | Required | Type/constraints | Meaning |
|---|---:|---|---|
| `vehicle_id` | no | string | Opaque Woodhouse vehicle ID. Omit only when exactly one eligible vehicle exists. |
| `seat_position` | yes | integer | Tesla seat-position integer. |
| `level` | yes | integer | Tesla request field. |

Result: a sanitized structured result with `correlation_id`. Commands also return Tesla's success/reason outcome and, when an automatic wake was needed, `wake_correlation_id`.

### `tesla_remote_start_drive`

Enable keyless remote driving; this is not climate start and is security-sensitive.

- Tesla operation: `POST /api/1/vehicles/{vin}/command/remote_start_drive`
- Tesla scope: `vehicle_cmds`
- Vehicle wake: `auto_if_needed`
- Risk: `security_sensitive`
- Retry: `never`
- Audit: `redacted_attempt_and_result`

Arguments:

| Name | Required | Type/constraints | Meaning |
|---|---:|---|---|
| `vehicle_id` | no | string | Opaque Woodhouse vehicle ID. Omit only when exactly one eligible vehicle exists. |
| `explicit_current_turn_intent` | yes | boolean; must be `true` | Set true only when the user unambiguously requested this exact security-sensitive operation in the current turn. |

Result: a sanitized structured result with `correlation_id`. Commands also return Tesla's success/reason outcome and, when an automatic wake was needed, `wake_correlation_id`.

### `tesla_remote_steering_wheel_heat_level_request`

Set the steering-wheel heat level while climate is running.

- Tesla operation: `POST /api/1/vehicles/{vin}/command/remote_steering_wheel_heat_level_request`
- Tesla scope: `vehicle_cmds`
- Vehicle wake: `auto_if_needed`
- Risk: `normal`
- Retry: `never`
- Audit: `redacted_attempt_and_result`

Arguments:

| Name | Required | Type/constraints | Meaning |
|---|---:|---|---|
| `vehicle_id` | no | string | Opaque Woodhouse vehicle ID. Omit only when exactly one eligible vehicle exists. |
| `level` | yes | integer | Tesla request field. |

Result: a sanitized structured result with `correlation_id`. Commands also return Tesla's success/reason outcome and, when an automatic wake was needed, `wake_correlation_id`.

### `tesla_remote_steering_wheel_heater_request`

Enable or disable non-automatic steering-wheel heat while climate is running.

- Tesla operation: `POST /api/1/vehicles/{vin}/command/remote_steering_wheel_heater_request`
- Tesla scope: `vehicle_cmds`
- Vehicle wake: `auto_if_needed`
- Risk: `normal`
- Retry: `never`
- Audit: `redacted_attempt_and_result`

Arguments:

| Name | Required | Type/constraints | Meaning |
|---|---:|---|---|
| `vehicle_id` | no | string | Opaque Woodhouse vehicle ID. Omit only when exactly one eligible vehicle exists. |
| `on` | yes | boolean | Tesla request field. |

Result: a sanitized structured result with `correlation_id`. Commands also return Tesla's success/reason outcome and, when an automatic wake was needed, `wake_correlation_id`.

### `tesla_remove_charge_schedule`

Remove a charging schedule by its Tesla schedule ID.

- Tesla operation: `POST /api/1/vehicles/{vin}/command/remove_charge_schedule`
- Tesla scope: `vehicle_charging_cmds`
- Vehicle wake: `auto_if_needed`
- Risk: `normal`
- Retry: `never`
- Audit: `redacted_attempt_and_result`

Arguments:

| Name | Required | Type/constraints | Meaning |
|---|---:|---|---|
| `vehicle_id` | no | string | Opaque Woodhouse vehicle ID. Omit only when exactly one eligible vehicle exists. |
| `id` | yes | integer | Tesla schedule or Supercharger identifier, depending on the tool. |

Result: a sanitized structured result with `correlation_id`. Commands also return Tesla's success/reason outcome and, when an automatic wake was needed, `wake_correlation_id`.

### `tesla_remove_precondition_schedule`

Remove a preconditioning schedule by its Tesla schedule ID.

- Tesla operation: `POST /api/1/vehicles/{vin}/command/remove_precondition_schedule`
- Tesla scope: `vehicle_cmds`
- Vehicle wake: `auto_if_needed`
- Risk: `normal`
- Retry: `never`
- Audit: `redacted_attempt_and_result`

Arguments:

| Name | Required | Type/constraints | Meaning |
|---|---:|---|---|
| `vehicle_id` | no | string | Opaque Woodhouse vehicle ID. Omit only when exactly one eligible vehicle exists. |
| `id` | yes | integer | Tesla schedule or Supercharger identifier, depending on the tool. |

Result: a sanitized structured result with `correlation_id`. Commands also return Tesla's success/reason outcome and, when an automatic wake was needed, `wake_correlation_id`.

### `tesla_schedule_software_update`

Schedule the available vehicle software update after the requested delay.

- Tesla operation: `POST /api/1/vehicles/{vin}/command/schedule_software_update`
- Tesla scope: `vehicle_cmds`
- Vehicle wake: `auto_if_needed`
- Risk: `security_sensitive`
- Retry: `never`
- Audit: `redacted_attempt_and_result`

Arguments:

| Name | Required | Type/constraints | Meaning |
|---|---:|---|---|
| `vehicle_id` | no | string | Opaque Woodhouse vehicle ID. Omit only when exactly one eligible vehicle exists. |
| `offset_sec` | yes | integer | Delay before update installation, in seconds. |
| `explicit_current_turn_intent` | yes | boolean; must be `true` | Set true only when the user unambiguously requested this exact security-sensitive operation in the current turn. |

Result: a sanitized structured result with `correlation_id`. Commands also return Tesla's success/reason outcome and, when an automatic wake was needed, `wake_correlation_id`.

### `tesla_service_data`

Read current service information without implicitly waking the vehicle.

- Tesla operation: `GET /api/1/vehicles/{vin}/service_data`
- Tesla scope: `vehicle_device_data`
- Vehicle wake: `requires_awake`
- Risk: `read_only`
- Retry: `safe_read`
- Audit: `none`

Arguments:

| Name | Required | Type/constraints | Meaning |
|---|---:|---|---|
| `vehicle_id` | no | string | Opaque Woodhouse vehicle ID. Omit only when exactly one eligible vehicle exists. |

Result: a sanitized structured result with `correlation_id`. The Tesla response remains live Fleet API data, not BigQuery history.

### `tesla_set_bioweapon_mode`

Enable or disable Bioweapon Defense Mode, with an explicit manual-override flag.

- Tesla operation: `POST /api/1/vehicles/{vin}/command/set_bioweapon_mode`
- Tesla scope: `vehicle_cmds`
- Vehicle wake: `auto_if_needed`
- Risk: `normal`
- Retry: `never`
- Audit: `redacted_attempt_and_result`

Arguments:

| Name | Required | Type/constraints | Meaning |
|---|---:|---|---|
| `vehicle_id` | no | string | Opaque Woodhouse vehicle ID. Omit only when exactly one eligible vehicle exists. |
| `on` | yes | boolean | Tesla request field. |
| `manual_override` | yes | boolean | Tesla request field. |

Result: a sanitized structured result with `correlation_id`. Commands also return Tesla's success/reason outcome and, when an automatic wake was needed, `wake_correlation_id`.

### `tesla_set_cabin_overheat_protection`

Enable or disable Cabin Overheat Protection and choose fan-only behavior.

- Tesla operation: `POST /api/1/vehicles/{vin}/command/set_cabin_overheat_protection`
- Tesla scope: `vehicle_cmds`
- Vehicle wake: `auto_if_needed`
- Risk: `normal`
- Retry: `never`
- Audit: `redacted_attempt_and_result`

Arguments:

| Name | Required | Type/constraints | Meaning |
|---|---:|---|---|
| `vehicle_id` | no | string | Opaque Woodhouse vehicle ID. Omit only when exactly one eligible vehicle exists. |
| `on` | yes | boolean | Tesla request field. |
| `fan_only` | yes | boolean | Tesla request field. |

Result: a sanitized structured result with `correlation_id`. Commands also return Tesla's success/reason outcome and, when an automatic wake was needed, `wake_correlation_id`.

### `tesla_set_charge_limit`

Set the vehicle's requested charge-limit percentage.

- Tesla operation: `POST /api/1/vehicles/{vin}/command/set_charge_limit`
- Tesla scope: `vehicle_charging_cmds`
- Vehicle wake: `auto_if_needed`
- Risk: `normal`
- Retry: `never`
- Audit: `redacted_attempt_and_result`

Arguments:

| Name | Required | Type/constraints | Meaning |
|---|---:|---|---|
| `vehicle_id` | no | string | Opaque Woodhouse vehicle ID. Omit only when exactly one eligible vehicle exists. |
| `percent` | yes | integer | Requested whole-number charge-limit percentage. |

Result: a sanitized structured result with `correlation_id`. Commands also return Tesla's success/reason outcome and, when an automatic wake was needed, `wake_correlation_id`.

### `tesla_set_charging_amps`

Set the requested charging-current limit in amperes.

- Tesla operation: `POST /api/1/vehicles/{vin}/command/set_charging_amps`
- Tesla scope: `vehicle_charging_cmds`
- Vehicle wake: `auto_if_needed`
- Risk: `normal`
- Retry: `never`
- Audit: `redacted_attempt_and_result`

Arguments:

| Name | Required | Type/constraints | Meaning |
|---|---:|---|---|
| `vehicle_id` | no | string | Opaque Woodhouse vehicle ID. Omit only when exactly one eligible vehicle exists. |
| `charging_amps` | yes | integer | Requested current in amperes; the vehicle may clamp or reject unsupported values. |

Result: a sanitized structured result with `correlation_id`. Commands also return Tesla's success/reason outcome and, when an automatic wake was needed, `wake_correlation_id`.

### `tesla_set_climate_keeper_mode`

Set climate keeper mode: 0 off, 1 keep, 2 dog, or 3 camp.

- Tesla operation: `POST /api/1/vehicles/{vin}/command/set_climate_keeper_mode`
- Tesla scope: `vehicle_cmds`
- Vehicle wake: `auto_if_needed`
- Risk: `normal`
- Retry: `never`
- Audit: `redacted_attempt_and_result`

Arguments:

| Name | Required | Type/constraints | Meaning |
|---|---:|---|---|
| `vehicle_id` | no | string | Opaque Woodhouse vehicle ID. Omit only when exactly one eligible vehicle exists. |
| `climate_keeper_mode` | yes | integer; one of `0`, `1`, `2`, `3` | Tesla request field. |

Result: a sanitized structured result with `correlation_id`. Commands also return Tesla's success/reason outcome and, when an automatic wake was needed, `wake_correlation_id`.

### `tesla_set_cop_temp`

Set Cabin Overheat Protection threshold: 0 low, 1 medium, or 2 high.

- Tesla operation: `POST /api/1/vehicles/{vin}/command/set_cop_temp`
- Tesla scope: `vehicle_cmds`
- Vehicle wake: `auto_if_needed`
- Risk: `normal`
- Retry: `never`
- Audit: `redacted_attempt_and_result`

Arguments:

| Name | Required | Type/constraints | Meaning |
|---|---:|---|---|
| `vehicle_id` | no | string | Opaque Woodhouse vehicle ID. Omit only when exactly one eligible vehicle exists. |
| `cop_temp` | yes | integer; one of `0`, `1`, `2` | Tesla request field. |

Result: a sanitized structured result with `correlation_id`. Commands also return Tesla's success/reason outcome and, when an automatic wake was needed, `wake_correlation_id`.

### `tesla_set_pin_to_drive`

Enable or disable PIN to Drive using a four-digit password; security-sensitive.

- Tesla operation: `POST /api/1/vehicles/{vin}/command/set_pin_to_drive`
- Tesla scope: `vehicle_cmds`
- Vehicle wake: `auto_if_needed`
- Risk: `security_sensitive`
- Retry: `never`
- Audit: `redacted_attempt_and_result`

Arguments:

| Name | Required | Type/constraints | Meaning |
|---|---:|---|---|
| `vehicle_id` | no | string | Opaque Woodhouse vehicle ID. Omit only when exactly one eligible vehicle exists. |
| `on` | yes | boolean | Tesla request field. |
| `password` | yes | string | Exactly four digits; never logged or stored in command audit. |
| `explicit_current_turn_intent` | yes | boolean; must be `true` | Set true only when the user unambiguously requested this exact security-sensitive operation in the current turn. |

Result: a sanitized structured result with `correlation_id`. Commands also return Tesla's success/reason outcome and, when an automatic wake was needed, `wake_correlation_id`.

### `tesla_set_preconditioning_max`

Enable or disable maximum preconditioning with an explicit manual override.

- Tesla operation: `POST /api/1/vehicles/{vin}/command/set_preconditioning_max`
- Tesla scope: `vehicle_cmds`
- Vehicle wake: `auto_if_needed`
- Risk: `normal`
- Retry: `never`
- Audit: `redacted_attempt_and_result`

Arguments:

| Name | Required | Type/constraints | Meaning |
|---|---:|---|---|
| `vehicle_id` | no | string | Opaque Woodhouse vehicle ID. Omit only when exactly one eligible vehicle exists. |
| `on` | yes | boolean | Tesla request field. |
| `manual_override` | yes | boolean | Tesla request field. |

Result: a sanitized structured result with `correlation_id`. Commands also return Tesla's success/reason outcome and, when an automatic wake was needed, `wake_correlation_id`.

### `tesla_set_sentry_mode`

Enable or disable Sentry Mode.

- Tesla operation: `POST /api/1/vehicles/{vin}/command/set_sentry_mode`
- Tesla scope: `vehicle_cmds`
- Vehicle wake: `auto_if_needed`
- Risk: `normal`
- Retry: `never`
- Audit: `redacted_attempt_and_result`

Arguments:

| Name | Required | Type/constraints | Meaning |
|---|---:|---|---|
| `vehicle_id` | no | string | Opaque Woodhouse vehicle ID. Omit only when exactly one eligible vehicle exists. |
| `on` | yes | boolean | Tesla request field. |

Result: a sanitized structured result with `correlation_id`. Commands also return Tesla's success/reason outcome and, when an automatic wake was needed, `wake_correlation_id`.

### `tesla_set_temps`

Set driver and passenger cabin-temperature targets.

- Tesla operation: `POST /api/1/vehicles/{vin}/command/set_temps`
- Tesla scope: `vehicle_cmds`
- Vehicle wake: `auto_if_needed`
- Risk: `normal`
- Retry: `never`
- Audit: `redacted_attempt_and_result`

Arguments:

| Name | Required | Type/constraints | Meaning |
|---|---:|---|---|
| `vehicle_id` | no | string | Opaque Woodhouse vehicle ID. Omit only when exactly one eligible vehicle exists. |
| `driver_temp` | yes | number | Driver-zone target temperature in Tesla's API units. |
| `passenger_temp` | yes | number | Passenger-zone target temperature in Tesla's API units. |

Result: a sanitized structured result with `correlation_id`. Commands also return Tesla's success/reason outcome and, when an automatic wake was needed, `wake_correlation_id`.

### `tesla_set_valet_mode`

Enable or disable Valet Mode using its four-digit password.

- Tesla operation: `POST /api/1/vehicles/{vin}/command/set_valet_mode`
- Tesla scope: `vehicle_cmds`
- Vehicle wake: `auto_if_needed`
- Risk: `security_sensitive`
- Retry: `never`
- Audit: `redacted_attempt_and_result`

Arguments:

| Name | Required | Type/constraints | Meaning |
|---|---:|---|---|
| `vehicle_id` | no | string | Opaque Woodhouse vehicle ID. Omit only when exactly one eligible vehicle exists. |
| `on` | yes | boolean | Tesla request field. |
| `password` | yes | string | Exactly four digits; never logged or stored in command audit. |
| `explicit_current_turn_intent` | yes | boolean; must be `true` | Set true only when the user unambiguously requested this exact security-sensitive operation in the current turn. |

Result: a sanitized structured result with `correlation_id`. Commands also return Tesla's success/reason outcome and, when an automatic wake was needed, `wake_correlation_id`.

### `tesla_set_vehicle_name`

Change the vehicle name when Guest Mode does not block it.

- Tesla operation: `POST /api/1/vehicles/{vin}/command/set_vehicle_name`
- Tesla scope: `vehicle_cmds`
- Vehicle wake: `auto_if_needed`
- Risk: `normal`
- Retry: `never`
- Audit: `redacted_attempt_and_result`

Arguments:

| Name | Required | Type/constraints | Meaning |
|---|---:|---|---|
| `vehicle_id` | no | string | Opaque Woodhouse vehicle ID. Omit only when exactly one eligible vehicle exists. |
| `vehicle_name` | yes | string | Tesla request field. |

Result: a sanitized structured result with `correlation_id`. Commands also return Tesla's success/reason outcome and, when an automatic wake was needed, `wake_correlation_id`.

### `tesla_speed_limit_activate`

Activate Speed Limit Mode with its four-digit PIN.

- Tesla operation: `POST /api/1/vehicles/{vin}/command/speed_limit_activate`
- Tesla scope: `vehicle_cmds`
- Vehicle wake: `auto_if_needed`
- Risk: `security_sensitive`
- Retry: `never`
- Audit: `redacted_attempt_and_result`

Arguments:

| Name | Required | Type/constraints | Meaning |
|---|---:|---|---|
| `vehicle_id` | no | string | Opaque Woodhouse vehicle ID. Omit only when exactly one eligible vehicle exists. |
| `pin` | yes | string | Exactly four digits; never logged or stored in command audit. |
| `explicit_current_turn_intent` | yes | boolean; must be `true` | Set true only when the user unambiguously requested this exact security-sensitive operation in the current turn. |

Result: a sanitized structured result with `correlation_id`. Commands also return Tesla's success/reason outcome and, when an automatic wake was needed, `wake_correlation_id`.

### `tesla_speed_limit_clear_pin`

Deactivate Speed Limit Mode and clear its PIN using the current PIN.

- Tesla operation: `POST /api/1/vehicles/{vin}/command/speed_limit_clear_pin`
- Tesla scope: `vehicle_cmds`
- Vehicle wake: `auto_if_needed`
- Risk: `security_sensitive`
- Retry: `never`
- Audit: `redacted_attempt_and_result`

Arguments:

| Name | Required | Type/constraints | Meaning |
|---|---:|---|---|
| `vehicle_id` | no | string | Opaque Woodhouse vehicle ID. Omit only when exactly one eligible vehicle exists. |
| `pin` | yes | string | Exactly four digits; never logged or stored in command audit. |
| `explicit_current_turn_intent` | yes | boolean; must be `true` | Set true only when the user unambiguously requested this exact security-sensitive operation in the current turn. |

Result: a sanitized structured result with `correlation_id`. Commands also return Tesla's success/reason outcome and, when an automatic wake was needed, `wake_correlation_id`.

### `tesla_speed_limit_deactivate`

Deactivate Speed Limit Mode using the current PIN.

- Tesla operation: `POST /api/1/vehicles/{vin}/command/speed_limit_deactivate`
- Tesla scope: `vehicle_cmds`
- Vehicle wake: `auto_if_needed`
- Risk: `security_sensitive`
- Retry: `never`
- Audit: `redacted_attempt_and_result`

Arguments:

| Name | Required | Type/constraints | Meaning |
|---|---:|---|---|
| `vehicle_id` | no | string | Opaque Woodhouse vehicle ID. Omit only when exactly one eligible vehicle exists. |
| `pin` | yes | string | Exactly four digits; never logged or stored in command audit. |
| `explicit_current_turn_intent` | yes | boolean; must be `true` | Set true only when the user unambiguously requested this exact security-sensitive operation in the current turn. |

Result: a sanitized structured result with `correlation_id`. Commands also return Tesla's success/reason outcome and, when an automatic wake was needed, `wake_correlation_id`.

### `tesla_speed_limit_set_limit`

Set the Speed Limit Mode maximum in miles per hour.

- Tesla operation: `POST /api/1/vehicles/{vin}/command/speed_limit_set_limit`
- Tesla scope: `vehicle_cmds`
- Vehicle wake: `auto_if_needed`
- Risk: `security_sensitive`
- Retry: `never`
- Audit: `redacted_attempt_and_result`

Arguments:

| Name | Required | Type/constraints | Meaning |
|---|---:|---|---|
| `vehicle_id` | no | string | Opaque Woodhouse vehicle ID. Omit only when exactly one eligible vehicle exists. |
| `limit_mph` | yes | integer | Maximum speed in miles per hour. |
| `explicit_current_turn_intent` | yes | boolean; must be `true` | Set true only when the user unambiguously requested this exact security-sensitive operation in the current turn. |

Result: a sanitized structured result with `correlation_id`. Commands also return Tesla's success/reason outcome and, when an automatic wake was needed, `wake_correlation_id`.

### `tesla_sun_roof_control`

Stop, close, or vent a supported sunroof.

- Tesla operation: `POST /api/1/vehicles/{vin}/command/sun_roof_control`
- Tesla scope: `vehicle_cmds`
- Vehicle wake: `auto_if_needed`
- Risk: `security_sensitive`
- Retry: `never`
- Audit: `redacted_attempt_and_result`

Arguments:

| Name | Required | Type/constraints | Meaning |
|---|---:|---|---|
| `vehicle_id` | no | string | Opaque Woodhouse vehicle ID. Omit only when exactly one eligible vehicle exists. |
| `state` | yes | string; one of `stop`, `close`, `vent` | Tesla request field. |
| `explicit_current_turn_intent` | yes | boolean; must be `true` | Set true only when the user unambiguously requested this exact security-sensitive operation in the current turn. |

Result: a sanitized structured result with `correlation_id`. Commands also return Tesla's success/reason outcome and, when an automatic wake was needed, `wake_correlation_id`.

### `tesla_trigger_homelink`

Trigger a paired HomeLink device near the supplied user coordinates.

- Tesla operation: `POST /api/1/vehicles/{vin}/command/trigger_homelink`
- Tesla scope: `vehicle_cmds`
- Vehicle wake: `auto_if_needed`
- Risk: `security_sensitive`
- Retry: `never`
- Audit: `redacted_attempt_and_result`

Arguments:

| Name | Required | Type/constraints | Meaning |
|---|---:|---|---|
| `vehicle_id` | no | string | Opaque Woodhouse vehicle ID. Omit only when exactly one eligible vehicle exists. |
| `lat` | yes | number | Latitude; sensitive and excluded from command audit parameters. |
| `lon` | yes | number | Longitude; sensitive and excluded from command audit parameters. |
| `token` | yes | string | Tesla HomeLink token; sensitive and never written to command audit. |
| `explicit_current_turn_intent` | yes | boolean; must be `true` | Set true only when the user unambiguously requested this exact security-sensitive operation in the current turn. |

Result: a sanitized structured result with `correlation_id`. Commands also return Tesla's success/reason outcome and, when an automatic wake was needed, `wake_correlation_id`.

### `tesla_upcoming_calendar_entries`

Send serialized upcoming calendar entries to the vehicle.

- Tesla operation: `POST /api/1/vehicles/{vin}/command/upcoming_calendar_entries`
- Tesla scope: `vehicle_cmds`
- Vehicle wake: `auto_if_needed`
- Risk: `normal`
- Retry: `never`
- Audit: `redacted_attempt_and_result`

Arguments:

| Name | Required | Type/constraints | Meaning |
|---|---:|---|---|
| `vehicle_id` | no | string | Opaque Woodhouse vehicle ID. Omit only when exactly one eligible vehicle exists. |
| `calendar_data` | yes | string | Tesla-compatible serialized calendar payload; treated as sensitive and never audited. |

Result: a sanitized structured result with `correlation_id`. Commands also return Tesla's success/reason outcome and, when an automatic wake was needed, `wake_correlation_id`.

### `tesla_vehicle`

Read current vehicle metadata.

- Tesla operation: `GET /api/1/vehicles/{vin}`
- Tesla scope: `vehicle_device_data`
- Vehicle wake: `never`
- Risk: `read_only`
- Retry: `safe_read`
- Audit: `none`

Arguments:

| Name | Required | Type/constraints | Meaning |
|---|---:|---|---|
| `vehicle_id` | no | string | Opaque Woodhouse vehicle ID. Omit only when exactly one eligible vehicle exists. |

Result: a sanitized structured result with `correlation_id`. The Tesla response remains live Fleet API data, not BigQuery history.

### `tesla_vehicle_data`

Read explicitly selected current-data sections without implicitly waking the vehicle.

- Tesla operation: `GET /api/1/vehicles/{vin}/vehicle_data`
- Tesla scope: `vehicle_device_data`
- Vehicle wake: `requires_awake`
- Risk: `read_only`
- Retry: `safe_read`
- Audit: `none`

Arguments:

| Name | Required | Type/constraints | Meaning |
|---|---:|---|---|
| `vehicle_id` | no | string | Opaque Woodhouse vehicle ID. Omit only when exactly one eligible vehicle exists. |
| `endpoints` | yes | array; items: string; at least 1 item(s); unique items | One or more explicit `vehicle_data` sections; duplicates are rejected. |

Result: a sanitized structured result with `correlation_id`. The Tesla response remains live Fleet API data, not BigQuery history.

### `tesla_wake_up`

Explicitly wake one owned vehicle.

- Tesla operation: `POST /api/1/vehicles/{vin}/wake_up`
- Tesla scope: `vehicle_device_data`
- Vehicle wake: `explicit`
- Risk: `normal`
- Retry: `never`
- Audit: `redacted_attempt_and_result`

Arguments:

| Name | Required | Type/constraints | Meaning |
|---|---:|---|---|
| `vehicle_id` | no | string | Opaque Woodhouse vehicle ID. Omit only when exactly one eligible vehicle exists. |

Result: a sanitized structured result with `correlation_id`. The explicit wake returns the vehicle identity, display name, and live state.

### `tesla_window_control`

Vent or close windows on a parked vehicle; closing may require nearby user coordinates.

- Tesla operation: `POST /api/1/vehicles/{vin}/command/window_control`
- Tesla scope: `vehicle_cmds`
- Vehicle wake: `auto_if_needed`
- Risk: `security_sensitive`
- Retry: `never`
- Audit: `redacted_attempt_and_result`

Arguments:

| Name | Required | Type/constraints | Meaning |
|---|---:|---|---|
| `vehicle_id` | no | string | Opaque Woodhouse vehicle ID. Omit only when exactly one eligible vehicle exists. |
| `command` | yes | string; one of `vent`, `close` | Window action. |
| `lat` | yes | number | Latitude; sensitive and excluded from command audit parameters. |
| `lon` | yes | number | Longitude; sensitive and excluded from command audit parameters. |
| `explicit_current_turn_intent` | yes | boolean; must be `true` | Set true only when the user unambiguously requested this exact security-sensitive operation in the current turn. |

Result: a sanitized structured result with `correlation_id`. Commands also return Tesla's success/reason outcome and, when an automatic wake was needed, `wake_correlation_id`.

### `get_analytics_schema`

Describe the authenticated user's private historical analytics catalog, including tables/views, fields, join keys, partition hints, limits, and useful SQL examples.

- Data source: authenticated user's server-derived BigQuery default dataset
- Platform scope: `mcp:access`
- Vehicle wake: `never`
- Risk: `read_only`
- Retry: no automatic retry after execution begins
- Audit/logging: query job metadata only; SQL and result rows are excluded

Arguments:

| Name | Required | Type/constraints | Meaning |
|---|---:|---|---|
| _none_ | — | — | Identity and dataset are derived from OAuth. |

Result: user-safe object/field descriptions, join keys, partition hints, examples, and active query limits; the physical dataset ID is not returned.

### `run_analytics_query`

Dry-run and execute one bounded, read-only Standard SQL SELECT/WITH query in the authenticated user's server-derived BigQuery dataset. Qualified names, scripting, DML/DDL, external queries, and remote/user-defined functions are rejected. Failures identify validation, dry-run, or execution phase and return sanitized BigQuery reason/message/location diagnostics when available.

- Data source: authenticated user's server-derived BigQuery default dataset
- Platform scope: `mcp:access`
- Vehicle wake: `never`
- Risk: `read_only`
- Retry: no automatic retry after execution begins
- Audit/logging: query job metadata only; SQL and result rows are excluded

Arguments:

| Name | Required | Type/constraints | Meaning |
|---|---:|---|---|
| `sql` | yes | string | One read-only BigQuery Standard SQL SELECT/WITH query using only unqualified names returned by get_analytics_schema. |

Result: bounded columns and rows plus truncation, job ID, duration, referenced in-scope objects, and processed/billed bytes. SQL is AST-validated, canonicalized, dry-run first, capped at 1 GiB billed, 30 seconds, 1,000 returned rows, and 1 MiB of serialized result data. BigQuery can aggregate or correlate more source rows; the row and result-size limits bound only the response returned through MCP. Errors retain error/message/correlation_id and add the validation, dry-run, or execution phase. BigQuery reason and line/column diagnostics are included when available after private infrastructure identifiers are sanitized; failed jobs may also include safe job/byte metadata.
