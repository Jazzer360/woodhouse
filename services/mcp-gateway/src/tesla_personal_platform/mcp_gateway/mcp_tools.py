"""Typed MCP surface for current Tesla state and intentional vehicle controls."""

from __future__ import annotations

import base64
import json
import secrets
import time
import types
from collections.abc import Callable
from dataclasses import asdict, dataclass, fields, is_dataclass
from datetime import UTC, datetime
from typing import (
    Any,
    Literal,
    Protocol,
    TypeAliasType,
    Union,
    get_args,
    get_origin,
    get_type_hints,
)

from tesla_personal_platform.analytics import AnalyticsContext, AnalyticsQueryError
from tesla_personal_platform.auth import CrossUserAccessError, UserContext
from tesla_personal_platform.mcp_gateway.mcp_auth import MCP_ACCESS_SCOPE
from tesla_personal_platform.mcp_gateway.tesla_onboarding import (
    TeslaOnboardingError,
    TeslaOnboardingStore,
    VehicleRecord,
)
from tesla_personal_platform.tesla_client import (
    BinaryDocument,
    CommandResult,
    PerUserTeslaClient,
    TeslaAPIError,
    TeslaFleetClient,
    TeslaVehicle,
    tesla_api_log_context,
)
from tesla_personal_platform.tesla_client.coverage import COMMAND_NAMES
from tesla_personal_platform.tesla_client.models import JsonObject
from tesla_personal_platform.tesla_client.redaction import REDACTED, redact_mapping
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
    ChargingHistoryQuery,
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
    VehicleDataQuery,
    VehicleNameRequest,
    WindowControlRequest,
)
from tesla_personal_platform.tesla_client.session import TeslaAccessProvider

Risk = Literal["read_only", "normal", "security_sensitive"]
WakeBehavior = Literal["never", "requires_awake", "explicit", "auto_if_needed"]
type Document = dict[str, Any]
_WAKE_POLL_ATTEMPTS = 6
_WAKE_POLL_INTERVAL_SECONDS = 10.0
_RESPONSE_SECRET_KEYS = frozenset(
    {
        "access_token",
        "authorization",
        "client_secret",
        "id_token",
        "private_key",
        "refresh_token",
    }
)
_AUDIT_WHOLE_VALUE_FIELDS = {
    "navigation_request": frozenset({"value"}),
    "navigation_waypoints_request": frozenset({"waypoints"}),
}
_MCP_ERROR_DETAIL_FIELDS = frozenset(
    {
        "reason",
        "phase",
        "location",
        "diagnostics",
        "job_id",
        "bytes_processed",
        "bytes_billed",
    }
)


class MCPToolError(Exception):
    """Safe failure returned to an MCP client."""

    def __init__(
        self,
        category: str,
        message: str,
        *,
        correlation_id: str | None = None,
        details: Document | None = None,
    ) -> None:
        super().__init__(message)
        self.category = category
        self.correlation_id = correlation_id
        self.details = {
            key: value for key, value in (details or {}).items() if key in _MCP_ERROR_DETAIL_FIELDS
        }


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

    def input_schema(self) -> Document:
        properties: Document = {}
        required: list[str] = []
        if self.vehicle_scoped:
            properties["vehicle_id"] = {
                "type": "string",
                "description": (
                    "Owned internal vehicle ID. Omit only when exactly one eligible vehicle exists."
                ),
            }
        if self.request_type is not None:
            request_schema = _dataclass_schema(self.request_type)
            properties.update(request_schema["properties"])
            required.extend(request_schema["required"])
        if self.extra_schema:
            extra_properties = self.extra_schema.get("properties", {})
            if isinstance(extra_properties, dict):
                properties.update(extra_properties)
            extra_required = self.extra_schema.get("required", [])
            if isinstance(extra_required, list):
                required.extend(str(value) for value in extra_required)
        if self.risk == "security_sensitive":
            properties["explicit_current_turn_intent"] = {
                "type": "boolean",
                "const": True,
                "description": (
                    "Set true only when the user unambiguously requested this exact "
                    "security-sensitive operation in the current turn."
                ),
            }
            required.append("explicit_current_turn_intent")
        return {
            "type": "object",
            "properties": properties,
            "required": sorted(set(required)),
            "additionalProperties": False,
        }

    def mcp_document(self, *, oauth_protected: bool = True) -> Document:
        annotations: Document = {
            "readOnlyHint": not self.write,
            "destructiveHint": self.risk == "security_sensitive",
            "idempotentHint": False if self.write else True,
            "openWorldHint": True,
        }
        document: Document = {
            "name": self.name,
            "description": (
                f"{self.description} Required Tesla scope: {self.required_scope}. "
                f"Wake: {self.wake_behavior}; risk: {self.risk}; retry: {self.retry_policy}; "
                f"audit: {self.audit_behavior}."
            ),
            "inputSchema": self.input_schema(),
            "annotations": annotations,
        }
        if oauth_protected:
            document["securitySchemes"] = [{"type": "oauth2", "scopes": [MCP_ACCESS_SCOPE]}]
        return document


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

