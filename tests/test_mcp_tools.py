"""Phase 6 MCP coverage, isolation, safety, and audit tests."""

import inspect
import re
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast

import pytest
from tesla_personal_platform.analytics import AnalyticsContext, AnalyticsQueryError
from tesla_personal_platform.auth import CrossUserAccessError, UserContext
from tesla_personal_platform.mcp_gateway.mcp_tools import (
    MCP_TOOL_SPECS,
    MCP_TOOLS_BY_NAME,
    MCPProtocol,
    MCPToolError,
    TeslaMCPService,
)
from tesla_personal_platform.mcp_gateway.tesla_onboarding import (
    TeslaConnection,
    TeslaOnboardingError,
    VehicleRecord,
)
from tesla_personal_platform.tesla_client import (
    CommandResult,
    ObjectResponse,
    TeslaAccessContext,
    TeslaAPIError,
    TeslaFleetClient,
    TeslaVehicle,
    TokenSet,
)
from tesla_personal_platform.tesla_client.observability import (
    TeslaAPILogContext,
    current_tesla_api_log_context,
)
from tesla_personal_platform.tesla_client.requests import NavigationRequest

ROOT = Path(__file__).parents[1]
ALL_SCOPES = (
    "openid",
    "offline_access",
    "vehicle_device_data",
    "vehicle_location",
    "vehicle_cmds",
    "vehicle_charging_cmds",
    "user_data",
)


def vehicle(vehicle_id: str, owner: str, vin: str) -> VehicleRecord:
    return VehicleRecord(
        vehicle_id,
        owner,
        "connection",
        vin,
        f"tesla-{vehicle_id}",
        vehicle_id,
        "online",
        "active",
        "paired",
        True,
        "2026.21.6",
        "1.2.0",
        5,
    )


class FakeCredentials:
    def access_for_user(
        self,
        owner_user_id: str,
        *,
        force_refresh: bool = False,
        now: datetime | None = None,
    ) -> TeslaAccessContext:
        return TeslaAccessContext(f"access-for-{owner_user_id}", "https://fleet.example")


class FakeFleet:
    def __init__(
        self,
        *,
        fail: bool = False,
        vehicle_states: list[str] | None = None,
        wake_state: str = "online",
    ) -> None:
        self.calls: list[tuple[str, tuple[object, ...], dict[str, object]]] = []
        self.fail = fail
        self.vehicle_states = vehicle_states or ["online"]
        self.wake_state = wake_state

    def __getattr__(self, name: str):  # type: ignore[no-untyped-def]
        def call(*args: object, **kwargs: object) -> object:
            self.calls.append((name, args, kwargs))
            if self.fail:
                raise TeslaAPIError("safe", category="upstream_failure")
            if name == "vehicle":
                state = (
                    self.vehicle_states.pop(0)
                    if len(self.vehicle_states) > 1
                    else self.vehicle_states[0]
                )
                return TeslaVehicle(str(kwargs["vin"]), "tesla-vehicle", "Test", state)
            if name == "wake_up":
                return TeslaVehicle(str(kwargs["vin"]), "tesla-vehicle", "Test", self.wake_state)
            if name in {"door_lock", "set_charge_limit", "set_pin_to_drive"}:
                return CommandResult(True)
            return ObjectResponse({"operation": name})

        return call


class ContextCapturingFleet(FakeFleet):
    def __init__(
        self,
        *,
        fail: bool = False,
        vehicle_states: list[str] | None = None,
        wake_state: str = "online",
    ) -> None:
        super().__init__(fail=fail, vehicle_states=vehicle_states, wake_state=wake_state)
        self.log_contexts: list[tuple[str, TeslaAPILogContext]] = []

    def __getattr__(self, name: str):  # type: ignore[no-untyped-def]
        operation = super().__getattr__(name)

        def call(*args: object, **kwargs: object) -> object:
            self.log_contexts.append((name, current_tesla_api_log_context()))
            return operation(*args, **kwargs)

        return call


class SecretEchoFleet(FakeFleet):
    def feature_config(self, *_args: object, **_kwargs: object) -> ObjectResponse:
        return ObjectResponse({"access_token": "must-not-leak", "enabled": True})


