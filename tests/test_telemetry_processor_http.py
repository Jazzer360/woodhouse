"""HTTP acknowledgement boundary tests for authenticated Pub/Sub push."""

import base64
import json
import threading
from datetime import UTC, datetime
from http.client import HTTPConnection

from tesla_personal_platform.telemetry_processor.main import TelemetryHTTPServer
from tesla_personal_platform.telemetry_processor.processor import (
    IncomingTelemetry,
    ProcessingResult,
    RetryableProcessingError,
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
            }
        }
    ).encode()


def post(processor: StubProcessor, body: bytes) -> int:
    server = TelemetryHTTPServer(
        ("127.0.0.1", 0),
        processor,  # type: ignore[arg-type]
        AcceptingVerifier(),
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        connection = HTTPConnection("127.0.0.1", server.server_port, timeout=5)
        connection.request(
            "POST",
            "/pubsub/push",
            body=body,
            headers={
                "Authorization": "Bearer signed-token",
                "Content-Type": "application/json",
            },
        )
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
