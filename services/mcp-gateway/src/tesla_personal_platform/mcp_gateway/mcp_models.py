"""Pydantic inputs for Woodhouse's compact semantic MCP capability families."""

from __future__ import annotations

from typing import ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ToolInput(BaseModel):
    """Strict MCP boundary model with conversion to the internal operation shape."""

    model_config = ConfigDict(extra="forbid")
    action: str
    vehicle_id: str | None = Field(
        default=None,
        description="Owned internal vehicle ID; omit only when exactly one vehicle is eligible.",
    )
    explicit_current_turn_intent: bool = Field(
        default=False,
        description=(
            "True only when the user unambiguously requested this exact security-sensitive "
            "action in the current turn."
        ),
    )
    required_by_action: ClassVar[dict[str, frozenset[str]]] = {}

    @model_validator(mode="after")
    def validate_action_fields(self) -> ToolInput:
        missing = [
            name
            for name in self.required_by_action.get(self.action, ())
            if getattr(self, name) is None
        ]
        if missing:
            raise ValueError(f"{self.action} requires: {', '.join(sorted(missing))}")
        return self

    def legacy_arguments(self) -> dict[str, object]:
        excluded = {"action"}
        if not self.explicit_current_turn_intent:
            excluded.add("explicit_current_turn_intent")
        return self.model_dump(mode="json", exclude=excluded, exclude_none=True)


class AccountRead(ToolInput):
    action: Literal["feature_config", "me", "orders", "list_vehicles"]
    vehicle_id: None = None
    explicit_current_turn_intent: Literal[False] = False


class VehicleRead(ToolInput):
    action: Literal[
        "vehicle",
        "fleet_status",
        "fleet_telemetry_config_get",
        "fleet_telemetry_errors",
        "mobile_enabled",
        "nearby_charging_sites",
        "recent_alerts",
        "release_notes",
        "service_data",
        "vehicle_data",
    ]
    explicit_current_turn_intent: Literal[False] = False
    endpoints: tuple[str, ...] | None = None
    count: int | None = Field(default=None, ge=1)
    radius: float | None = Field(default=None, ge=0)
    detail: bool | None = None
    staged: bool | None = None
    language: str | None = None
    required_by_action = {"vehicle_data": frozenset({"endpoints"})}


class ChargingRecordRead(ToolInput):
    action: Literal["charging_history", "charging_invoice"]
    start_time: str | None = None
    end_time: str | None = None
    page: int | None = Field(default=None, ge=1)
    page_size: int | None = Field(default=None, ge=1, le=100)
    sort_by: str | None = None
    sort_order: Literal["asc", "desc"] | None = None
    invoice_id: str | None = None
    explicit_current_turn_intent: Literal[False] = False
    required_by_action = {"charging_invoice": frozenset({"invoice_id"})}


class VehicleAccessControl(ToolInput):
    action: Literal[
        "door_lock",
        "door_unlock",
        "actuate_trunk",
        "window_control",
        "trigger_homelink",
        "remote_start_drive",
        "guest_mode",
        "flash_lights",
        "honk_horn",
    ]
    which_trunk: Literal["front", "rear"] | None = None
    command: Literal["vent", "close"] | None = None
    lat: float | None = None
    lon: float | None = None
    token: str | None = None
    enable: bool | None = None
    required_by_action = {
        "actuate_trunk": frozenset({"which_trunk"}),
        "window_control": frozenset({"command", "lat", "lon"}),
        "trigger_homelink": frozenset({"lat", "lon", "token"}),
        "guest_mode": frozenset({"enable"}),
    }


class ClimateControl(ToolInput):
    action: Literal[
        "auto_conditioning_start",
        "auto_conditioning_stop",
        "remote_auto_seat_climate_request",
        "remote_auto_steering_wheel_heat_climate_request",
        "remote_seat_cooler_request",
        "remote_seat_heater_request",
        "remote_steering_wheel_heat_level_request",
        "remote_steering_wheel_heater_request",
        "set_bioweapon_mode",
        "set_cabin_overheat_protection",
        "set_climate_keeper_mode",
        "set_cop_temp",
        "set_preconditioning_max",
        "set_temps",
    ]
    explicit_current_turn_intent: Literal[False] = False
    auto_seat_position: int | None = None
    auto_climate_on: bool | None = None
    on: bool | None = None
    seat_position: int | None = None
    seat_cooler_level: int | None = None
    level: int | None = None
    manual_override: bool | None = None
    fan_only: bool | None = None
    climate_keeper_mode: Literal[0, 1, 2, 3] | None = None
    cop_temp: Literal[0, 1, 2] | None = None
    driver_temp: float | None = None
    passenger_temp: float | None = None
    required_by_action = {
        "remote_auto_seat_climate_request": frozenset({"auto_seat_position", "auto_climate_on"}),
        "remote_auto_steering_wheel_heat_climate_request": frozenset({"on"}),
        "remote_seat_cooler_request": frozenset({"seat_position", "seat_cooler_level"}),
        "remote_seat_heater_request": frozenset({"seat_position", "level"}),
        "remote_steering_wheel_heat_level_request": frozenset({"level"}),
        "remote_steering_wheel_heater_request": frozenset({"on"}),
        "set_bioweapon_mode": frozenset({"on", "manual_override"}),
        "set_cabin_overheat_protection": frozenset({"on", "fan_only"}),
        "set_climate_keeper_mode": frozenset({"climate_keeper_mode"}),
        "set_cop_temp": frozenset({"cop_temp"}),
        "set_preconditioning_max": frozenset({"on", "manual_override"}),
        "set_temps": frozenset({"driver_temp", "passenger_temp"}),
    }


