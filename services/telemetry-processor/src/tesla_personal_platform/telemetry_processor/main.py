"""Authenticated Pub/Sub push service for permanent raw telemetry."""

import json
import logging
import os
from datetime import UTC, datetime
from hashlib import sha256
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Final, Protocol

from google.cloud import bigquery, firestore
from tesla_personal_platform.telemetry_processor import SERVICE_NAME
from tesla_personal_platform.telemetry_processor.gcp import (
    BigQueryTelemetrySink,
    FirestoreFixtureRetryGate,
    FirestoreVehicleRegistry,
    GooglePushTokenVerifier,
)
from tesla_personal_platform.telemetry_processor.processor import (
    InvalidPushMessageError,
    ProcessingResult,
    RetryableProcessingError,
    TelemetryProcessor,
    TelemetrySourcePolicy,
    decode_pubsub_push,
)

DEFAULT_HOST: Final = "127.0.0.1"
DEFAULT_PORT: Final = 8080
MAX_PUSH_BODY_BYTES: Final = 2_000_000
LOGGER = logging.getLogger(SERVICE_NAME)


class PushTokenVerifier(Protocol):
    def verify(self, authorization: str | None) -> None: ...


class AwaitingInfrastructureVerifier:
    """Permit only the unavailable processor path during a staged rollout."""

    def verify(self, authorization: str | None) -> None:
        del authorization


class AwaitingInfrastructureProcessor:
    """Keep Cloud Run healthy while refusing to acknowledge any delivery."""

    def process(self, incoming: object, *, now: datetime) -> ProcessingResult:
        del incoming, now
        raise RetryableProcessingError("telemetry_infrastructure_not_ready")


def health_document() -> dict[str, str]:
    return {"phase": "raw-telemetry-history", "service": SERVICE_NAME, "status": "ok"}


class TelemetryHTTPServer(ThreadingHTTPServer):
    """HTTP server carrying initialized, immutable runtime dependencies."""

    allow_reuse_address = True

    def __init__(
        self,
        address: tuple[str, int],
        processor: TelemetryProcessor,
        verifier: PushTokenVerifier,
        source_policy: TelemetrySourcePolicy,
    ) -> None:
        self.processor = processor
        self.verifier = verifier
        self.source_policy = source_policy
        super().__init__(address, TelemetryHandler)


class TelemetryHandler(BaseHTTPRequestHandler):
    server: TelemetryHTTPServer

    def do_GET(self) -> None:  # noqa: N802
        if self.path != "/healthz":
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        self._json(HTTPStatus.OK, health_document())

    def do_POST(self) -> None:  # noqa: N802
        if self.path != "/pubsub/push":
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        try:
            self.server.verifier.verify(self.headers.get("Authorization"))
        except Exception:
            self._event("pubsub_push_rejected", outcome="unauthorized")
            self._json(HTTPStatus.UNAUTHORIZED, {"error": "unauthorized"})
            return

        length = self._content_length()
        if length is None:
            return
        body = self.rfile.read(length)
        now = datetime.now(UTC)
        try:
            incoming = decode_pubsub_push(
                body,
                now=now,
                source_policy=self.server.source_policy,
            )
            result = self.server.processor.process(incoming, now=now)
        except InvalidPushMessageError as exc:
            self._event("pubsub_push_rejected", outcome="invalid", category=str(exc))
            # Authenticated but malformed envelopes are poison messages rather
            # than valid telemetry. Record the diagnostic and acknowledge them
            # so one corrupt wrapper cannot permanently block the subscription.
            self.send_response(HTTPStatus.NO_CONTENT)
            self.end_headers()
            return
        except RetryableProcessingError as exc:
            self._event("telemetry_persistence_retry", outcome="retry", category=str(exc))
            self._json(HTTPStatus.SERVICE_UNAVAILABLE, {"error": "retry"})
            return
        except Exception:
            LOGGER.exception(json.dumps({"event": "telemetry_processing_failed"}))
            self._json(HTTPStatus.SERVICE_UNAVAILABLE, {"error": "retry"})
            return

        self._log_result(result)
        self.send_response(HTTPStatus.NO_CONTENT)
        self.end_headers()

    def _content_length(self) -> int | None:
        raw = self.headers.get("Content-Length")
        try:
            length = int(raw or "")
        except ValueError:
            length = -1
        if length < 1 or length > MAX_PUSH_BODY_BYTES:
            # Authentication has already succeeded, so this is an invalid
            # Pub/Sub envelope rather than an untrusted request. Acknowledge it
            # as poison to prevent a permanent retry backlog.
            self._event("pubsub_push_rejected", outcome="invalid", category="invalid_size")
            self.send_response(HTTPStatus.NO_CONTENT)
            self.end_headers()
            return None
        return length

    def _log_result(self, result: ProcessingResult) -> None:
        fields: dict[str, object] = {
            "event": "telemetry_delivery",
            "disposition": result.disposition,
            "record_type": result.record_type,
            "pubsub_message_id": result.pubsub_message_id,
            "source_authentication": result.source_authentication,
        }
        if result.user_id:
            fields["user_id"] = result.user_id
        if result.vehicle_id:
            fields["vehicle_id"] = result.vehicle_id
        if result.telemetry_config_provenance:
            fields["telemetry_config_provenance"] = result.telemetry_config_provenance
            if result.telemetry_config_provenance != "complete":
                fields["event"] = "telemetry_config_provenance_missing"
        if result.quarantine_reason:
            fields["quarantine_reason"] = result.quarantine_reason
            fields["event"] = (
                "unknown_vehicle_telemetry"
                if result.quarantine_reason in {"unknown_vin", "missing_vin"}
                else "telemetry_quarantined"
            )
        if result.fixture_id:
            fields["fixture_fingerprint"] = sha256(result.fixture_id.encode()).hexdigest()[:16]
        LOGGER.info(json.dumps(fields, sort_keys=True))

    def _event(self, event: str, **fields: object) -> None:
        LOGGER.warning(json.dumps({"event": event, **fields}, sort_keys=True))

    def _json(self, status: HTTPStatus, document: dict[str, str]) -> None:
        body = json.dumps(document, sort_keys=True).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        """Avoid logging bearer headers, request bodies, or query strings."""
        del format
        LOGGER.info(
            json.dumps(
                {
                    "event": "http_access",
                    "method": self.command,
                    "path": self.path.partition("?")[0],
                    "status": str(args[1]) if len(args) > 1 else "unknown",
                },
                sort_keys=True,
            )
        )