ANALYTICS_TOOL_SCHEMAS: dict[str, Document] = {
    "get_analytics_schema": {
        "type": "object",
        "properties": {},
        "required": [],
        "additionalProperties": False,
    },
    "run_analytics_query": {
        "type": "object",
        "properties": {
            "sql": {
                "type": "string",
                "description": (
                    "One read-only BigQuery Standard SQL SELECT/WITH query using only "
                    "unqualified names returned by get_analytics_schema."
                ),
            }
        },
        "required": ["sql"],
        "additionalProperties": False,
    },
}


def analytics_tool_documents(*, oauth_protected: bool = True) -> list[Document]:
    """Return the two general historical tools; no one-off history endpoints."""
    descriptions = {
        "get_analytics_schema": (
            "Describe the authenticated user's private historical analytics catalog, including "
            "tables/views, fields, join keys, partition hints, limits, and useful SQL examples."
        ),
        "run_analytics_query": (
            "Dry-run and execute one bounded, read-only Standard SQL SELECT/WITH query in the "
            "authenticated user's server-derived BigQuery dataset. Qualified names, scripting, "
            "DML/DDL, external queries, and remote/user-defined functions are rejected. Failures "
            "identify validation, dry-run, or execution phase and return sanitized BigQuery "
            "reason/message/location diagnostics when available."
        ),
    }
    documents: list[Document] = []
    for name in ("get_analytics_schema", "run_analytics_query"):
        document: Document = {
            "name": name,
            "description": descriptions[name],
            "inputSchema": ANALYTICS_TOOL_SCHEMAS[name],
            "annotations": {
                "readOnlyHint": True,
                "destructiveHint": False,
                "idempotentHint": True,
                "openWorldHint": True,
            },
        }
        if oauth_protected:
            document["securitySchemes"] = [{"type": "oauth2", "scopes": [MCP_ACCESS_SCOPE]}]
        documents.append(document)
    return documents


