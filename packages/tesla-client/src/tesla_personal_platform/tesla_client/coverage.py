"""Machine-readable typed-client coverage matching the Markdown contract."""

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True, slots=True)
class EndpointImplementation:
    client: Literal["fleet", "partner"]
    method: str
    compatibility_only: bool = False
    wake_behavior: Literal["never", "requires_awake", "explicit"] = "never"
    retry_policy: Literal["safe_read", "never"] = "safe_read"


def _fleet(
    method: str,
    *,
    compatibility_only: bool = False,
    wake_behavior: Literal["never", "requires_awake", "explicit"] = "never",
    retry_policy: Literal["safe_read", "never"] = "safe_read",
) -> EndpointImplementation:
    return EndpointImplementation("fleet", method, compatibility_only, wake_behavior, retry_policy)


def _partner(method: str) -> EndpointImplementation:
    return EndpointImplementation("partner", method)


COMMAND_NAMES = (
    "actuate_trunk",
    "add_charge_schedule",
    "add_precondition_schedule",
    "adjust_volume",
    "auto_conditioning_start",
    "auto_conditioning_stop",
    "cancel_software_update",
    "charge_max_range",
    "charge_port_door_close",
    "charge_port_door_open",
    "charge_standard",
    "charge_start",
    "charge_stop",
    "clear_pin_to_drive_admin",
    "door_lock",
    "door_unlock",
    "erase_user_data",
    "flash_lights",
    "guest_mode",
    "honk_horn",
    "media_next_fav",
    "media_next_track",
    "media_prev_fav",
    "media_prev_track",
    "media_toggle_playback",
    "media_volume_down",
    "media_volume_up",
    "navigation_gps_request",
    "navigation_request",
    "navigation_sc_request",
    "navigation_waypoints_request",
    "parental_controls_activate",
    "parental_controls_clear_pin_admin",
    "parental_controls_deactivate",
    "parental_controls_enable_setting",
    "parental_controls_set_speed_limit",
    "remote_auto_seat_climate_request",
    "remote_auto_steering_wheel_heat_climate_request",
    "remote_boombox",
    "remote_seat_cooler_request",
    "remote_seat_heater_request",
    "remote_start_drive",
    "remote_steering_wheel_heat_level_request",
    "remote_steering_wheel_heater_request",
    "remove_charge_schedule",
    "remove_precondition_schedule",
    "reset_pin_to_drive_pin",
    "reset_valet_pin",
    "schedule_software_update",
    "set_bioweapon_mode",
    "set_cabin_overheat_protection",
    "set_charge_limit",
    "set_charging_amps",
    "set_climate_keeper_mode",
    "set_cop_temp",
    "set_pin_to_drive",
    "set_preconditioning_max",
    "set_scheduled_charging",
    "set_scheduled_departure",
    "set_sentry_mode",
    "set_temps",
    "set_valet_mode",
    "set_vehicle_name",
    "speed_limit_activate",
    "speed_limit_clear_pin",
    "speed_limit_clear_pin_admin",
    "speed_limit_deactivate",
    "speed_limit_set_limit",
    "sun_roof_control",
    "trigger_homelink",
    "upcoming_calendar_entries",
    "window_control",
)

IMPLEMENTED_ENDPOINTS: dict[tuple[str, str], EndpointImplementation] = {
    ("GET", "/api/1/vehicles/{vin}/drivers"): _fleet("drivers"),
    ("DELETE", "/api/1/vehicles/{vin}/drivers"): _fleet("drivers_remove", retry_policy="never"),
    ("POST", "/api/1/vehicles/fleet_status"): _fleet("fleet_status"),
    ("POST", "/api/1/vehicles/fleet_telemetry_config"): _fleet(
        "fleet_telemetry_config_create", retry_policy="never"
    ),
    ("DELETE", "/api/1/vehicles/{vin}/fleet_telemetry_config"): _fleet(
        "fleet_telemetry_config_delete", retry_policy="never"
    ),
    ("GET", "/api/1/vehicles/{vin}/fleet_telemetry_config"): _fleet("fleet_telemetry_config_get"),
    ("POST", "/api/1/vehicles/fleet_telemetry_config_jws"): _fleet(
        "fleet_telemetry_config_jws", compatibility_only=True, retry_policy="never"
    ),
    ("GET", "/api/1/vehicles/{vin}/fleet_telemetry_errors"): _fleet("fleet_telemetry_errors"),
    ("GET", "/api/1/vehicles"): _fleet("list_vehicles"),
    ("GET", "/api/1/vehicles/{vin}/mobile_enabled"): _fleet(
        "mobile_enabled", wake_behavior="requires_awake"
    ),
    ("GET", "/api/1/vehicles/{vin}/nearby_charging_sites"): _fleet(
        "nearby_charging_sites", wake_behavior="requires_awake"
    ),
    ("GET", "/api/1/vehicles/{vin}/recent_alerts"): _fleet(
        "recent_alerts", wake_behavior="requires_awake"
    ),
    ("GET", "/api/1/vehicles/{vin}/release_notes"): _fleet(
        "release_notes", wake_behavior="requires_awake"
    ),
    ("GET", "/api/1/vehicles/{vin}/service_data"): _fleet(
        "service_data", wake_behavior="requires_awake"
    ),
    ("GET", "/api/1/vehicles/{vin}/invitations"): _fleet("share_invites"),
    ("POST", "/api/1/vehicles/{vin}/invitations"): _fleet(
        "share_invites_create", retry_policy="never"
    ),
    ("POST", "/api/1/invitations/redeem"): _fleet("share_invites_redeem", retry_policy="never"),
    ("POST", "/api/1/vehicles/{vin}/invitations/{id}/revoke"): _fleet(
        "share_invites_revoke", retry_policy="never"
    ),
    ("POST", "/api/1/vehicles/{vin}/signed_command"): _fleet(
        "signed_command", retry_policy="never"
    ),
    ("GET", "/api/1/vehicles/{vin}"): _fleet("vehicle"),
    ("GET", "/api/1/vehicles/{vin}/vehicle_data"): _fleet(
        "vehicle_data", wake_behavior="requires_awake"
    ),
    ("POST", "/api/1/vehicles/{vin}/wake_up"): _fleet(
        "wake_up", wake_behavior="explicit", retry_policy="never"
    ),
    ("GET", "/api/1/users/feature_config"): _fleet("feature_config"),
    ("GET", "/api/1/users/me"): _fleet("me"),
    ("GET", "/api/1/users/orders"): _fleet("orders"),
    ("GET", "/api/1/users/region"): _fleet("region"),
    ("GET", "/api/1/partner_accounts/fleet_telemetry_error_vins"): _partner(
        "fleet_telemetry_error_vins"
    ),
    ("GET", "/api/1/partner_accounts/fleet_telemetry_errors"): _partner("fleet_telemetry_errors"),
    ("GET", "/api/1/partner_accounts/public_key?domain={domain}"): _partner("public_key"),
    ("POST", "/api/1/partner_accounts"): EndpointImplementation(
        "partner", "register", retry_policy="never"
    ),
    ("GET", "/api/1/dx/charging/history"): _fleet("charging_history"),
    ("GET", "/api/1/dx/charging/invoice/{id}"): _fleet("charging_invoice"),
}

for _command in COMMAND_NAMES:
    IMPLEMENTED_ENDPOINTS[("POST", f"/api/1/vehicles/{{vin}}/command/{_command}")] = _fleet(
        _command,
        compatibility_only=_command in {"set_scheduled_charging", "set_scheduled_departure"},
        wake_behavior="requires_awake",
        retry_policy="never",
    )
