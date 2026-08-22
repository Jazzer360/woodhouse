"""Exhaustive mocked coverage for the Phase 5 typed Fleet API boundary."""

import inspect
import json
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import cast
from urllib.parse import urlsplit

import pytest
from tesla_personal_platform.tesla_client import (
    FleetStatus,
    PerUserTeslaClient,
    TeslaAccessContext,
    TeslaAPIError,
    TeslaFleetClient,
    TeslaPartnerClient,
    TeslaReauthorizationRequired,
    TeslaVehicle,
)
from tesla_personal_platform.tesla_client import requests as request_models
from tesla_personal_platform.tesla_client.coverage import (
    COMMAND_NAMES,
    IMPLEMENTED_ENDPOINTS,
)
from tesla_personal_platform.tesla_client.redaction import REDACTED, redact_mapping
from tesla_personal_platform.tesla_client.transport import HttpResponse

NA_BASE = "https://fleet-api.prd.na.vn.cloud.tesla.com"
TOKEN = "test-access-token"
VIN = "TEST00000000VIN01"


class RecordingTransport:
    def __init__(self, responses: list[HttpResponse]) -> None:
        self.responses = deque(responses)
        self.requests: list[tuple[str, str, dict[str, object]]] = []

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        form: dict[str, str] | None = None,
        json_body: object | None = None,
    ) -> HttpResponse:
        self.requests.append(
            (method, url, {"headers": headers, "form": form, "json_body": json_body})
        )
        return self.responses.popleft()


def response(status: int, document: object) -> HttpResponse:
    return HttpResponse(status, json.dumps(document).encode(), "application/json")


def _matrix_rows() -> list[tuple[str, str, str, str]]:
    rows: list[tuple[str, str, str, str]] = []
    coverage = Path("docs/fleet-api-coverage.md").read_text(encoding="utf-8")
    for line in coverage.splitlines():
        if not line.startswith("|") or "---" in line:
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) >= 5 and cells[0] not in {"Endpoint", "Command"}:
            rows.append((cells[1], cells[2], cells[3], cells[4]))
    return rows


def test_coverage_contract_has_a_typed_method_for_every_required_row() -> None:
    required = {
        (method, path)
        for method, path, implementation, _ in _matrix_rows()
        if implementation == "Required"
    }
    compatibility = {
        (method, path)
        for method, path, implementation, _ in _matrix_rows()
        if implementation == "Compatibility"
    }

    assert len(required) == 101
    assert len(COMMAND_NAMES) == 72
    assert required <= IMPLEMENTED_ENDPOINTS.keys()
    assert compatibility <= IMPLEMENTED_ENDPOINTS.keys()
    assert all(IMPLEMENTED_ENDPOINTS[key].compatibility_only for key in compatibility)

    classes = {"fleet": TeslaFleetClient, "partner": TeslaPartnerClient}
    for key in required | compatibility:
        implementation = IMPLEMENTED_ENDPOINTS[key]
        method = getattr(classes[implementation.client], implementation.method)
        signature = inspect.signature(method)
        assert signature.return_annotation is not inspect.Signature.empty

    command_specs = [
        implementation
        for (method, path), implementation in IMPLEMENTED_ENDPOINTS.items()
        if method == "POST" and "/command/" in path
    ]
    assert all(spec.wake_behavior == "requires_awake" for spec in command_specs)
    assert all(spec.retry_policy == "never" for spec in command_specs)
    assert (
        IMPLEMENTED_ENDPOINTS[("POST", "/api/1/vehicles/{vin}/wake_up")].wake_behavior == "explicit"
    )