class TeslaMCPService:
    """Execute typed operations inside the authenticated user's boundary."""

    def __init__(
        self,
        *,
        fleet: TeslaFleetClient,
        command_fleet: TeslaFleetClient,
        credentials: TeslaAccessProvider,
        store: TeslaOnboardingStore,
        audit_store: CommandAuditStore,
        analytics: AnalyticsProvider | None = None,
        sleep: Callable[[float], None] = time.sleep,
        oauth_protected: bool = True,
    ) -> None:
        self._fleet = PerUserTeslaClient(fleet, credentials)
        self._commands = PerUserTeslaClient(command_fleet, credentials)
        self._store = store
        self._audit_store = audit_store
        self._analytics = analytics
        self._sleep = sleep
        self._oauth_protected = oauth_protected

    def tools(self) -> list[Document]:
        tools = [
            spec.mcp_document(oauth_protected=self._oauth_protected) for spec in MCP_TOOL_SPECS
        ]
        if self._analytics is not None:
            tools.extend(analytics_tool_documents(oauth_protected=self._oauth_protected))
        return tools

    def call(self, context: UserContext, name: str, arguments: object) -> Document:
        correlation_id = f"corr_{secrets.token_hex(16)}"
        try:
            return self._call(context, name, arguments, correlation_id=correlation_id)
        except (MCPToolError, TeslaAPIError) as error:
            if error.correlation_id is None:
                error.correlation_id = correlation_id
            raise

    def _call(
        self,
        context: UserContext,
        name: str,
        arguments: object,
        *,
        correlation_id: str,
    ) -> Document:
        if not isinstance(arguments, dict) or not all(isinstance(key, str) for key in arguments):
            raise MCPToolError("invalid_arguments", "Tool arguments must be a JSON object")
        values = dict(arguments)
        analytics_schema = ANALYTICS_TOOL_SCHEMAS.get(name)
        if analytics_schema is not None:
            if self._analytics is None:
                raise MCPToolError("analytics_unavailable", "Historical analytics is unavailable")
            _validate_schema(values, analytics_schema)
            try:
                if name == "get_analytics_schema":
                    document = self._analytics.get_schema(
                        context,
                        correlation_id=correlation_id,
                    )
                else:
                    document = self._analytics.run_query(
                        context,
                        str(values["sql"]),
                        correlation_id=correlation_id,
                    )
            except AnalyticsQueryError as error:
                raise MCPToolError(
                    error.category,
                    str(error),
                    details=error.response_details(),
                ) from error
            document["correlation_id"] = correlation_id
            return document

        spec = MCP_TOOLS_BY_NAME.get(name)
        if spec is None:
            raise MCPToolError("unknown_tool", "Unknown MCP tool")
        if (
            spec.risk == "security_sensitive"
            and values.get("explicit_current_turn_intent") is not True
        ):
            raise MCPToolError(
                "explicit_intent_required",
                "This security-sensitive operation requires unambiguous current-turn intent",
            )
        _validate_schema(values, spec.input_schema())
        values.pop("explicit_current_turn_intent", None)

        connection = self._store.get_connection(context.user_id)
        if spec.required_scope not in connection.tokens.scopes:
            raise MCPToolError(
                "missing_tesla_scope",
                f"Reconnect Tesla with the required {spec.required_scope} scope",
            )
        if (
            spec.client_method in COMMAND_NAMES
            and "vehicle_device_data" not in connection.tokens.scopes
        ):
            raise MCPToolError(
                "missing_tesla_scope",
                "Reconnect Tesla with the required vehicle_device_data scope "
                "for command wake checks",
            )
        endpoints = arguments.get("endpoints", [])
        if (
            spec.client_method == "vehicle_data"
            and isinstance(endpoints, list)
            and "location_data" in endpoints
            and "vehicle_location" not in connection.tokens.scopes
        ):
            raise MCPToolError(
                "missing_tesla_scope",
                "Reconnect Tesla with the required vehicle_location scope",
            )
        vehicle = self._resolve_vehicle(context.user_id, values.pop("vehicle_id", None))
        if spec.vehicle_scoped and vehicle is None:
            vehicle = self._eligible_vehicle(context.user_id)
        if spec.write:
            if vehicle is None:
                raise MCPToolError("vehicle_required", "A write operation requires a vehicle")
            return self._execute_audited(
                context,
                spec,
                vehicle,
                values,
                correlation_id=correlation_id,
            )
        with tesla_api_log_context(
            correlation_id=correlation_id,
            vehicle_id=vehicle.vehicle_id if vehicle is not None else None,
            source="chatgpt-mcp",
            flow_phase="read",
        ):
            document = _serialize(self._execute(context, spec, vehicle, values))
        document["correlation_id"] = correlation_id
        return document

    def _resolve_vehicle(self, owner_user_id: str, selected: object) -> VehicleRecord | None:
        if selected is not None:
            if not isinstance(selected, str) or not selected:
                raise MCPToolError("invalid_vehicle", "vehicle_id must be a non-empty string")
            try:
                return self._store.get_vehicle(owner_user_id, selected)
            except (CrossUserAccessError, TeslaOnboardingError) as error:
                raise MCPToolError(
                    "vehicle_not_owned", "Vehicle is not owned by this user"
                ) from error
        return None

    def _eligible_vehicle(self, owner_user_id: str) -> VehicleRecord:
        vehicles = [
            vehicle
            for vehicle in self._store.list_vehicles(owner_user_id)
            if vehicle.authorization_status == "active"
        ]
        if not vehicles:
            raise MCPToolError("no_eligible_vehicle", "No eligible Tesla vehicle is connected")
        if len(vehicles) != 1:
            choices = ", ".join(sorted(vehicle.vehicle_id for vehicle in vehicles))
            raise MCPToolError(
                "vehicle_ambiguous",
                f"Multiple eligible vehicles exist; choose one vehicle_id: {choices}",
            )
        return vehicles[0]

    def _execute_audited(
        self,
        context: UserContext,
        spec: ToolSpec,
        vehicle: VehicleRecord,
        values: dict[str, object],
        *,
        correlation_id: str,
    ) -> Document:
        audit_id = f"audit_{secrets.token_hex(16)}"
        try:
            self._audit_store.begin_command_audit(
                audit_id=audit_id,
                timestamp=datetime.now(UTC),
                owner_user_id=context.user_id,
                vehicle_id=vehicle.vehicle_id,
                tool_name=spec.name,
                redacted_parameters=_redact_command_parameters(spec, values),
                correlation_id=correlation_id,
                source="chatgpt-mcp",
            )
        except Exception as error:
            raise MCPToolError(
                "audit_unavailable",
                "Command was not sent because its audit record could not be created",
            ) from error
        try:
            if (
                spec.client_method in COMMAND_NAMES
                and vehicle.command_protocol_required is True
                and vehicle.virtual_key_status != "paired"
            ):
                raise MCPToolError(
                    "virtual_key_not_paired",
                    "The selected vehicle still requires Virtual Key pairing",
                )
            wake_correlation_id = None
            if spec.client_method in COMMAND_NAMES:
                wake_correlation_id = self._ensure_vehicle_online(
                    context,
                    spec,
                    vehicle,
                    command_correlation_id=correlation_id,
                )
            with tesla_api_log_context(
                correlation_id=correlation_id,
                vehicle_id=vehicle.vehicle_id,
                source="chatgpt-mcp",
                flow_phase="command",
            ):
                result = self._execute(context, spec, vehicle, values)
        except Exception as error:
            category = (
                error.category
                if isinstance(error, (TeslaAPIError, MCPToolError))
                else "internal_error"
            )
            self._finalize_audit(audit_id, "failure", category)
            raise
        successful = not isinstance(result, CommandResult) or result.successful
        self._finalize_audit(
            audit_id,
            "success" if successful else "rejected",
            None if successful else "tesla_rejected",
        )
        document = _serialize(result)
        document["correlation_id"] = correlation_id
        if wake_correlation_id is not None:
            document["wake_correlation_id"] = wake_correlation_id
        return document

    def _ensure_vehicle_online(
        self,
        context: UserContext,
        spec: ToolSpec,
        vehicle: VehicleRecord,
        *,
        command_correlation_id: str,
    ) -> str | None:
        """Wake an offline vehicle before a command, without retrying the command itself."""
        with tesla_api_log_context(
            correlation_id=command_correlation_id,
            vehicle_id=vehicle.vehicle_id,
            source="chatgpt-mcp",
            flow_phase="command_preflight",
        ):
            live_vehicle = self._live_vehicle(context.user_id, vehicle.vin)
        if live_vehicle.state == "online":
            return None

        audit_id = f"audit_{secrets.token_hex(16)}"
        correlation_id = f"corr_{secrets.token_hex(16)}"
        try:
            self._audit_store.begin_command_audit(
                audit_id=audit_id,
                timestamp=datetime.now(UTC),
                owner_user_id=context.user_id,
                vehicle_id=vehicle.vehicle_id,
                tool_name="tesla_wake_up",
                redacted_parameters={"automatic_for": spec.name},
                correlation_id=correlation_id,
                source="chatgpt-mcp",
            )
        except Exception as error:
            raise MCPToolError(
                "audit_unavailable",
                "Command was not sent because its automatic wake could not be audited",
            ) from error

        try:
            with tesla_api_log_context(
                correlation_id=correlation_id,
                vehicle_id=vehicle.vehicle_id,
                source="chatgpt-mcp",
                flow_phase="automatic_wake",
            ):
                wake_result = self._fleet.execute(
                    context.user_id,
                    lambda fleet, token, base: fleet.wake_up(
                        token,
                        base_url=base,
                        vin=vehicle.vin,
                    ),
                )
        except Exception as error:
            category = (
                error.category
                if isinstance(error, (TeslaAPIError, MCPToolError))
                else "internal_error"
            )
            self._finalize_audit(audit_id, "failure", category)
            raise

        self._finalize_audit(audit_id, "success", None)
        if wake_result.state == "online":
            return correlation_id

        for _attempt in range(_WAKE_POLL_ATTEMPTS):
            self._sleep(_WAKE_POLL_INTERVAL_SECONDS)
            with tesla_api_log_context(
                correlation_id=correlation_id,
                vehicle_id=vehicle.vehicle_id,
                source="chatgpt-mcp",
                flow_phase="wake_poll",
                flow_iteration=_attempt + 1,
            ):
                if self._live_vehicle(context.user_id, vehicle.vin).state == "online":
                    return correlation_id

        raise MCPToolError(
            "vehicle_unavailable",
            "Vehicle did not come online within 60 seconds; command was not sent",
        )

    def _live_vehicle(self, owner_user_id: str, vin: str) -> TeslaVehicle:
        return self._fleet.execute(
            owner_user_id,
            lambda fleet, token, base: fleet.vehicle(token, base_url=base, vin=vin),
        )

    def _finalize_audit(self, audit_id: str, result: str, error_category: str | None) -> None:
        try:
            self._audit_store.complete_command_audit(
                audit_id=audit_id,
                result=result,
                error_category=error_category,
            )
        except Exception as error:
            raise MCPToolError(
                "command_result_indeterminate",
                "Command may have executed but its audit could not be finalized; do not retry",
            ) from error

    def _execute(
        self,
        context: UserContext,
        spec: ToolSpec,
        vehicle: VehicleRecord | None,
        values: dict[str, object],
    ) -> object:
        if spec.vehicle_scoped and vehicle is None:
            vehicle = self._eligible_vehicle(context.user_id)
        if spec.client_method == "list_vehicles":
            live = self._fleet.execute(
                context.user_id,
                lambda fleet, token, base: fleet.list_vehicles(token, base_url=base),
            )
            owned = {item.vin: item for item in self._store.list_vehicles(context.user_id)}
            return [
                {
                    "vehicle_id": owned[item.vin].vehicle_id,
                    "display_name": item.display_name,
                    "state": item.state,
                    "authorization_status": owned[item.vin].authorization_status,
                    "virtual_key_status": owned[item.vin].virtual_key_status,
                }
                for item in live
                if item.vin in owned and owned[item.vin].authorization_status == "active"
            ]
        if spec.client_method == "charging_invoice":
            invoice_id = str(values["invoice_id"])
            return self._fleet.execute(
                context.user_id,
                lambda fleet, token, base: fleet.charging_invoice(
                    token, base_url=base, invoice_id=invoice_id
                ),
            )
        if vehicle is None:
            return self._fleet.execute(
                context.user_id,
                lambda fleet, token, base: getattr(fleet, spec.client_method)(token, base_url=base),
            )

        if spec.client_method == "fleet_status":
            return self._fleet.execute(
                context.user_id,
                lambda fleet, token, base: fleet.fleet_status(
                    token, base_url=base, vins=[vehicle.vin]
                )[vehicle.vin],
            )
        if spec.client_method == "vehicle_data":
            endpoints = values["endpoints"]
            if not isinstance(endpoints, list):
                raise MCPToolError("invalid_arguments", "endpoints must be an array")
            vehicle_query = VehicleDataQuery(tuple(str(value) for value in endpoints))
            return self._fleet.execute(
                context.user_id,
                lambda fleet, token, base: fleet.vehicle_data(
                    token, base_url=base, vin=vehicle.vin, query=vehicle_query
                ),
            )
        if spec.client_method == "charging_history":
            charging_query = ChargingHistoryQuery(
                vin=vehicle.vin,
                start_time=_optional_datetime(values.get("start_time")),
                end_time=_optional_datetime(values.get("end_time")),
                page=_optional_int(values.get("page")),
                page_size=_optional_int(values.get("page_size")),
                sort_by=_optional_str(values.get("sort_by")),
                sort_order=values.get("sort_order"),  # type: ignore[arg-type]
            )
            return self._fleet.execute(
                context.user_id,
                lambda fleet, token, base: fleet.charging_history(
                    token, base_url=base, query=charging_query
                ),
            )

        request = _request_instance(spec.request_type, values) if spec.request_type else None
        client = self._commands if spec.client_method in COMMAND_NAMES else self._fleet

        def operation(fleet: TeslaFleetClient, token: str, base: str) -> object:
            method = getattr(fleet, spec.client_method)
            keyword: dict[str, object] = {"base_url": base, "vin": vehicle.vin}
            if request is not None:
                keyword["request"] = request
            elif spec.client_method == "nearby_charging_sites":
                keyword.update({key: values.get(key) for key in ("count", "radius", "detail")})
            elif spec.client_method == "release_notes":
                keyword.update({key: values.get(key) for key in ("staged", "language")})
            return method(token, **keyword)

        return client.execute(context.user_id, operation)