class UnexpectedFailureFleet(FakeFleet):
    def door_lock(self, *_args: object, **_kwargs: object) -> CommandResult:
        raise RuntimeError("internal implementation detail")


class FakeStore:
    def __init__(self, vehicles: list[VehicleRecord], scopes: tuple[str, ...] = ALL_SCOPES) -> None:
        self.vehicles = vehicles
        self.scopes = scopes
        self.started: list[dict[str, object]] = []
        self.completed: list[dict[str, object]] = []

    def get_connection(self, owner_user_id: str) -> TeslaConnection:
        return TeslaConnection(
            "connection",
            owner_user_id,
            TokenSet(
                "secret-access",
                "secret-refresh",
                datetime.now(UTC) + timedelta(hours=1),
                self.scopes,
                "tesla-subject",
            ),
            1,
            "na",
            "https://fleet.example",
            "connected",
        )

    def list_vehicles(self, owner_user_id: str) -> list[VehicleRecord]:
        return [item for item in self.vehicles if item.owner_user_id == owner_user_id]

    def get_vehicle(self, owner_user_id: str, vehicle_id: str) -> VehicleRecord:
        for item in self.vehicles:
            if item.vehicle_id == vehicle_id:
                if item.owner_user_id != owner_user_id:
                    raise CrossUserAccessError("outside user boundary")
                return item
        raise TeslaOnboardingError("missing")

    def begin_command_audit(self, **values: object) -> None:
        self.started.append(values)

    def complete_command_audit(self, **values: object) -> None:
        self.completed.append(values)


class FailingAuditStore(FakeStore):
    def __init__(self, vehicles: list[VehicleRecord], *, fail_on: str) -> None:
        super().__init__(vehicles)
        self.fail_on = fail_on

    def begin_command_audit(self, **values: object) -> None:
        if self.fail_on == "begin":
            raise RuntimeError("database unavailable")
        super().begin_command_audit(**values)

    def complete_command_audit(self, **values: object) -> None:
        if self.fail_on == "complete":
            raise RuntimeError("database unavailable")
        super().complete_command_audit(**values)


class FakeAnalytics:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.calls: list[tuple[str, AnalyticsContext, object, str]] = []

    def get_schema(self, context: AnalyticsContext, *, correlation_id: str) -> dict[str, Any]:
        self.calls.append(("schema", context, None, correlation_id))
        return {"objects": [{"name": "drives"}]}

    def run_query(
        self,
        context: AnalyticsContext,
        sql: str,
        *,
        correlation_id: str,
    ) -> dict[str, Any]:
        self.calls.append(("query", context, sql, correlation_id))
        if self.fail:
            raise AnalyticsQueryError("dataset_boundary", "Qualified names are not allowed")
        return {"rows": [{"drive_count": 2}], "bytes_processed": 123}


def service(
    store: FakeStore,
    *,
    direct: FakeFleet | None = None,
    proxy: FakeFleet | None = None,
    analytics: FakeAnalytics | None = None,
    sleep: Callable[[float], None] | None = None,
) -> tuple[TeslaMCPService, FakeFleet, FakeFleet]:
    direct = direct or FakeFleet()
    proxy = proxy or FakeFleet()
    instance = TeslaMCPService(
        fleet=cast(TeslaFleetClient, direct),
        command_fleet=cast(TeslaFleetClient, proxy),
        credentials=FakeCredentials(),
        store=cast(Any, store),
        audit_store=store,
        analytics=analytics,
        sleep=sleep or (lambda _seconds: None),
    )
    return instance, direct, proxy


CONTEXT = UserContext("user-a", "dataset-a", "issuer", "subject")


def mcp_matrix_rows() -> set[str]:
    rows: set[str] = set()
    for line in (ROOT / "docs" / "fleet-api-coverage.md").read_text(encoding="utf-8").splitlines():
        if re.match(r"^\|.*\| MCP \|", line):
            rows.add(line.split("|")[1].strip())
    return rows


