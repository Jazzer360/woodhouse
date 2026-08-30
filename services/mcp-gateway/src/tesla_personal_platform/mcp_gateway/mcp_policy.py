"""Private typed Tesla operation policy behind the semantic MCP tools."""

from __future__ import annotations

from dataclasses import dataclass, fields
from datetime import datetime
from typing import Any, Literal, Protocol

from pydantic import TypeAdapter, ValidationError
from tesla_personal_platform.analytics import AnalyticsContext
from tesla_personal_platform.tesla_client.coverage import COMMAND_NAMES
from tesla_personal_platform.tesla_client.models import JsonObject
from tesla_personal_platform.tesla_client.requests import (
    ActuateTrunkRequest,
    AdjustVolumeRequest,
    AutoSeatClimateRequest,
    BoolRequest,
    CabinOverheatRequest,
    CabinOverheatTemperatureRequest,
    CalendarRequest,
    ChargeLimitRequest,
    ChargeScheduleRequest,
    ChargingAmpsRequest,
    ClimateKeeperRequest,
    EnableRequest,
    HomeLinkRequest,
    LevelRequest,
    NavigationGPSRequest,
    NavigationRequest,
    NavigationSuperchargerRequest,
    NavigationWaypointRequest,
    OnWithOverrideRequest,
    ParentalSettingRequest,
    PinRequest,
    PreconditionScheduleRequest,
    ScheduleIDRequest,
    SeatCoolerRequest,
    SeatHeaterRequest,
    SetPinRequest,
    SoftwareUpdateRequest,
    SoundRequest,
    SpeedLimitRequest,
    SunRoofRequest,
    TemperatureRequest,
    VehicleNameRequest,
    WindowControlRequest,
)

Risk = Literal["read_only", "normal", "security_sensitive"]
WakeBehavior = Literal["never", "requires_awake", "explicit", "auto_if_needed"]
type Document = dict[str, Any]


class MCPToolError(Exception):
    """Safe failure returned to an MCP client."""

    def __init__(
        self,
        category: str,
        message: str,
        *,
        correlation_id: str | None = None,
    ) -> None:
        super().__init__(message)
        self.category = category
        self.correlation_id = correlation_id


class CommandAuditStore(Protocol):
    def begin_command_audit(
        self,
        *,
        audit_id: str,
        timestamp: datetime,
        owner_user_id: str,
        vehicle_id: str,
        tool_name: str,
        redacted_parameters: JsonObject,
        correlation_id: str,
        source: str,
    ) -> None: ...

    def complete_command_audit(
        self,
        *,
        audit_id: str,
        result: str,
        error_category: str | None,
    ) -> None: ...


class AnalyticsProvider(Protocol):
    """Historical analytics boundary, already scoped by authenticated context."""

    def get_schema(self, context: AnalyticsContext, *, correlation_id: str) -> Document: ...

    def run_query(
        self,
        context: AnalyticsContext,
        sql: str,
        *,
        correlation_id: str,
    ) -> Document: ...