class MCPProtocol:
    """Small stateless Streamable HTTP JSON-RPC implementation."""

    def __init__(self, service: TeslaMCPService) -> None:
        self._service = service

    def handle(self, context: UserContext | None, payload: object) -> Document | None:
        if not isinstance(payload, dict) or payload.get("jsonrpc") != "2.0":
            return _jsonrpc_error(None, -32600, "Invalid JSON-RPC request")
        request_id = payload.get("id")
        method = payload.get("method")
        if not isinstance(method, str):
            return _jsonrpc_error(request_id, -32600, "Invalid JSON-RPC method")
        if request_id is None and method in {
            "notifications/initialized",
            "notifications/cancelled",
        }:
            return None
        if method == "initialize":
            return _jsonrpc_result(
                request_id,
                {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": {"name": "tesla-personal-platform", "version": "0.1.0"},
                },
            )
        if method == "ping":
            return _jsonrpc_result(request_id, {})
        if method == "tools/list":
            return _jsonrpc_result(request_id, {"tools": self._service.tools()})
        if method == "tools/call":
            if context is None:
                return _jsonrpc_error(request_id, -32001, "Authentication required")
            params = payload.get("params")
            if not isinstance(params, dict) or not isinstance(params.get("name"), str):
                return _jsonrpc_error(request_id, -32602, "Invalid tool call parameters")
            try:
                result = self._service.call(
                    context,
                    params["name"],
                    params.get("arguments", {}),
                )
            except MCPToolError as error:
                document: Document = {"error": error.category, "message": str(error)}
                document.update(error.details)
                if error.correlation_id is not None:
                    document["correlation_id"] = error.correlation_id
                return _tool_result(
                    request_id,
                    document,
                    True,
                )
            except TeslaAPIError as error:
                document = {"error": error.category, "message": "Tesla Fleet API request failed"}
                if error.correlation_id is not None:
                    document["correlation_id"] = error.correlation_id
                return _tool_result(
                    request_id,
                    document,
                    True,
                )
            except Exception:
                return _tool_result(
                    request_id,
                    {"error": "internal_error", "message": "MCP operation failed"},
                    True,
                )
            return _tool_result(request_id, result, False)
        return _jsonrpc_error(request_id, -32601, "Method not found")

    def authentication_required(self, payload: object, challenge: str) -> Document:
        request_id = payload.get("id") if isinstance(payload, dict) else None
        result = _tool_result(
            request_id,
            {"error": "authentication_required", "message": "Sign in to continue"},
            True,
        )
        result_value = result.get("result")
        if isinstance(result_value, dict):
            result_value["_meta"] = {"mcp/www_authenticate": [challenge]}
        return result


