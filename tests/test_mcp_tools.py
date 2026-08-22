"""Phase 6 MCP coverage, isolation, safety, and audit tests."""

import inspect
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast

import pytest
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
    TokenSet,
)

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
    def __init__(self, *, fail: bool = False) -> None:
        self.calls: list[tuple[str, tuple[object, ...], dict[str, object]]] = []
        self.fail = fail

    def __getattr__(self, name: str):  # type: ignore[no-untyped-def]
        def call(*args: object, **kwargs: object) -> object:
            self.calls.append((name, args, kwargs))
            if self.fail:
                raise TeslaAPIError("safe", category="upstream_failure")
            if name in {"door_lock", "set_pin_to_drive", "wake_up"}:
                return CommandResult(True)
            return ObjectResponse({"operation": name})

        return call


class SecretEchoFleet(FakeFleet):
    def feature_config(self, *_args: object, **_kwargs: object) -> ObjectResponse:
        return ObjectResponse({"access_token": "must-not-leak", "enabled": True})


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


def service(
    store: FakeStore,
    *,
    direct: FakeFleet | None = None,
    proxy: FakeFleet | None = None,
) -> tuple[TeslaMCPService, FakeFleet, FakeFleet]:
    direct = direct or FakeFleet()
    proxy = proxy or FakeFleet()
    instance = TeslaMCPService(
        fleet=cast(TeslaFleetClient, direct),
        command_fleet=cast(TeslaFleetClient, proxy),
        credentials=FakeCredentials(),
        store=cast(Any, store),
        audit_store=store,
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

    assert result == {"data": {"operation": "vehicle"}}
    assert direct.calls[0][2]["vin"] == "VIN1"


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

    assert not direct.calls
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


def test_failed_command_keeps_attempt_and_records_safe_error_category() -> None:
    store = FakeStore([vehicle("veh-one", "user-a", "VIN1")])
    instance, _, _ = service(store, proxy=FakeFleet(fail=True))

    with pytest.raises(TeslaAPIError):
        instance.call(CONTEXT, "tesla_door_lock", {"vehicle_id": "veh-one"})

    assert store.started[0]["source"] == "chatgpt-mcp"
    assert store.completed[0]["result"] == "failure"
    assert store.completed[0]["error_category"] == "upstream_failure"


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
    assert failed["result"]["isError"] is True
    assert failed["result"]["structuredContent"]["error"] == "vehicle_ambiguous"
    assert "secret" not in str(failed)


def test_unexpected_credential_fields_are_redacted_from_tool_responses() -> None:
    fleet = SecretEchoFleet()
    instance, _, _ = service(FakeStore([vehicle("veh-one", "user-a", "VIN1")]), direct=fleet)

    result = instance.call(CONTEXT, "tesla_feature_config", {})

    assert result["data"]["access_token"] == "[REDACTED]"
    assert "must-not-leak" not in str(result)