@dataclass(frozen=True, slots=True)
class ToolSpec:
    matrix_name: str
    name: str
    client_method: str
    description: str
    required_scope: str
    vehicle_scoped: bool
    write: bool
    risk: Risk
    wake_behavior: WakeBehavior
    retry_policy: Literal["safe_read", "never"]
    request_type: type[Any] | None = None
    extra_schema: Document | None = None

    @property
    def audit_behavior(self) -> str:
        return "redacted_attempt_and_result" if self.write else "none"

    def validate_arguments(self, values: dict[str, object]) -> None:
        """Validate private operation arguments behind the semantic MCP boundary."""
        allowed: set[str] = set()
        if self.vehicle_scoped:
            allowed.add("vehicle_id")
            vehicle_id = values.get("vehicle_id")
            if vehicle_id is not None and not isinstance(vehicle_id, str):
                raise MCPToolError("invalid_arguments", "Invalid value for vehicle_id")
        if self.request_type is not None:
            request_fields = {field.name for field in fields(self.request_type)}
            allowed.update(request_fields)
            request_values = {key: value for key, value in values.items() if key in request_fields}
            try:
                TypeAdapter(self.request_type).validate_python(request_values)
            except ValidationError as error:
                raise MCPToolError(
                    "invalid_arguments", "Tool arguments do not match the typed request"
                ) from error
        if self.extra_schema:
            extra_properties = self.extra_schema.get("properties", {})
            if not isinstance(extra_properties, dict):
                raise AssertionError("Invalid internal operation policy")
            allowed.update(extra_properties)
            extra_required = self.extra_schema.get("required", [])
            if not isinstance(extra_required, list):
                raise AssertionError("Invalid internal operation policy")
            if set(map(str, extra_required)) - set(values):
                raise MCPToolError(
                    "invalid_arguments", "Tool arguments do not match the typed request"
                )
            for key, schema in extra_properties.items():
                if key in values and (
                    not isinstance(schema, dict) or not _matches_schema(values[key], schema)
                ):
                    raise MCPToolError("invalid_arguments", f"Invalid value for {key}")
        if self.risk == "security_sensitive":
            allowed.add("explicit_current_turn_intent")
            if values.get("explicit_current_turn_intent") is not True:
                raise MCPToolError("invalid_arguments", "Explicit current-turn intent is required")
        if set(values) - allowed:
            raise MCPToolError("invalid_arguments", "Tool arguments do not match the typed request")


_VEHICLE_READS = {
    "drivers": ("drivers", "List drivers authorized for the selected vehicle.", "never", None),
    "fleet_status": ("fleet_status", "Read command-key and protocol status.", "never", None),
    "fleet_telemetry_config get": (
        "fleet_telemetry_config_get",
        "Read the selected vehicle telemetry configuration.",
        "never",
        None,
    ),
    "fleet_telemetry_errors": (
        "fleet_telemetry_errors",
        "Read telemetry delivery errors for the selected vehicle.",
        "never",
        None,
    ),
    "mobile_enabled": (
        "mobile_enabled",
        "Read current mobile-access capability without waking the vehicle.",
        "requires_awake",
        None,
    ),
    "nearby_charging_sites": (
        "nearby_charging_sites",
        "Read nearby charging sites without implicitly waking the vehicle.",
        "requires_awake",
        {
            "properties": {
                "count": {"type": "integer", "minimum": 1},
                "radius": {"type": "number", "minimum": 0},
                "detail": {"type": "boolean"},
            }
        },
    ),
    "recent_alerts": (
        "recent_alerts",
        "Read recent vehicle alerts without implicitly waking the vehicle.",
        "requires_awake",
        None,
    ),
    "release_notes": (
        "release_notes",
        "Read firmware release notes without implicitly waking the vehicle.",
        "requires_awake",
        {"properties": {"staged": {"type": "boolean"}, "language": {"type": "string"}}},
    ),
    "service_data": (
        "service_data",
        "Read current service information without implicitly waking the vehicle.",
        "requires_awake",
        None,
    ),
    "vehicle": ("vehicle", "Read current vehicle metadata.", "never", None),
    "vehicle_data": (
        "vehicle_data",
        "Read explicitly selected current-data sections without implicitly waking the vehicle.",
        "requires_awake",
        {
            "properties": {
                "endpoints": {
                    "type": "array",
                    "items": {"type": "string"},
                    "minItems": 1,
                    "uniqueItems": True,
                }
            },
            "required": ["endpoints"],
        },
    ),
}

_ACCOUNT_READS = {
    "feature_config": ("feature_config", "Read the authenticated Tesla user's feature config."),
    "me": ("me", "Read the authenticated Tesla account summary."),
    "orders": ("orders", "Read active orders for the authenticated Tesla account."),
}

