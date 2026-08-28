"""Typed MCP surface for current Tesla state and intentional vehicle controls."""

from __future__ import annotations

import base64
import secrets
import time
from collections.abc import Callable
from dataclasses import asdict, fields, is_dataclass
from datetime import UTC, datetime
from typing import Any, Literal

from tesla_personal_platform.analytics import AnalyticsQueryError
from tesla_personal_platform.auth import CrossUserAccessError, UserContext
from tesla_personal_platform.mcp_gateway.mcp_policy import (
    ANALYTICS_OPERATIONS,
    MCP_TOOLS_BY_NAME,
    AnalyticsProvider,
    CommandAuditStore,
    MCPToolError,
    ToolSpec,
)
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
    ChargingHistoryQuery,
    VehicleDataQuery,
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
    ) -> None:
        self._fleet = PerUserTeslaClient(fleet, credentials)
        self._commands = PerUserTeslaClient(command_fleet, credentials)
        self._store = store
        self._audit_store = audit_store
        self._analytics = analytics
        self._sleep = sleep

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
        if name in ANALYTICS_OPERATIONS:
            if self._analytics is None:
                raise MCPToolError("analytics_unavailable", "Historical analytics is unavailable")
            if name == "get_analytics_schema" and values:
                raise MCPToolError("invalid_arguments", "Schema operation takes no arguments")
            if name == "run_analytics_query" and (
                set(values) != {"sql"} or not isinstance(values.get("sql"), str)
            ):
                raise MCPToolError("invalid_arguments", "Analytics query requires one SQL string")
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
                raise MCPToolError(error.category, str(error)) from error
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
        spec.validate_arguments(values)
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


def _request_instance(request_type: type[Any], values: dict[str, object]) -> object:
    names = {field.name for field in fields(request_type)}
    return request_type(**{key: value for key, value in values.items() if key in names})


def _redact_command_parameters(spec: ToolSpec, values: dict[str, object]) -> JsonObject:
    redacted = redact_mapping(values)
    for field_name in _AUDIT_WHOLE_VALUE_FIELDS.get(spec.client_method, ()):
        if field_name in values:
            redacted[field_name] = REDACTED
    return redacted


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
