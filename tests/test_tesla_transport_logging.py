"""Structured Tesla transport logging and redaction tests."""

import json
import logging
from collections import deque
from email.message import Message
from io import BytesIO
from urllib.error import HTTPError, URLError

import pytest
from tesla_personal_platform.tesla_client import (
    LocalCommandProxyTransport,
    TeslaFleetClient,
    TeslaIDTokenVerifier,
    TeslaTransportError,
    UrllibTransport,
    tesla_api_log_context,
)

NA_BASE = "https://fleet-api.prd.na.vn.cloud.tesla.com"
VIN = "TESTCAR0000000001"
LOGGER_NAME = "tesla_personal_platform.tesla_client.api_calls"


class FakeResponse:
    def __init__(self, status: int, document: object) -> None:
        self.status = status
        self.body = json.dumps(document).encode("utf-8")
        self.headers = Message()
        self.headers["Content-Type"] = "application/json; charset=utf-8"

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self) -> bytes:
        return self.body


class FakeOpener:
    def __init__(self, outcomes: list[FakeResponse | Exception]) -> None:
        self.outcomes = deque(outcomes)

    def open(self, *_args: object, **_kwargs: object) -> FakeResponse:
        outcome = self.outcomes.popleft()
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def _events(caplog: pytest.LogCaptureFixture) -> list[dict[str, object]]:
    return [json.loads(record.message) for record in caplog.records if record.name == LOGGER_NAME]


def _http_error(status: int, document: object) -> HTTPError:
    body = BytesIO(json.dumps(document).encode("utf-8"))
    headers = Message()
    headers["Content-Type"] = "application/json"
    return HTTPError("https://redacted.invalid", status, "failure", headers, body)


def test_direct_transport_logs_start_and_redacted_http_result(
    caplog: pytest.LogCaptureFixture,
) -> None:
    access_token = "secret-access-token"
    response_token = "response-secret-token"
    destination = "123 Test Street"
    transport = UrllibTransport()
    transport._opener = FakeOpener(  # type: ignore[assignment]
        [
            _http_error(
                400,
                {
                    "error": "command not implemented",
                    "error_description": (
                        f"vehicle {VIN} owner@example.com at 44.9; "
                        f"access_token={response_token}; destination {destination}; "
                        "https://example.invalid/private"
                    ),
                },
            )
        ]
    )

    with (
        caplog.at_level(logging.INFO, logger=LOGGER_NAME),
        tesla_api_log_context(
            correlation_id="corr_test",
            vehicle_id="veh_internal",
            source="chatgpt-mcp",
            flow_phase="command",
        ),
    ):
        response = transport.request(
            "POST",
            f"{NA_BASE}/api/1/vehicles/{VIN}/command/remote_boombox",
            headers={"Authorization": f"Bearer {access_token}"},
            json_body={
                "sound": 0,
                "vin": VIN,
                "lat": 44.9,
                "navigation": {"value": destination},
            },
        )

    assert response.status == 400
    events = _events(caplog)
    assert [event["phase"] for event in events] == ["started", "completed"]
    started, completed = events
    assert started["route"] == "/api/1/vehicles/{vin}/command/remote_boombox"
    assert started["operation"] == "remote_boombox"
    assert started["destination"] == "tesla_fleet_api"
    assert started["region"] == "na"
    assert started["request_fields"] == ["lat", "navigation", "sound", "vin"]
    assert started["correlation_id"] == "corr_test"
    assert started["vehicle_id"] == "veh_internal"
    assert completed["status_code"] == 400
    assert completed["outcome"] == "client_error"
    assert completed["response_summary"] == {
        "error": "command not implemented",
        "error_description": (
            "vehicle [REDACTED] [REDACTED_EMAIL] at [REDACTED]; "
            "[REDACTED_CREDENTIAL]; destination [REDACTED]; [REDACTED_URL]"
        ),
    }
    serialized = "\n".join(record.message for record in caplog.records)
    assert VIN not in serialized
    assert access_token not in serialized
    assert "44.9" not in serialized
    assert "owner@example.com" not in serialized
    assert response_token not in serialized
    assert destination not in serialized