COMMAND_REQUESTS: dict[str, request_models.Payload] = {
    "actuate_trunk": request_models.ActuateTrunkRequest("front"),
    "add_charge_schedule": request_models.ChargeScheduleRequest(
        1.0, 2.0, 1, "All", True, 60, True, 120, False, True
    ),
    "add_precondition_schedule": request_models.PreconditionScheduleRequest(
        1.0, 2.0, 1, "All", 60, False, True
    ),
    "adjust_volume": request_models.AdjustVolumeRequest(4.5),
    "guest_mode": request_models.EnableRequest(True),
    "navigation_gps_request": request_models.NavigationGPSRequest(1.0, 2.0, 0),
    "navigation_request": request_models.NavigationRequest(
        "share_ext_content_raw", {"android.intent.extra.TEXT": "destination"}, "en-US", "1"
    ),
    "navigation_sc_request": request_models.NavigationSuperchargerRequest(123, 0),
    "navigation_waypoints_request": request_models.NavigationWaypointRequest("[]"),
    "parental_controls_activate": request_models.PinRequest("1234"),
    "parental_controls_deactivate": request_models.PinRequest("1234"),
    "parental_controls_enable_setting": request_models.ParentalSettingRequest("SpeedLimit", True),
    "parental_controls_set_speed_limit": request_models.SpeedLimitRequest(65),
    "remote_auto_seat_climate_request": request_models.AutoSeatClimateRequest(0, True),
    "remote_auto_steering_wheel_heat_climate_request": request_models.BoolRequest(True),
    "remote_boombox": request_models.SoundRequest(2000),
    "remote_seat_cooler_request": request_models.SeatCoolerRequest(0, 1),
    "remote_seat_heater_request": request_models.SeatHeaterRequest(0, 1),
    "remote_steering_wheel_heat_level_request": request_models.LevelRequest(1),
    "remote_steering_wheel_heater_request": request_models.BoolRequest(True),
    "remove_charge_schedule": request_models.ScheduleIDRequest(1),
    "remove_precondition_schedule": request_models.ScheduleIDRequest(1),
    "schedule_software_update": request_models.SoftwareUpdateRequest(60),
    "set_bioweapon_mode": request_models.OnWithOverrideRequest(True, False),
    "set_cabin_overheat_protection": request_models.CabinOverheatRequest(True, False),
    "set_charge_limit": request_models.ChargeLimitRequest(80),
    "set_charging_amps": request_models.ChargingAmpsRequest(32),
    "set_climate_keeper_mode": request_models.ClimateKeeperRequest(1),
    "set_cop_temp": request_models.CabinOverheatTemperatureRequest(1),
    "set_pin_to_drive": request_models.SetPinRequest(True, "1234"),
    "set_preconditioning_max": request_models.OnWithOverrideRequest(True, False),
    "set_scheduled_charging": request_models.ScheduledChargingRequest(True, 120),
    "set_scheduled_departure": request_models.ScheduledDepartureRequest(
        True, 120, True, False, True, 360
    ),
    "set_sentry_mode": request_models.BoolRequest(True),
    "set_temps": request_models.TemperatureRequest(21.5, 21.5),
    "set_valet_mode": request_models.SetPinRequest(True, "1234"),
    "set_vehicle_name": request_models.VehicleNameRequest("Test Vehicle"),
    "speed_limit_activate": request_models.PinRequest("1234"),
    "speed_limit_clear_pin": request_models.PinRequest("1234"),
    "speed_limit_deactivate": request_models.PinRequest("1234"),
    "speed_limit_set_limit": request_models.SpeedLimitRequest(65),
    "sun_roof_control": request_models.SunRoofRequest("vent"),
    "trigger_homelink": request_models.HomeLinkRequest(1.0, 2.0, "opaque"),
    "upcoming_calendar_entries": request_models.CalendarRequest("[]"),
    "window_control": request_models.WindowControlRequest("close", 1.0, 2.0),
}


@pytest.mark.parametrize("command", COMMAND_NAMES)
def test_every_vehicle_command_has_an_endpoint_level_mock(command: str) -> None:
    transport = RecordingTransport([response(200, {"response": {"result": True}})])
    client = TeslaFleetClient(transport)
    method = getattr(client, command)
    request = COMMAND_REQUESTS.get(command)

    if request is None:
        result = method(TOKEN, base_url=NA_BASE, vin=VIN)
    else:
        result = method(TOKEN, base_url=NA_BASE, vin=VIN, request=request)

    assert result.successful is True
    sent_method, sent_url, sent = transport.requests[0]
    assert sent_method == "POST"
    assert urlsplit(sent_url).path == f"/api/1/vehicles/{VIN}/command/{command}"
    assert sent["json_body"] == (request.to_payload() if request is not None else {})


NON_COMMAND_METHODS = tuple(
    implementation.method
    for (method, path), implementation in IMPLEMENTED_ENDPOINTS.items()
    if implementation.client == "fleet" and "/command/" not in path
)