def matrix_command_risks() -> dict[str, str]:
    risks: dict[str, str] = {}
    in_commands = False
    for line in (ROOT / "docs" / "fleet-api-coverage.md").read_text(encoding="utf-8").splitlines():
        if line.startswith("## Vehicle commands"):
            in_commands = True
            continue
        if in_commands and line.startswith("---"):
            break
        if in_commands and re.match(r"^\|.*\| MCP \|", line):
            cells = [cell.strip() for cell in line.split("|")[1:-1]]
            risks[cells[0]] = cells[5]
    return risks


def test_every_mcp_matrix_row_has_one_typed_tool_mapping() -> None:
    mapped = [spec.matrix_name for spec in MCP_TOOL_SPECS]

    assert set(mapped) == mcp_matrix_rows()
    assert len(mapped) == len(set(mapped))
    assert all(spec.input_schema()["additionalProperties"] is False for spec in MCP_TOOL_SPECS)
    assert all(spec.required_scope for spec in MCP_TOOL_SPECS)
    assert all(spec.wake_behavior for spec in MCP_TOOL_SPECS)
    assert all(spec.retry_policy for spec in MCP_TOOL_SPECS)


def test_excluded_and_compatibility_commands_are_not_mcp_tools() -> None:
    forbidden = {
        "clear_pin_to_drive_admin",
        "erase_user_data",
        "parental_controls_clear_pin_admin",
        "reset_pin_to_drive_pin",
        "reset_valet_pin",
        "speed_limit_clear_pin_admin",
        "set_scheduled_charging",
        "set_scheduled_departure",
    }

    assert not {f"tesla_{name}" for name in forbidden} & set(MCP_TOOLS_BY_NAME)


def test_command_risk_classification_matches_the_coverage_matrix() -> None:
    for command, matrix_risk in matrix_command_risks().items():
        expected = "security_sensitive" if matrix_risk == "Tier 2" else "normal"
        assert MCP_TOOLS_BY_NAME[f"tesla_{command}"].risk == expected


def test_command_tools_advertise_automatic_wake_preflight() -> None:
    for command in matrix_command_risks():
        spec = MCP_TOOLS_BY_NAME[f"tesla_{command}"]
        assert spec.wake_behavior == "auto_if_needed"
        assert spec.retry_policy == "never"


def test_every_command_request_parameter_has_a_typed_input_model() -> None:
    for spec in MCP_TOOL_SPECS:
        if not spec.write or spec.client_method == "wake_up":
            continue
        signature = inspect.signature(getattr(TeslaFleetClient, spec.client_method))
        assert ("request" in signature.parameters) == (spec.request_type is not None)


def test_multiple_vehicles_require_an_explicit_internal_vehicle_id() -> None:
    instance, direct, _ = service(
        FakeStore([vehicle("veh-one", "user-a", "VIN1"), vehicle("veh-two", "user-a", "VIN2")])
    )

    with pytest.raises(MCPToolError) as caught:
        instance.call(CONTEXT, "tesla_vehicle", {})

    assert caught.value.category == "vehicle_ambiguous"
    assert not direct.calls


def test_exactly_one_vehicle_is_auto_selected() -> None:
    instance, direct, _ = service(FakeStore([vehicle("veh-one", "user-a", "VIN1")]))

    result = instance.call(CONTEXT, "tesla_vehicle", {})

    assert result["vin"] == "VIN1"
    assert result["state"] == "online"
    assert direct.calls[0][2]["vin"] == "VIN1"


def test_read_result_correlation_is_propagated_to_tesla_log_context() -> None:
    direct = ContextCapturingFleet()
    instance, _, _ = service(
        FakeStore([vehicle("veh-one", "user-a", "VIN1")]),
        direct=direct,
    )

    result = instance.call(CONTEXT, "tesla_vehicle", {})

    correlation_id = result["correlation_id"]
    assert isinstance(correlation_id, str) and correlation_id.startswith("corr_")
    _, context = direct.log_contexts[0]
    assert context.correlation_id == correlation_id
    assert context.vehicle_id == "veh-one"
    assert context.source == "chatgpt-mcp"
    assert context.flow_phase == "read"