def _tool_result(request_id: object, document: Document, is_error: bool) -> Document:
    return _jsonrpc_result(
        request_id,
        {
            "content": [{"type": "text", "text": json.dumps(document, sort_keys=True)}],
            "structuredContent": document,
            "isError": is_error,
        },
    )


def _jsonrpc_result(request_id: object, result: Document) -> Document:
    return {"jsonrpc": "2.0", "id": _json_id(request_id), "result": result}


def _jsonrpc_error(request_id: object, code: int, message: str) -> Document:
    return {
        "jsonrpc": "2.0",
        "id": _json_id(request_id),
        "error": {"code": code, "message": message},
    }


def _json_id(value: object) -> str | int | None:
    if value is None or isinstance(value, (str, int)) and not isinstance(value, bool):
        return value
    return None


def _request_instance(request_type: type[Any], values: dict[str, object]) -> object:
    names = {field.name for field in fields(request_type)}
    return request_type(**{key: value for key, value in values.items() if key in names})


def _dataclass_schema(model: type[Any]) -> Document:
    hints = get_type_hints(model)
    properties: Document = {}
    required: list[str] = []
    for field in fields(model):
        annotation = hints[field.name]
        properties[field.name] = _annotation_schema(annotation)
        if not _is_optional(annotation):
            required.append(field.name)
    return {"properties": properties, "required": required}