def build_runtime() -> tuple[TelemetryProcessor, PushTokenVerifier, TelemetrySourcePolicy]:
    project_id = _required_env("GOOGLE_CLOUD_PROJECT")
    audience = _required_env("PUBSUB_PUSH_AUDIENCE")
    push_identity = _required_env("PUBSUB_PUSH_SERVICE_ACCOUNT")
    receiver_version = _required_env("TELEMETRY_RECEIVER_VERSION")
    firestore_client = firestore.Client(project=project_id)
    processor = TelemetryProcessor(
        FirestoreVehicleRegistry(firestore_client),
        BigQueryTelemetrySink(
            bigquery.Client(project=project_id),
            project_id,
            _required_env("QUARANTINE_TABLE"),
            _required_env("SYNTHETIC_TELEMETRY_TABLE"),
        ),
        FirestoreFixtureRetryGate(firestore_client),
        receiver_version=receiver_version,
    )
    synthetic_subscription = os.environ.get(
        "SYNTHETIC_TELEMETRY_SUBSCRIPTION",
        f"projects/{project_id}/subscriptions/tpp-raw-telemetry-processor",
    )
    fleet_subscriptions = os.environ.get("FLEET_TELEMETRY_SUBSCRIPTIONS") or json.dumps(
        {
            (
                f"projects/{project_id}/subscriptions/"
                f"tpp-raw-telemetry-{record_type.lower()}-processor"
            ): record_type
            for record_type in ("V", "alerts", "connectivity", "errors")
        }
    )
    source_policy = TelemetrySourcePolicy.from_json(
        synthetic_subscription,
        fleet_subscriptions,
    )
    return processor, GooglePushTokenVerifier(audience, push_identity), source_policy


def _required_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} is required")
    return value


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    try:
        processor, verifier, source_policy = build_runtime()
    except (RuntimeError, ValueError) as exc:
        LOGGER.warning(
            json.dumps(
                {
                    "event": "telemetry_processor_awaiting_infrastructure",
                    "category": str(exc),
                },
                sort_keys=True,
            )
        )
        # Existing main delivery can run before the post-merge Terraform apply.
        # Stay healthy for rollout while returning 503 for every push, ensuring
        # Pub/Sub retains/redelivers rather than acknowledging any observation.
        processor = AwaitingInfrastructureProcessor()  # type: ignore[assignment]
        verifier = AwaitingInfrastructureVerifier()
        source_policy = TelemetrySourcePolicy(
            "projects/awaiting/subscriptions/synthetic",
            {"projects/awaiting/subscriptions/fleet": "V"},
        )
    host = os.environ.get("HOST", DEFAULT_HOST)
    port = int(os.environ.get("PORT", str(DEFAULT_PORT)))
    TelemetryHTTPServer((host, port), processor, verifier, source_policy).serve_forever()


if __name__ == "__main__":
    main()