def test_cross_user_vehicle_selection_is_rejected_before_tesla() -> None:
    instance, direct, proxy = service(FakeStore([vehicle("veh-other", "user-b", "OTHER-VIN")]))

    with pytest.raises(MCPToolError) as caught:
        instance.call(CONTEXT, "tesla_vehicle", {"vehicle_id": "veh-other"})

    assert caught.value.category == "vehicle_not_owned"
    assert not direct.calls
    assert not proxy.calls


def test_security_sensitive_command_requires_explicit_current_turn_intent() -> None:
    store = FakeStore([vehicle("veh-one", "user-a", "VIN1")])
    instance, _, proxy = service(store)

    with pytest.raises(MCPToolError) as caught:
        instance.call(CONTEXT, "tesla_door_unlock", {"vehicle_id": "veh-one"})

    assert caught.value.category == "explicit_intent_required"
    assert not proxy.calls
    assert not store.started


def test_write_uses_proxy_and_records_redacted_success_audit() -> None:
    store = FakeStore([vehicle("veh-one", "user-a", "VIN1")])
    instance, direct, proxy = service(store)

    result = instance.call(
        CONTEXT,
        "tesla_set_pin_to_drive",
        {
            "vehicle_id": "veh-one",
            "on": True,
            "password": "1234",
            "explicit_current_turn_intent": True,
        },
    )

    assert [call[0] for call in direct.calls] == ["vehicle"]
    assert proxy.calls[0][0] == "set_pin_to_drive"
    assert store.started[0]["redacted_parameters"] == {
        "on": True,
        "password": "[REDACTED]",
    }
    assert store.completed[0]["result"] == "success"
    serialized = str(result)
    assert "1234" not in serialized
    assert "secret-access" not in serialized
    assert "secret-refresh" not in serialized


def test_navigation_destination_schema_accepts_object_and_redacts_entire_value() -> None:
    store = FakeStore([vehicle("veh-one", "user-a", "VIN1")])
    instance, _, proxy = service(store)
    destination = {
        "address": "123 Private Street",
        "lat": 37.123456,
        "lon": -122.123456,
    }

    instance.call(
        CONTEXT,
        "tesla_navigation_request",
        {
            "vehicle_id": "veh-one",
            "type": "share_dest",
            "value": destination,
            "locale": "en-US",
            "timestamp_ms": "1787610000000",
        },
    )

    request = cast(NavigationRequest, proxy.calls[0][2]["request"])
    assert request.value == destination
    assert store.started[0]["redacted_parameters"] == {
        "type": "share_dest",
        "value": "[REDACTED]",
        "locale": "en-US",
        "timestamp_ms": "1787610000000",
    }
    assert "123 Private Street" not in str(store.started)
    assert "37.123456" not in str(store.started)


def test_navigation_destination_schema_rejects_string_value() -> None:
    store = FakeStore([vehicle("veh-one", "user-a", "VIN1")])
    instance, _, proxy = service(store)

    with pytest.raises(MCPToolError) as caught:
        instance.call(
            CONTEXT,
            "tesla_navigation_request",
            {
                "vehicle_id": "veh-one",
                "type": "share_dest",
                "value": "123 Private Street",
                "locale": "en-US",
                "timestamp_ms": "1787610000000",
            },
        )

    assert caught.value.category == "invalid_arguments"
    assert not proxy.calls
    assert not store.started


def test_navigation_waypoints_are_redacted_as_one_sensitive_payload() -> None:
    store = FakeStore([vehicle("veh-one", "user-a", "VIN1")])
    instance, _, _ = service(store)
    waypoints = '[{"lat":37.123456,"lon":-122.123456}]'

    instance.call(
        CONTEXT,
        "tesla_navigation_waypoints_request",
        {"vehicle_id": "veh-one", "waypoints": waypoints},
    )

    assert store.started[0]["redacted_parameters"] == {"waypoints": "[REDACTED]"}
    assert waypoints not in str(store.started)