@pytest.mark.parametrize("method_name", NON_COMMAND_METHODS)
def test_every_non_command_fleet_endpoint_has_an_endpoint_level_mock(method_name: str) -> None:
    raw: dict[str, object] = {"response": {}}
    kwargs: dict[str, object] = {"base_url": NA_BASE}
    if method_name in {
        "drivers",
        "drivers_remove",
        "fleet_telemetry_config_delete",
        "fleet_telemetry_config_get",
        "fleet_telemetry_errors",
        "mobile_enabled",
        "nearby_charging_sites",
        "recent_alerts",
        "release_notes",
        "service_data",
        "share_invites",
        "share_invites_create",
        "share_invites_revoke",
        "signed_command",
        "vehicle",
        "vehicle_data",
        "wake_up",
    }:
        kwargs["vin"] = VIN
    if method_name in {"drivers", "orders", "share_invites", "list_vehicles"}:
        raw = {"response": []}
    if method_name in {"vehicle", "wake_up"}:
        raw = {"response": {"vin": VIN, "id_s": "1"}}
    if method_name == "vehicle_data":
        raw = {"response": {"vin": VIN, "id_s": "1", "charge_state": {}}}
        kwargs["query"] = request_models.VehicleDataQuery(("charge_state",))
    if method_name == "region":
        raw = {"response": {"region": "na", "fleet_api_base_url": NA_BASE}}
    if method_name == "fleet_status":
        raw = {"response": {"vehicle_info": {VIN: {}}}}
        kwargs["vins"] = (VIN,)
    if method_name == "fleet_telemetry_config_create":
        config = request_models.FleetTelemetryConfig(
            "telemetry.example", "PEM", {"Soc": request_models.FleetTelemetryField(60)}
        )
        kwargs["request"] = request_models.FleetTelemetryConfigRequest((VIN,), config)
    if method_name == "fleet_telemetry_config_jws":
        kwargs["request"] = request_models.FleetTelemetryJWSRequest((VIN,), "jws")
    if method_name == "share_invites_redeem":
        kwargs["request"] = request_models.InvitationRedeemRequest("invite")
    if method_name == "share_invites_revoke":
        kwargs["invitation_id"] = "invite-id"
    if method_name == "signed_command":
        kwargs["request"] = request_models.SignedCommandRequest("message")
    if method_name == "charging_history":
        kwargs["query"] = request_models.ChargingHistoryQuery()

    is_invoice = method_name == "charging_invoice"
    if is_invoice:
        kwargs["invoice_id"] = "invoice-id"
        transport = RecordingTransport([HttpResponse(200, b"%PDF", "application/pdf")])
    else:
        transport = RecordingTransport([response(200, raw)])
    client = TeslaFleetClient(transport)

    getattr(client, method_name)(TOKEN, **kwargs)

    actual_method, actual_url, _ = transport.requests[0]
    candidates = [
        key
        for key, implementation in IMPLEMENTED_ENDPOINTS.items()
        if implementation.client == "fleet" and implementation.method == method_name
    ]
    expected_method, expected_path = candidates[0]
    expected_path = expected_path.split("?", maxsplit=1)[0]
    identifier = "invite-id" if method_name == "share_invites_revoke" else "invoice-id"
    expected_path = expected_path.replace("{vin}", VIN).replace("{id}", identifier)
    assert actual_method == expected_method
    assert urlsplit(actual_url).path == expected_path


@pytest.mark.parametrize(
    ("method_name", "document"),
    [
        ("register", {"response": {}}),
        ("public_key", {"response": "public-key"}),
        ("fleet_telemetry_errors", {"response": []}),
        ("fleet_telemetry_error_vins", {"response": []}),
    ],
)
def test_every_partner_endpoint_has_an_endpoint_level_mock(
    method_name: str, document: object
) -> None:
    transport = RecordingTransport([response(200, document)])
    client = TeslaPartnerClient(transport)
    kwargs: dict[str, object] = {"base_url": NA_BASE}
    if method_name in {"register", "public_key"}:
        kwargs["domain"] = "example.com"

    getattr(client, method_name)(TOKEN, **kwargs)

    expected = next(
        key
        for key, implementation in IMPLEMENTED_ENDPOINTS.items()
        if implementation.client == "partner" and implementation.method == method_name
    )
    actual_method, actual_url, _ = transport.requests[0]
    assert actual_method == expected[0]
    assert urlsplit(actual_url).path == expected[1].split("?", maxsplit=1)[0]


