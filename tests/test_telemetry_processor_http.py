"""HTTP acknowledgement boundary tests for authenticated Pub/Sub push."""

import base64
import json
import threading
from datetime import UTC, datetime
from http.client import HTTPConnection
from typing import Any

import pytest
from tesla_personal_platform.telemetry_processor import main as telemetry_main
from tesla_personal_platform.telemetry_processor.main import (
    MAX_PUSH_BODY_BYTES,
    TelemetryHTTPServer,
)
from tesla_personal_platform.telemetry_processor.processor import (
    IncomingTelemetry,
    ProcessingResult,
    RetryableProcessingError,
    TelemetrySourcePolicy,
)

FLEET_SUBSCRIPTION = "projects/test/subscriptions/tpp-raw-telemetry-v-processor"
SOURCE_POLICY = TelemetrySourcePolicy(
    "projects/test/subscriptions/tpp-raw-telemetry-processor",
    {FLEET_SUBSCRIPTION: "V"},
)


class AcceptingVerifier:
    def verify(self, authorization: str | None) -> None:
        assert authorization == "Bearer signed-token"


class StubProcessor:
    def __init__(self, failure: Exception | None = None) -> None:
        self.failure = failure
        self.calls = 0

    def process(self, incoming: IncomingTelemetry, *, now: datetime) -> ProcessingResult:
        del now
        self.calls += 1
        if self.failure is not None:
            raise self.failure
        return ProcessingResult("persisted", incoming.record_type, incoming.pubsub_message_id)


def valid_push() -> bytes:
    payload = {
        "createdAt": "2026-08-23T12:00:00Z",
        "vin": "VIN-A",
        "data": [],
    }
    return json.dumps(
        {
            "message": {
                "messageId": "message-1",
                "publishTime": "2026-08-23T12:00:01Z",
                "attributes": {
                    "vin": "VIN-A",
                    "receivedat": str(int(datetime.now(UTC).timestamp() * 1000)),
                    "txtype": "V",
                },
                "data": base64.b64encode(json.dumps(payload).encode()).decode(),
            },
            "subscription": FLEET_SUBSCRIPTION,
        }
    ).encode()


def post(
    processor: StubProcessor,
    body: bytes,
    *,
    content_length: int | None = None,
    omit_content_length: bool = False,
) -> int:
    server = TelemetryHTTPServer(
        ("127.0.0.1", 0),
        processor,  # type: ignore[arg-type]
        AcceptingVerifier(),
        SOURCE_POLICY,
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        connection = HTTPConnection("127.0.0.1", server.server_port, timeout=5)
        headers = {
            "Authorization": "Bearer signed-token",
            "Content-Type": "application/json",
        }
        if omit_content_length:
            connection.putrequest("POST", "/pubsub/push")
            for name, value in headers.items():
                connection.putheader(name, value)
            connection.endheaders()
        else:
            if content_length is not None:
                headers["Content-Length"] = str(content_length)
            connection.request("POST", "/pubsub/push", body=body, headers=headers)
        response = connection.getresponse()
        response.read()
        return response.status
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_http_acknowledges_only_after_processor_reports_durable_success() -> None:
    service = StubProcessor()

    assert post(service, valid_push()) == 204
    assert service.calls == 1


def test_http_negatively_acknowledges_retryable_persistence_failure() -> None:
    service = StubProcessor(RetryableProcessingError("bigquery_append_failed"))

    assert post(service, valid_push()) == 503
    assert service.calls == 1


def test_authenticated_malformed_poison_wrapper_is_logged_and_acknowledged() -> None:
    service = StubProcessor()

    assert post(service, b"not-json") == 204
    assert service.calls == 0


def test_authenticated_oversized_poison_delivery_is_acknowledged() -> None:
    service = StubProcessor()

    assert post(service, b"x", content_length=MAX_PUSH_BODY_BYTES + 1) == 204
    assert service.calls == 0


def test_authenticated_delivery_without_content_length_is_acknowledged() -> None:
    service = StubProcessor()

    assert post(service, b"", omit_content_length=True) == 204
    assert service.calls == 0


def test_invalid_source_policy_starts_in_retryable_awaiting_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    def invalid_runtime() -> None:
        raise ValueError("telemetry subscription policy is invalid")

    class CapturingServer:
        def __init__(self, address: object, processor: object, *args: object) -> None:
            captured["address"] = address
            captured["processor"] = processor

        def serve_forever(self) -> None:
            captured["served"] = True

    monkeypatch.setattr(telemetry_main, "build_runtime", invalid_runtime)
    monkeypatch.setattr(telemetry_main, "TelemetryHTTPServer", CapturingServer)
    monkeypatch.setenv("HOST", "127.0.0.1")
    monkeypatch.setenv("PORT", "8080")

    telemetry_main.main()

    assert captured["address"] == ("127.0.0.1", 8080)
    assert isinstance(captured["processor"], telemetry_main.AwaitingInfrastructureProcessor)
    assert captured["served"] is True