def test_failed_command_keeps_attempt_and_records_safe_error_category() -> None:
    store = FakeStore([vehicle("veh-one", "user-a", "VIN1")])
    instance, _, proxy = service(store, proxy=FakeFleet(fail=True))

    with pytest.raises(TeslaAPIError) as caught:
        instance.call(CONTEXT, "tesla_door_lock", {"vehicle_id": "veh-one"})

    assert len(proxy.calls) == 1
    assert caught.value.correlation_id == store.started[0]["correlation_id"]
    assert store.started[0]["source"] == "chatgpt-mcp"
    assert store.completed[0]["result"] == "failure"
    assert store.completed[0]["error_category"] == "upstream_failure"


def test_offline_command_is_woken_audited_and_sent_once() -> None:
    store = FakeStore([vehicle("veh-one", "user-a", "VIN1")])
    direct = FakeFleet(vehicle_states=["offline", "online"], wake_state="asleep")
    sleeps: list[float] = []
    instance, _, proxy = service(store, direct=direct, sleep=sleeps.append)

    result = instance.call(
        CONTEXT,
        "tesla_set_charge_limit",
        {"vehicle_id": "veh-one", "percent": 80},
    )

    assert [call[0] for call in direct.calls] == ["vehicle", "wake_up", "vehicle"]
    assert [call[0] for call in proxy.calls] == ["set_charge_limit"]
    assert sleeps == [10.0]
    assert [audit["tool_name"] for audit in store.started] == [
        "tesla_set_charge_limit",
        "tesla_wake_up",
    ]
    assert store.started[1]["redacted_parameters"] == {"automatic_for": "tesla_set_charge_limit"}
    assert [audit["result"] for audit in store.completed] == ["success", "success"]
    assert result["successful"] is True
    assert result["wake_correlation_id"] == store.started[1]["correlation_id"]


def test_command_wake_phases_have_their_expected_log_correlations() -> None:
    store = FakeStore([vehicle("veh-one", "user-a", "VIN1")])
    direct = ContextCapturingFleet(vehicle_states=["offline", "online"], wake_state="asleep")
    proxy = ContextCapturingFleet()
    instance, _, _ = service(
        store,
        direct=direct,
        proxy=proxy,
        sleep=lambda _seconds: None,
    )

    result = instance.call(
        CONTEXT,
        "tesla_set_charge_limit",
        {"vehicle_id": "veh-one", "percent": 80},
    )

    command_correlation = result["correlation_id"]
    wake_correlation = result["wake_correlation_id"]
    assert direct.log_contexts[0][1].correlation_id == command_correlation
    assert direct.log_contexts[0][1].flow_phase == "command_preflight"
    assert direct.log_contexts[1][1].correlation_id == wake_correlation
    assert direct.log_contexts[1][1].flow_phase == "automatic_wake"
    assert direct.log_contexts[2][1].correlation_id == wake_correlation
    assert direct.log_contexts[2][1].flow_phase == "wake_poll"
    assert direct.log_contexts[2][1].flow_iteration == 1
    assert proxy.log_contexts[0][1].correlation_id == command_correlation
    assert proxy.log_contexts[0][1].flow_phase == "command"


def test_offline_command_is_not_sent_when_wake_times_out() -> None:
    store = FakeStore([vehicle("veh-one", "user-a", "VIN1")])
    direct = FakeFleet(vehicle_states=["offline"], wake_state="asleep")
    sleeps: list[float] = []
    instance, _, proxy = service(store, direct=direct, sleep=sleeps.append)

    with pytest.raises(MCPToolError) as caught:
        instance.call(CONTEXT, "tesla_door_lock", {"vehicle_id": "veh-one"})

    assert caught.value.category == "vehicle_unavailable"
    assert [call[0] for call in direct.calls] == ["vehicle", "wake_up"] + ["vehicle"] * 6
    assert not proxy.calls
    assert sleeps == [10.0] * 6
    assert [audit["result"] for audit in store.completed] == ["success", "failure"]
    assert store.completed[-1]["error_category"] == "vehicle_unavailable"


def test_unexpected_command_failure_uses_normalized_audit_category() -> None:
    store = FakeStore([vehicle("veh-one", "user-a", "VIN1")])
    instance, _, _ = service(store, proxy=UnexpectedFailureFleet())

    with pytest.raises(RuntimeError, match="internal implementation detail"):
        instance.call(CONTEXT, "tesla_door_lock", {"vehicle_id": "veh-one"})

    assert store.completed[0]["result"] == "failure"
    assert store.completed[0]["error_category"] == "internal_error"