def test_local_proxy_logs_command_result_without_body_or_credentials(
    caplog: pytest.LogCaptureFixture,
) -> None:
    transport = object.__new__(LocalCommandProxyTransport)
    object.__setattr__(transport, "_origin", "https://localhost:4443")
    object.__setattr__(transport, "_timeout_seconds", 1.0)
    object.__setattr__(
        transport,
        "_opener",
        FakeOpener([FakeResponse(200, {"response": {"result": True, "reason": "ok"}})]),
    )

    with caplog.at_level(logging.INFO, logger=LOGGER_NAME):
        response = transport.request(
            "POST",
            f"{NA_BASE}/api/1/vehicles/{VIN}/command/set_charge_limit",
            headers={"Authorization": "Bearer private-token"},
            json_body={"percent": 80},
        )

    assert response.status == 200
    completed = _events(caplog)[1]
    assert completed["destination"] == "vehicle_command_proxy"
    assert completed["response_summary"] == {"result": True, "reason": "ok"}
    serialized = "\n".join(record.message for record in caplog.records)
    assert VIN not in serialized
    assert "private-token" not in serialized
    assert '"percent":80' not in serialized


def test_successful_read_skips_diagnostic_response_parsing(
    caplog: pytest.LogCaptureFixture,
) -> None:
    transport = UrllibTransport()
    transport._opener = FakeOpener(  # type: ignore[assignment]
        [FakeResponse(200, {"message": "large read response must not be summarized"})]
    )

    with caplog.at_level(logging.INFO, logger=LOGGER_NAME):
        response = transport.request(
            "GET",
            f"{NA_BASE}/api/1/vehicles/{VIN}/vehicle_data",
            headers={"Authorization": "Bearer private-token"},
        )

    assert response.status == 200
    completed = _events(caplog)[1]
    assert "response_summary" not in completed


def test_diagnostic_summary_redacts_common_credential_syntax(
    caplog: pytest.LogCaptureFixture,
) -> None:
    credential_values = ("alpha-value", "beta-value", "gamma-value", "delta-value")
    transport = UrllibTransport()
    transport._opener = FakeOpener(  # type: ignore[assignment]
        [
            _http_error(
                401,
                {
                    "error_description": (
                        f"access_token={credential_values[0]} token: {credential_values[1]} "
                        f'"client_secret":"{credential_values[2]}" '
                        f"Bearer {credential_values[3]}"
                    )
                },
            )
        ]
    )

    with caplog.at_level(logging.INFO, logger=LOGGER_NAME):
        transport.request("GET", f"{NA_BASE}/api/1/vehicles/{VIN}")

    completed = _events(caplog)[1]
    assert completed["response_summary"] == {
        "error_description": " ".join(["[REDACTED_CREDENTIAL]"] * 4)
    }
    serialized = "\n".join(record.message for record in caplog.records)
    assert all(value not in serialized for value in credential_values)


def test_oversized_error_body_skips_diagnostic_response_parsing(
    caplog: pytest.LogCaptureFixture,
) -> None:
    transport = UrllibTransport()
    transport._opener = FakeOpener(  # type: ignore[assignment]
        [_http_error(500, {"error_description": "x" * (64 * 1024)})]
    )

    with caplog.at_level(logging.INFO, logger=LOGGER_NAME):
        response = transport.request(
            "GET",
            f"{NA_BASE}/api/1/vehicles/{VIN}",
            headers={"Authorization": "Bearer private-token"},
        )

    assert response.status == 500
    completed = _events(caplog)[1]
    assert "response_summary" not in completed