class ChargingControl(ToolInput):
    action: Literal[
        "charge_max_range",
        "charge_port_door_close",
        "charge_port_door_open",
        "charge_standard",
        "charge_start",
        "charge_stop",
        "set_charge_limit",
        "set_charging_amps",
        "add_charge_schedule",
        "remove_charge_schedule",
        "add_precondition_schedule",
        "remove_precondition_schedule",
    ]
    explicit_current_turn_intent: Literal[False] = False
    percent: int | None = Field(default=None, ge=0, le=100)
    charging_amps: int | None = Field(default=None, ge=0)
    id: int | None = None
    lat: float | None = None
    lon: float | None = None
    days_of_week: str | None = None
    start_enabled: bool | None = None
    start_time: int | None = None
    end_enabled: bool | None = None
    end_time: int | None = None
    precondition_time: int | None = None
    one_time: bool | None = None
    enabled: bool | None = None
    required_by_action = {
        "set_charge_limit": frozenset({"percent"}),
        "set_charging_amps": frozenset({"charging_amps"}),
        "remove_charge_schedule": frozenset({"id"}),
        "remove_precondition_schedule": frozenset({"id"}),
        "add_charge_schedule": frozenset(
            {
                "lat",
                "lon",
                "id",
                "days_of_week",
                "start_enabled",
                "start_time",
                "end_enabled",
                "end_time",
                "one_time",
                "enabled",
            }
        ),
        "add_precondition_schedule": frozenset(
            {
                "lat",
                "lon",
                "id",
                "days_of_week",
                "precondition_time",
                "one_time",
                "enabled",
            }
        ),
    }


class MediaControl(ToolInput):
    action: Literal[
        "adjust_volume",
        "media_next_fav",
        "media_next_track",
        "media_prev_fav",
        "media_prev_track",
        "media_toggle_playback",
        "media_volume_down",
        "media_volume_up",
        "remote_boombox",
    ]
    explicit_current_turn_intent: Literal[False] = False
    volume: float | None = None
    sound: int | None = None
    required_by_action = {
        "adjust_volume": frozenset({"volume"}),
        "remote_boombox": frozenset({"sound"}),
    }


class NavigationControl(ToolInput):
    action: Literal[
        "navigation_gps_request",
        "navigation_request",
        "navigation_sc_request",
        "navigation_waypoints_request",
    ]
    explicit_current_turn_intent: Literal[False] = False
    lat: float | None = None
    lon: float | None = None
    order: int | None = None
    id: int | None = None
    type: str | None = None
    value: dict[str, object] | None = None
    locale: str | None = None
    timestamp_ms: str | None = None
    waypoints: str | None = None
    required_by_action = {
        "navigation_gps_request": frozenset({"lat", "lon"}),
        "navigation_sc_request": frozenset({"id"}),
        "navigation_request": frozenset({"type", "value", "locale", "timestamp_ms"}),
        "navigation_waypoints_request": frozenset({"waypoints"}),
    }


class SecurityControl(ToolInput):
    action: Literal[
        "parental_controls_activate",
        "parental_controls_deactivate",
        "parental_controls_enable_setting",
        "parental_controls_set_speed_limit",
        "set_pin_to_drive",
        "set_sentry_mode",
        "set_valet_mode",
        "speed_limit_activate",
        "speed_limit_clear_pin",
        "speed_limit_deactivate",
        "speed_limit_set_limit",
    ]
    pin: str | None = Field(default=None, pattern=r"^\d{4}$")
    password: str | None = Field(default=None, pattern=r"^\d{4}$")
    on: bool | None = None
    setting: str | None = None
    enable: bool | None = None
    limit_mph: int | None = Field(default=None, ge=0)
    required_by_action = {
        "parental_controls_activate": frozenset({"pin"}),
        "parental_controls_deactivate": frozenset({"pin"}),
        "parental_controls_enable_setting": frozenset({"setting", "enable"}),
        "parental_controls_set_speed_limit": frozenset({"limit_mph"}),
        "set_pin_to_drive": frozenset({"on", "password"}),
        "set_sentry_mode": frozenset({"on"}),
        "set_valet_mode": frozenset({"on", "password"}),
        "speed_limit_activate": frozenset({"pin"}),
        "speed_limit_clear_pin": frozenset({"pin"}),
        "speed_limit_deactivate": frozenset({"pin"}),
        "speed_limit_set_limit": frozenset({"limit_mph"}),
    }


class VehicleSettingsControl(ToolInput):
    action: Literal[
        "cancel_software_update",
        "schedule_software_update",
        "set_vehicle_name",
        "sun_roof_control",
        "upcoming_calendar_entries",
    ]
    offset_sec: int | None = Field(default=None, ge=0)
    vehicle_name: str | None = None
    state: Literal["stop", "close", "vent"] | None = None
    calendar_data: str | None = None
    required_by_action = {
        "schedule_software_update": frozenset({"offset_sec"}),
        "set_vehicle_name": frozenset({"vehicle_name"}),
        "sun_roof_control": frozenset({"state"}),
        "upcoming_calendar_entries": frozenset({"calendar_data"}),
    }


class WakeVehicle(BaseModel):
    model_config = ConfigDict(extra="forbid")
    vehicle_id: str | None = None


class AnalyticsQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sql: str = Field(min_length=1, max_length=32 * 1024)