def test_command_is_not_sent_when_initial_audit_write_fails() -> None:
    store = FailingAuditStore([vehicle("veh-one", "user-a", "VIN1")], fail_on="begin")
    instance, _, proxy = service(store)

    with pytest.raises(MCPToolError) as caught:
        instance.call(CONTEXT, "tesla_door_lock", {"vehicle_id": "veh-one"})

    assert caught.value.category == "audit_unavailable"
    assert not proxy.calls


def test_audit_completion_failure_warns_that_command_may_have_executed() -> None:
    store = FailingAuditStore([vehicle("veh-one", "user-a", "VIN1")], fail_on="complete")
    instance, _, proxy = service(store)

    with pytest.raises(MCPToolError) as caught:
        instance.call(CONTEXT, "tesla_door_lock", {"vehicle_id": "veh-one"})

    assert caught.value.category == "command_result_indeterminate"
    assert len(proxy.calls) == 1
    assert len(store.started) == 1


def test_missing_scope_is_rejected_before_vehicle_or_tesla_access() -> None:
    store = FakeStore([vehicle("veh-one", "user-a", "VIN1")], scopes=("openid",))
    instance, direct, proxy = service(store)

    with pytest.raises(MCPToolError) as caught:
        instance.call(CONTEXT, "tesla_door_lock", {"vehicle_id": "veh-one"})

    assert caught.value.category == "missing_tesla_scope"
    assert not direct.calls
    assert not proxy.calls
    assert not store.started


def test_command_requires_vehicle_data_scope_for_wake_preflight() -> None:
    store = FakeStore(
        [vehicle("veh-one", "user-a", "VIN1")],
        scopes=("vehicle_cmds",),
    )
    instance, direct, proxy = service(store)

    with pytest.raises(MCPToolError) as caught:
        instance.call(CONTEXT, "tesla_door_lock", {"vehicle_id": "veh-one"})

    assert caught.value.category == "missing_tesla_scope"
    assert not direct.calls
    assert not proxy.calls
    assert not store.started


def test_protocol_lists_typed_tools_and_reports_tool_errors_without_secrets() -> None:
    instance, _, _ = service(
        FakeStore([vehicle("veh-one", "user-a", "VIN1"), vehicle("veh-two", "user-a", "VIN2")])
    )
    protocol = MCPProtocol(instance)

    listed = protocol.handle(CONTEXT, {"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    failed = protocol.handle(
        CONTEXT,
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": "tesla_vehicle", "arguments": {}},
        },
    )

    assert listed is not None
    assert failed is not None
    tools = listed["result"]["tools"]
    assert isinstance(tools, list) and len(tools) == len(MCP_TOOL_SPECS)
    assert all(
        tool["securitySchemes"] == [{"type": "oauth2", "scopes": ["mcp:access"]}] for tool in tools
    )
    assert failed["result"]["isError"] is True
    assert failed["result"]["structuredContent"]["error"] == "vehicle_ambiguous"
    assert failed["result"]["structuredContent"]["correlation_id"].startswith("corr_")
    assert "secret" not in str(failed)


def test_protocol_exposes_and_executes_only_two_general_analytics_tools() -> None:
    analytics = FakeAnalytics()
    instance, direct, proxy = service(
        FakeStore([vehicle("veh-one", "user-a", "VIN1")]),
        analytics=analytics,
    )
    protocol = MCPProtocol(instance)

    listed = protocol.handle(CONTEXT, {"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    schema = protocol.handle(
        CONTEXT,
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": "get_analytics_schema", "arguments": {}},
        },
    )
    query = protocol.handle(
        CONTEXT,
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {
                "name": "run_analytics_query",
                "arguments": {"sql": "SELECT COUNT(*) AS drive_count FROM drives"},
            },
        },
    )

    assert listed is not None and schema is not None and query is not None
    tool_names = {tool["name"] for tool in listed["result"]["tools"]}
    assert {"get_analytics_schema", "run_analytics_query"} <= tool_names
    assert len(tool_names) == len(MCP_TOOL_SPECS) + 2
    assert schema["result"]["structuredContent"]["objects"] == [{"name": "drives"}]
    assert query["result"]["structuredContent"]["rows"] == [{"drive_count": 2}]
    assert [call[0] for call in analytics.calls] == ["schema", "query"]
    assert all(call[1] == CONTEXT for call in analytics.calls)
    assert not direct.calls and not proxy.calls