def _annotation_schema(annotation: object) -> Document:
    if isinstance(annotation, TypeAliasType):
        return _annotation_schema(annotation.__value__)
    origin = get_origin(annotation)
    args = get_args(annotation)
    if origin is Literal:
        values = list(args)
        schema = _annotation_schema(type(values[0]))
        schema["enum"] = values
        return schema
    if origin in {Union, types.UnionType}:
        non_none = [arg for arg in args if arg is not type(None)]
        if len(non_none) == 1:
            return _annotation_schema(non_none[0])
    if origin in {tuple, list}:
        return {"type": "array", "items": _annotation_schema(args[0] if args else str)}
    if origin is dict:
        return {"type": "object", "additionalProperties": True}
    if annotation is bool:
        return {"type": "boolean"}
    if annotation is int:
        return {"type": "integer"}
    if annotation is float:
        return {"type": "number"}
    return {"type": "string"}


def _redact_command_parameters(spec: ToolSpec, values: dict[str, object]) -> JsonObject:
    redacted = redact_mapping(values)
    for field_name in _AUDIT_WHOLE_VALUE_FIELDS.get(spec.client_method, ()):
        if field_name in values:
            redacted[field_name] = REDACTED
    return redacted


def _is_optional(annotation: object) -> bool:
    return type(None) in get_args(annotation)


