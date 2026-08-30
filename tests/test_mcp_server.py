"""Official MCP SDK and semantic capability-family tests."""

from __future__ import annotations

import asyncio
from collections import Counter
from typing import Any, cast, get_args

from mcp import Client
from tesla_personal_platform.auth import UserContext
from tesla_personal_platform.mcp_gateway.mcp_models import (
    AccountRead,
    ChargingControl,
    ChargingRecordRead,
    ClimateControl,
    MediaControl,
    NavigationControl,
    SecurityControl,
    VehicleAccessControl,
    VehicleRead,
    VehicleSettingsControl,
)
from tesla_personal_platform.mcp_gateway.mcp_policy import MCP_TOOL_SPECS
from tesla_personal_platform.mcp_gateway.mcp_server import create_mcp_server
from tesla_personal_platform.mcp_gateway.mcp_tools import TeslaMCPService
from tesla_personal_platform.tesla_client import TeslaAPIError

CONTEXT = UserContext("usr_private", "tesla_u_private", "issuer", "subject")


class RecordingService:
    def __init__(self) -> None:
        self.calls: list[tuple[UserContext, str, object]] = []

    def call(self, context: UserContext, name: str, arguments: object) -> dict[str, Any]:
        self.calls.append((context, name, arguments))
        return {"operation": name, "arguments": arguments}


def test_every_private_operation_is_reachable_from_exactly_one_semantic_family() -> None:
    family_models = (
        AccountRead,
        VehicleRead,
        ChargingRecordRead,
        VehicleAccessControl,
        ClimateControl,
        ChargingControl,
        MediaControl,
        NavigationControl,
        SecurityControl,
        VehicleSettingsControl,
    )
    exposed = Counter(
        action
        for model in family_models
        for action in get_args(model.model_fields["action"].annotation)
    )
    expected = {spec.client_method for spec in MCP_TOOL_SPECS if spec.client_method != "wake_up"}

    assert set(exposed) == expected
    assert all(count == 1 for count in exposed.values())


def test_official_sdk_exposes_compact_semantic_surface_and_routes_typed_call() -> None:
    service = RecordingService()
    server = create_mcp_server(
        cast(TeslaMCPService, service),
        test_user_context=CONTEXT,
    )

    async def scenario() -> None:
        async with Client(server) as client:
            tools = await client.list_tools()
            names = [tool.name for tool in tools.tools]
            assert names == [
                "get_tesla_account",
                "get_vehicle_status",
                "get_charging_records",
                "control_vehicle_access",
                "control_vehicle_climate",
                "control_vehicle_charging",
                "control_vehicle_media",
                "control_vehicle_navigation",
                "control_vehicle_security",
                "control_vehicle_settings",
                "wake_vehicle",
                "get_analytics_schema",
                "run_analytics_query",
            ]
            result = await client.call_tool(
                "control_vehicle_climate",
                {
                    "request": {
                        "action": "set_temps",
                        "vehicle_id": "veh_private",
                        "driver_temp": 20.5,
                        "passenger_temp": 21.0,
                    }
                },
            )
            assert result.is_error is False
            assert result.structured_content is not None
            assert result.structured_content["operation"] == "tesla_set_temps"

    asyncio.run(scenario())
    assert service.calls == [
        (
            CONTEXT,
            "tesla_set_temps",
            {
                "vehicle_id": "veh_private",
                "driver_temp": 20.5,
                "passenger_temp": 21.0,
            },
        )
    ]


def test_semantic_model_rejects_missing_action_specific_fields_before_dispatch() -> None:
    service = RecordingService()
    server = create_mcp_server(
        cast(TeslaMCPService, service),
        test_user_context=CONTEXT,
    )

    async def scenario() -> None:
        async with Client(server) as client:
            result = await client.call_tool(
                "control_vehicle_climate",
                {"request": {"action": "set_temps", "vehicle_id": "veh_private"}},
            )
            assert result.is_error is True

    asyncio.run(scenario())
    assert service.calls == []


def test_official_sdk_error_result_never_includes_upstream_secret_text() -> None:
    class FailingService:
        def call(self, context: UserContext, name: str, arguments: object) -> dict[str, Any]:
            del context, name, arguments
            raise TeslaAPIError(
                "upstream leaked access_token=must-not-escape",
                category="upstream_failure",
                correlation_id="corr_safe",
            )

    server = create_mcp_server(
        cast(TeslaMCPService, FailingService()),
        test_user_context=CONTEXT,
    )

    async def scenario() -> None:
        async with Client(server) as client:
            result = await client.call_tool("get_tesla_account", {"request": {"action": "me"}})
            assert result.is_error is True
            rendered = str(result)
            assert "must-not-escape" not in rendered
            assert "upstream_failure" in rendered
            assert "corr_safe" in rendered

    asyncio.run(scenario())