def test_protocol_returns_safe_analytics_validation_error() -> None:
    analytics = FakeAnalytics(fail=True)
    instance, _, _ = service(
        FakeStore([vehicle("veh-one", "user-a", "VIN1")]),
        analytics=analytics,
    )

    failed = MCPProtocol(instance).handle(
        CONTEXT,
        {
            "jsonrpc": "2.0",
            "id": 4,
            "method": "tools/call",
            "params": {
                "name": "run_analytics_query",
                "arguments": {"sql": "SELECT * FROM other.dataset.table"},
            },
        },
    )

    assert failed is not None
    assert failed["result"]["isError"] is True
    assert failed["result"]["structuredContent"]["error"] == "dataset_boundary"


def test_protocol_tesla_failure_includes_transport_correlation() -> None:
    instance, _, _ = service(
        FakeStore([vehicle("veh-one", "user-a", "VIN1")]),
        direct=FakeFleet(fail=True),
    )
    protocol = MCPProtocol(instance)

    failed = protocol.handle(
        CONTEXT,
        {
            "jsonrpc": "2.0",
            "id": 4,
            "method": "tools/call",
            "params": {
                "name": "tesla_vehicle",
                "arguments": {"vehicle_id": "veh-one"},
            },
        },
    )

    assert failed is not None
    content = failed["result"]["structuredContent"]
    assert failed["result"]["isError"] is True
    assert content["error"] == "upstream_failure"
    assert content["correlation_id"].startswith("corr_")


def test_legacy_mcp_listing_does_not_advertise_unavailable_oauth_flow() -> None:
    store = FakeStore([vehicle("veh-one", "user-a", "VIN1")])
    direct = FakeFleet()
    proxy = FakeFleet()
    instance = TeslaMCPService(
        fleet=cast(TeslaFleetClient, direct),
        command_fleet=cast(TeslaFleetClient, proxy),
        credentials=FakeCredentials(),
        store=cast(Any, store),
        audit_store=store,
        sleep=lambda _seconds: None,
        oauth_protected=False,
    )

    tools = instance.tools()

    assert all("securitySchemes" not in tool for tool in tools)


def test_protocol_authentication_error_includes_chatgpt_linking_challenge() -> None:
    instance, _, _ = service(FakeStore([vehicle("veh-one", "user-a", "VIN1")]))
    protocol = MCPProtocol(instance)
    request = {
        "jsonrpc": "2.0",
        "id": 3,
        "method": "tools/call",
        "params": {"name": "tesla_vehicle", "arguments": {}},
    }

    result = protocol.authentication_required(
        request,
        'Bearer resource_metadata="https://woodhouse.example/.well-known/oauth-protected-resource"',
    )

    assert result["result"]["isError"] is True
    assert "mcp/www_authenticate" in result["result"]["_meta"]


def test_unique_array_items_are_enforced_by_tool_validation() -> None:
    instance, direct, _ = service(FakeStore([vehicle("veh-one", "user-a", "VIN1")]))

    with pytest.raises(MCPToolError) as caught:
        instance.call(
            CONTEXT,
            "tesla_vehicle_data",
            {"vehicle_id": "veh-one", "endpoints": ["location_data", "location_data"]},
        )

    assert caught.value.category == "invalid_arguments"
    assert not direct.calls


def test_unexpected_credential_fields_are_redacted_from_tool_responses() -> None:
    fleet = SecretEchoFleet()
    instance, _, _ = service(FakeStore([vehicle("veh-one", "user-a", "VIN1")]), direct=fleet)

    result = instance.call(CONTEXT, "tesla_feature_config", {})

    assert result["data"]["access_token"] == "[REDACTED]"
    assert "must-not-leak" not in str(result)