_COMMAND_REQUEST_TYPES: dict[str, type[Any]] = {
    "actuate_trunk": ActuateTrunkRequest,
    "add_charge_schedule": ChargeScheduleRequest,
    "add_precondition_schedule": PreconditionScheduleRequest,
    "adjust_volume": AdjustVolumeRequest,
    "guest_mode": EnableRequest,
    "navigation_gps_request": NavigationGPSRequest,
    "navigation_request": NavigationRequest,
    "navigation_sc_request": NavigationSuperchargerRequest,
    "navigation_waypoints_request": NavigationWaypointRequest,
    "parental_controls_activate": PinRequest,
    "parental_controls_deactivate": PinRequest,
    "parental_controls_enable_setting": ParentalSettingRequest,
    "parental_controls_set_speed_limit": SpeedLimitRequest,
    "remote_auto_seat_climate_request": AutoSeatClimateRequest,
    "remote_auto_steering_wheel_heat_climate_request": BoolRequest,
    "remote_boombox": SoundRequest,
    "remote_seat_cooler_request": SeatCoolerRequest,
    "remote_seat_heater_request": SeatHeaterRequest,
    "remote_steering_wheel_heat_level_request": LevelRequest,
    "remote_steering_wheel_heater_request": BoolRequest,
    "remove_charge_schedule": ScheduleIDRequest,
    "remove_precondition_schedule": ScheduleIDRequest,
    "schedule_software_update": SoftwareUpdateRequest,
    "set_bioweapon_mode": OnWithOverrideRequest,
    "set_cabin_overheat_protection": CabinOverheatRequest,
    "set_charge_limit": ChargeLimitRequest,
    "set_charging_amps": ChargingAmpsRequest,
    "set_climate_keeper_mode": ClimateKeeperRequest,
    "set_cop_temp": CabinOverheatTemperatureRequest,
    "set_pin_to_drive": SetPinRequest,
    "set_preconditioning_max": OnWithOverrideRequest,
    "set_sentry_mode": BoolRequest,
    "set_temps": TemperatureRequest,
    "set_valet_mode": SetPinRequest,
    "set_vehicle_name": VehicleNameRequest,
    "speed_limit_activate": PinRequest,
    "speed_limit_clear_pin": PinRequest,
    "speed_limit_deactivate": PinRequest,
    "speed_limit_set_limit": SpeedLimitRequest,
    "sun_roof_control": SunRoofRequest,
    "trigger_homelink": HomeLinkRequest,
    "upcoming_calendar_entries": CalendarRequest,
    "window_control": WindowControlRequest,
}

_EXCLUDED_COMMANDS = frozenset(
    {
        "clear_pin_to_drive_admin",
        "erase_user_data",
        "parental_controls_clear_pin_admin",
        "reset_pin_to_drive_pin",
        "reset_valet_pin",
        "speed_limit_clear_pin_admin",
        "set_scheduled_charging",
        "set_scheduled_departure",
    }
)
_SECURITY_SENSITIVE = frozenset(
    {
        "actuate_trunk",
        "cancel_software_update",
        "door_unlock",
        "guest_mode",
        "parental_controls_activate",
        "parental_controls_deactivate",
        "parental_controls_enable_setting",
        "parental_controls_set_speed_limit",
        "remote_start_drive",
        "schedule_software_update",
        "set_pin_to_drive",
        "set_valet_mode",
        "speed_limit_activate",
        "speed_limit_clear_pin",
        "speed_limit_deactivate",
        "speed_limit_set_limit",
        "sun_roof_control",
        "trigger_homelink",
        "window_control",
    }
)
_CHARGING_COMMANDS = frozenset(
    name for name in COMMAND_NAMES if "charge" in name or "charging" in name
)