def test_targeted_vehicle_data_does_not_implicitly_wake_vehicle() -> None:
    transport = RecordingTransport(
        [response(200, {"response": {"vin": VIN, "id_s": "1", "charge_state": {}}})]
    )
    client = TeslaFleetClient(transport)

    client.vehicle_data(
        TOKEN,
        base_url=NA_BASE,
        vin=VIN,
        query=request_models.VehicleDataQuery(("charge_state",)),
    )

    assert len(transport.requests) == 1
    assert "endpoints=charge_state" in transport.requests[0][1]


def test_safe_reads_retry_but_vehicle_commands_do_not() -> None:
    sleeps: list[float] = []
    read_transport = RecordingTransport(
        [response(503, {"error": "busy"}), response(200, {"response": {}})]
    )
    TeslaFleetClient(read_transport, sleep=sleeps.append).feature_config(TOKEN, base_url=NA_BASE)
    assert len(read_transport.requests) == 2
    assert sleeps == [0.25]

    command_transport = RecordingTransport([response(503, {"error": "busy"})])
    with pytest.raises(TeslaAPIError, match="status 503"):
        TeslaFleetClient(command_transport, sleep=sleeps.append).door_lock(
            TOKEN, base_url=NA_BASE, vin=VIN
        )
    assert len(command_transport.requests) == 1


def test_invalid_response_body_is_not_retained_as_an_exception_cause() -> None:
    transport = RecordingTransport([HttpResponse(200, b"sensitive-upstream-body")])

    with pytest.raises(TeslaAPIError, match="invalid JSON") as caught:
        TeslaFleetClient(transport).feature_config(TOKEN, base_url=NA_BASE)

    assert caught.value.__cause__ is None
    assert "sensitive-upstream-body" not in str(caught.value)


class CredentialProvider:
    def __init__(self) -> None:
        self.calls: list[tuple[str, bool]] = []

    def access_for_user(
        self,
        owner_user_id: str,
        *,
        force_refresh: bool = False,
        now: datetime | None = None,
    ) -> TeslaAccessContext:
        del now
        self.calls.append((owner_user_id, force_refresh))
        return TeslaAccessContext("fresh" if force_refresh else "stale", NA_BASE)


def test_per_user_session_selects_and_refreshes_only_the_requested_user() -> None:
    provider = CredentialProvider()
    fleet = cast(TeslaFleetClient, object())
    session = PerUserTeslaClient(fleet, provider)
    used_tokens: list[str] = []

    def operation(_: TeslaFleetClient, token: str, base_url: str) -> str:
        used_tokens.append(token)
        assert base_url == NA_BASE
        if token == "stale":
            raise TeslaReauthorizationRequired("expired")
        return "ok"

    assert session.execute("user-a", operation) == "ok"
    assert provider.calls == [("user-a", False), ("user-a", True)]
    assert used_tokens == ["stale", "fresh"]


def test_redaction_removes_tokens_pins_vehicle_ids_and_location() -> None:
    redacted = redact_mapping(
        {
            "authorization": "Bearer secret",
            "pin": "1234",
            "vin": VIN,
            "lat": 1.0,
            "nested": {"refresh_token": "secret", "safe": True},
        }
    )

    assert redacted["authorization"] == REDACTED
    assert redacted["pin"] == REDACTED
    assert redacted["vin"] == REDACTED
    assert redacted["lat"] == REDACTED
    assert redacted["nested"] == {"refresh_token": REDACTED, "safe": True}


def test_sensitive_request_models_do_not_reveal_values_in_repr() -> None:
    assert "1234" not in repr(request_models.PinRequest("1234"))
    assert "secret" not in repr(request_models.SignedCommandRequest("secret"))
    assert "secret" not in repr(request_models.HomeLinkRequest(1.0, 2.0, "secret"))
    assert "41.878113" not in repr(request_models.CoordinatesRequest(41.878113, -87.629799))
    assert "-87.629799" not in repr(request_models.CoordinatesRequest(41.878113, -87.629799))
    assert VIN not in repr(request_models.ChargingHistoryQuery(vin=VIN))


def test_vehicle_response_models_do_not_reveal_vins_in_repr() -> None:
    vehicle = TeslaVehicle(VIN, "123", "Woodhouse", "online")
    status = FleetStatus(VIN, True, True, "2026.21.6", "1.2.0", 5, {})

    assert VIN not in repr(vehicle)
    assert VIN not in repr(status)