def _validate_schema(arguments: dict[str, object], schema: Document) -> None:
    properties = schema.get("properties", {})
    required = schema.get("required", [])
    if not isinstance(properties, dict) or not isinstance(required, list):
        raise AssertionError("Invalid internal tool schema")
    unknown = set(arguments) - set(properties)
    missing = set(str(value) for value in required) - set(arguments)
    if unknown or missing:
        raise MCPToolError("invalid_arguments", "Tool arguments do not match the typed schema")
    for key, value in arguments.items():
        field_schema = properties[key]
        if not isinstance(field_schema, dict) or not _matches_schema(value, field_schema):
            raise MCPToolError("invalid_arguments", f"Invalid value for {key}")


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


def _serialize(value: object) -> Document:
    if isinstance(value, BinaryDocument):
        return {
            "content_type": value.content_type,
            "content_base64": base64.b64encode(value.content).decode("ascii"),
        }
    if is_dataclass(value):
        return _json_object(asdict(value))  # type: ignore[arg-type]
    if isinstance(value, dict):
        return _json_object(value)
    if isinstance(value, (list, tuple)):
        return {"items": [_json_value(item) for item in value]}
    return {"value": _json_value(value)}


def _json_object(value: dict[Any, Any]) -> Document:
    return {
        str(key): "[REDACTED]"
        if str(key).casefold() in _RESPONSE_SECRET_KEYS
        else _json_value(item)
        for key, item in value.items()
    }


def _json_value(value: object) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, datetime):
        return value.isoformat()
    if is_dataclass(value):
        return _json_object(asdict(value))  # type: ignore[arg-type]
    if isinstance(value, dict):
        return _json_object(value)
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    return str(value)


def _optional_datetime(value: object) -> datetime | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise MCPToolError("invalid_arguments", "Timestamp must be an ISO-8601 string")
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise MCPToolError("invalid_arguments", "Timestamp must be ISO-8601") from error


def _optional_int(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _optional_str(value: object) -> str | None:
    return value if isinstance(value, str) else None