def _build_specs() -> tuple[ToolSpec, ...]:
    specs: list[ToolSpec] = []
    for matrix_name, (method, description, wake, schema) in _VEHICLE_READS.items():
        specs.append(
            ToolSpec(
                matrix_name,
                f"tesla_{method}",
                method,
                description,
                "vehicle_device_data",
                True,
                False,
                "read_only",
                wake,  # type: ignore[arg-type]
                "safe_read",
                extra_schema=schema,
            )
        )
    specs.extend(
        ToolSpec(
            matrix_name,
            f"tesla_{method}",
            method,
            description,
            "user_data",
            False,
            False,
            "read_only",
            "never",
            "safe_read",
        )
        for matrix_name, (method, description) in _ACCOUNT_READS.items()
    )
    specs.extend(
        (
            ToolSpec(
                "list",
                "tesla_list_vehicles",
                "list_vehicles",
                "List current Tesla vehicles intersected with the authenticated user's registry.",
                "vehicle_device_data",
                False,
                False,
                "read_only",
                "never",
                "safe_read",
            ),
            ToolSpec(
                "charging_history",
                "tesla_charging_history",
                "charging_history",
                "Read charging history for one owned vehicle.",
                "vehicle_charging_cmds",
                True,
                False,
                "read_only",
                "never",
                "safe_read",
                extra_schema={
                    "properties": {
                        "start_time": {"type": "string", "format": "date-time"},
                        "end_time": {"type": "string", "format": "date-time"},
                        "page": {"type": "integer", "minimum": 1},
                        "page_size": {"type": "integer", "minimum": 1, "maximum": 100},
                        "sort_by": {"type": "string"},
                        "sort_order": {"type": "string", "enum": ["asc", "desc"]},
                    }
                },
            ),
            ToolSpec(
                "charging_invoice",
                "tesla_charging_invoice",
                "charging_invoice",
                "Read one charging invoice owned by the authenticated Tesla account.",
                "vehicle_charging_cmds",
                False,
                False,
                "read_only",
                "never",
                "safe_read",
                extra_schema={
                    "properties": {"invoice_id": {"type": "string"}},
                    "required": ["invoice_id"],
                },
            ),
            ToolSpec(
                "wake_up",
                "tesla_wake_up",
                "wake_up",
                "Explicitly wake one owned vehicle.",
                "vehicle_device_data",
                True,
                True,
                "normal",
                "explicit",
                "never",
            ),
        )
    )
    for command in COMMAND_NAMES:
        if command in _EXCLUDED_COMMANDS:
            continue
        risk: Risk = "security_sensitive" if command in _SECURITY_SENSITIVE else "normal"
        scope = "vehicle_charging_cmds" if command in _CHARGING_COMMANDS else "vehicle_cmds"
        specs.append(
            ToolSpec(
                command,
                f"tesla_{command}",
                command,
                f"Execute the typed Tesla {command} command on one owned vehicle.",
                scope,
                True,
                True,
                risk,
                "auto_if_needed",
                "never",
                _COMMAND_REQUEST_TYPES.get(command),
            )
        )
    return tuple(sorted(specs, key=lambda item: item.name))


MCP_TOOL_SPECS = _build_specs()
MCP_TOOLS_BY_NAME = {spec.name: spec for spec in MCP_TOOL_SPECS}

ANALYTICS_OPERATIONS = frozenset({"get_analytics_schema", "run_analytics_query"})


def _matches_schema(value: object, schema: dict[str, object]) -> bool:
    expected = schema.get("type")
    valid = (
        (expected == "string" and isinstance(value, str))
        or (expected == "boolean" and isinstance(value, bool))
        or (expected == "integer" and isinstance(value, int) and not isinstance(value, bool))
        or (
            expected == "number" and isinstance(value, (int, float)) and not isinstance(value, bool)
        )
        or (expected == "object" and isinstance(value, dict))
        or (expected == "array" and isinstance(value, list))
    )
    if not valid or "const" in schema and value != schema["const"]:
        return False
    allowed = schema.get("enum")
    if isinstance(allowed, list) and value not in allowed:
        return False
    minimum = schema.get("minimum")
    maximum = schema.get("maximum")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if isinstance(minimum, (int, float)) and value < minimum:
            return False
        if isinstance(maximum, (int, float)) and value > maximum:
            return False
    if isinstance(value, list):
        minimum_items = schema.get("minItems")
        if isinstance(minimum_items, int) and len(value) < minimum_items:
            return False
        if schema.get("uniqueItems") is True and any(
            item in value[index + 1 :] for index, item in enumerate(value)
        ):
            return False
        item_schema = schema.get("items")
        if isinstance(item_schema, dict) and any(
            not _matches_schema(item, item_schema) for item in value
        ):
            return False
    return True