def test_transport_failure_logs_safe_terminal_event(caplog: pytest.LogCaptureFixture) -> None:
    transport = UrllibTransport()
    transport._opener = FakeOpener(  # type: ignore[assignment]
        [URLError("network failure containing secret-access-token")]
    )

    with (
        caplog.at_level(logging.INFO, logger=LOGGER_NAME),
        pytest.raises(TeslaTransportError),
    ):
        transport.request(
            "GET",
            f"{NA_BASE}/api/1/vehicles/{VIN}",
            headers={"Authorization": "Bearer secret-access-token"},
        )

    events = _events(caplog)
    assert [event["phase"] for event in events] == ["started", "failed"]
    assert events[1]["outcome"] == "transport_error"
    assert events[1]["error_category"] == "transport_error"
    serialized = "\n".join(record.message for record in caplog.records)
    assert VIN not in serialized
    assert "secret-access-token" not in serialized
    assert "network failure" not in serialized


def test_unexpected_transport_failure_is_logged_without_exception_text(
    caplog: pytest.LogCaptureFixture,
) -> None:
    transport = UrllibTransport()
    transport._opener = FakeOpener(  # type: ignore[assignment]
        [RuntimeError("unexpected failure containing secret-access-token")]
    )

    with (
        caplog.at_level(logging.INFO, logger=LOGGER_NAME),
        pytest.raises(RuntimeError),
    ):
        transport.request(
            "GET",
            f"{NA_BASE}/api/1/vehicles/{VIN}",
            headers={"Authorization": "Bearer secret-access-token"},
        )

    events = _events(caplog)
    assert [event["phase"] for event in events] == ["started", "failed"]
    assert events[1]["error_category"] == "unexpected_transport_error"
    serialized = "\n".join(record.message for record in caplog.records)
    assert VIN not in serialized
    assert "secret-access-token" not in serialized
    assert "unexpected failure" not in serialized


def test_jwks_fetch_uses_logged_tesla_transport(caplog: pytest.LogCaptureFixture) -> None:
    transport = UrllibTransport()
    transport._opener = FakeOpener([FakeResponse(200, {"keys": []})])  # type: ignore[assignment]
    verifier = TeslaIDTokenVerifier(transport=transport)

    with caplog.at_level(logging.INFO, logger=LOGGER_NAME):
        document = verifier._jwks.fetch_data()

    assert document == {"keys": []}
    events = _events(caplog)
    assert [event["phase"] for event in events] == ["started", "completed"]
    assert events[0]["operation"] == "oauth_jwks"
    assert events[0]["destination"] == "tesla_oauth"
    assert events[0]["route"] == "/oauth2/v3/discovery/thirdparty/keys"


def test_each_safe_read_retry_has_an_attempt_number(caplog: pytest.LogCaptureFixture) -> None:
    transport = UrllibTransport()
    transport._opener = FakeOpener(  # type: ignore[assignment]
        [
            _http_error(503, {"error": "temporarily_unavailable"}),
            FakeResponse(200, {"response": {"enabled": True}}),
        ]
    )
    client = TeslaFleetClient(transport, sleep=lambda _seconds: None)

    with caplog.at_level(logging.INFO, logger=LOGGER_NAME):
        result = client.feature_config("secret-access-token", base_url=NA_BASE)

    assert result.data["enabled"] is True
    events = _events(caplog)
    assert [event["phase"] for event in events] == [
        "started",
        "completed",
        "started",
        "completed",
    ]
    assert [event["attempt"] for event in events] == [1, 1, 2, 2]
    assert events[1]["status_code"] == 503
    assert events[3]["status_code"] == 200


def test_invalid_destination_is_rejected_without_logging(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with (
        caplog.at_level(logging.INFO, logger=LOGGER_NAME),
        pytest.raises(ValueError, match="approved Tesla HTTPS host"),
    ):
        UrllibTransport().request(
            "GET",
            "https://attacker.example/collect",
            headers={"Authorization": "Bearer secret-access-token"},
        )

    assert not _events(caplog)
