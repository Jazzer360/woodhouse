"""Pure telemetry ingestion and routing logic."""

import base64
import binascii
import json
import re
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Any, Protocol

from tesla_personal_platform.shared_models import RawTelemetryEvent

ALLOWED_RECORD_TYPES = frozenset({"V", "alerts", "connectivity", "errors"})
SYNTHETIC_MARKER = "phase7"
FIXTURE_ID = re.compile(r"^phase7[-_][A-Za-z0-9_-]{1,80}$")


class InvalidPushMessageError(ValueError):
    """The Pub/Sub wrapper cannot be safely interpreted."""


class RetryableProcessingError(RuntimeError):
    """Persistence or an intentional fixture retry requires Pub/Sub redelivery."""


@dataclass(frozen=True, slots=True)
class VehicleRoute:
    """Trusted server-side destination for one vehicle."""

    user_id: str
    dataset_id: str
    vehicle_id: str
    tesla_vehicle_id: str | None
    telemetry_config_version: str | None = None
    telemetry_config_hash: str | None = None


class VehicleRegistry(Protocol):
    def resolve(self, vin: str) -> VehicleRoute | None:
        """Resolve a certificate-derived VIN without caller ownership input."""


class TelemetrySink(Protocol):
    def append_user(self, dataset_id: str, event: RawTelemetryEvent) -> None: ...

    def append_quarantine(self, event: RawTelemetryEvent, reason: str) -> None: ...

    def append_synthetic(
        self,
        event: RawTelemetryEvent,
        fixture_id: str,
        *,
        first_failure_recorded: bool,
    ) -> None: ...


class FixtureRetryGate(Protocol):
    def should_fail_first(self, fixture_id: str, *, now: datetime) -> bool: ...


@dataclass(frozen=True, slots=True)
class IncomingTelemetry:
    """One decoded Pub/Sub delivery from Tesla's official receiver."""

    source_timestamp: datetime
    edge_received_at: datetime
    payload: dict[str, Any]
    vin: str | None
    record_type: str
    transport_message_id: str | None
    pubsub_message_id: str
    pubsub_publish_time: datetime | None
    pubsub_delivery_attempt: int | None
    telemetry_client_version: str | None
    receiver_record_version: int | None
    validation_error: str | None
    synthetic_fixture_id: str | None
    synthetic_fail_first: bool


@dataclass(frozen=True, slots=True)
class ProcessingResult:
    disposition: str
    record_type: str
    pubsub_message_id: str
    vehicle_id: str | None = None
    user_id: str | None = None
    quarantine_reason: str | None = None
    fixture_id: str | None = None


class TelemetryProcessor:
    """Route every delivery exactly once per invocation without raw de-duplication."""

    def __init__(
        self,
        registry: VehicleRegistry,
        sink: TelemetrySink,
        retry_gate: FixtureRetryGate,
        *,
        receiver_version: str,
    ) -> None:
        self._registry = registry
        self._sink = sink
        self._retry_gate = retry_gate
        self._receiver_version = receiver_version

    def process(self, incoming: IncomingTelemetry, *, now: datetime) -> ProcessingResult:
        """Persist or quarantine one delivery before allowing its HTTP acknowledgement."""
        event = self._event(incoming, now=now)
        if incoming.validation_error is not None:
            self._sink.append_quarantine(event, incoming.validation_error)
            return ProcessingResult(
                "quarantined",
                incoming.record_type,
                incoming.pubsub_message_id,
                quarantine_reason=incoming.validation_error,
            )

        fixture_id = incoming.synthetic_fixture_id
        if fixture_id is not None:
            first_failure_recorded = False
            if incoming.synthetic_fail_first:
                if self._retry_gate.should_fail_first(fixture_id, now=now):
                    raise RetryableProcessingError("synthetic_first_delivery_failure")
                first_failure_recorded = True
            self._sink.append_synthetic(
                event,
                fixture_id,
                first_failure_recorded=first_failure_recorded,
            )
            return ProcessingResult(
                "synthetic",
                incoming.record_type,
                incoming.pubsub_message_id,
                fixture_id=fixture_id,
            )

        if incoming.vin is None:
            self._sink.append_quarantine(event, "missing_vin")
            return ProcessingResult(
                "quarantined",
                incoming.record_type,
                incoming.pubsub_message_id,
                quarantine_reason="missing_vin",
            )

        route = self._registry.resolve(incoming.vin)
        if route is None:
            self._sink.append_quarantine(event, "unknown_vin")
            return ProcessingResult(
                "quarantined",
                incoming.record_type,
                incoming.pubsub_message_id,
                quarantine_reason="unknown_vin",
            )

        owned_event = replace(
            event,
            vehicle_id=route.vehicle_id,
            tesla_vehicle_id=route.tesla_vehicle_id,
            telemetry_config_version=route.telemetry_config_version,
            telemetry_config_hash=route.telemetry_config_hash,
        )
        self._sink.append_user(route.dataset_id, owned_event)
        return ProcessingResult(
            "persisted",
            incoming.record_type,
            incoming.pubsub_message_id,
            vehicle_id=route.vehicle_id,
            user_id=route.user_id,
        )

    def _event(self, incoming: IncomingTelemetry, *, now: datetime) -> RawTelemetryEvent:
        return RawTelemetryEvent(
            source_timestamp=incoming.source_timestamp,
            ingested_at=incoming.edge_received_at,
            processed_at=now,
            vehicle_id=None,
            vin=incoming.vin,
            tesla_vehicle_id=None,
            record_type=incoming.record_type,
            payload=incoming.payload,
            telemetry_config_version=None,
            telemetry_config_hash=None,
            transport_message_id=incoming.transport_message_id,
            pubsub_message_id=incoming.pubsub_message_id,
            pubsub_publish_time=incoming.pubsub_publish_time,
            pubsub_delivery_attempt=incoming.pubsub_delivery_attempt,
            telemetry_client_version=incoming.telemetry_client_version,
            telemetry_receiver_version=self._receiver_version,
            receiver_record_version=incoming.receiver_record_version,
        )


def decode_pubsub_push(body: bytes, *, now: datetime) -> IncomingTelemetry:
    """Decode the authenticated Pub/Sub push wrapper and official receiver metadata."""
    try:
        wrapper = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise InvalidPushMessageError("invalid_json_wrapper") from exc
    if not isinstance(wrapper, dict) or not isinstance(wrapper.get("message"), dict):
        raise InvalidPushMessageError("missing_pubsub_message")
    message = wrapper["message"]
    message_id = _required_string(message, "messageId")
    encoded = _required_string(message, "data")
    try:
        decoded = base64.b64decode(encoded, validate=True)
        payload = json.loads(decoded)
    except (binascii.Error, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise InvalidPushMessageError("invalid_pubsub_data") from exc
    if not isinstance(payload, dict):
        raise InvalidPushMessageError("payload_must_be_object")

    raw_attributes = message.get("attributes", {})
    if not isinstance(raw_attributes, dict) or not all(
        isinstance(key, str) and isinstance(value, str) for key, value in raw_attributes.items()
    ):
        raise InvalidPushMessageError("invalid_pubsub_attributes")
    attributes: dict[str, str] = raw_attributes
    publish_time = _optional_timestamp(message.get("publishTime"))
    delivery_attempt = _optional_int(wrapper.get("deliveryAttempt"))
    synthetic_fixture_id = _synthetic_fixture_id(attributes)

    record_type = attributes.get("txtype", "synthetic" if synthetic_fixture_id else "unknown")
    vin_value = attributes.get("vin")
    vin = vin_value if vin_value else None
    validation_error: str | None = None
    if synthetic_fixture_id is None and record_type not in ALLOWED_RECORD_TYPES:
        validation_error = "invalid_record_type"

    payload_vin = payload.get("vin")
    if (
        synthetic_fixture_id is None
        and vin is not None
        and isinstance(payload_vin, str)
        and payload_vin
        and payload_vin != vin
    ):
        validation_error = "payload_vin_mismatch"

    source_timestamp = _optional_timestamp(payload.get("createdAt"))
    if source_timestamp is None:
        validation_error = validation_error or "missing_source_timestamp"
        source_timestamp = publish_time or now

    edge_received_at = _milliseconds_timestamp(attributes.get("receivedat"))
    if edge_received_at is None:
        if synthetic_fixture_id is None:
            validation_error = validation_error or "missing_edge_received_timestamp"
        edge_received_at = publish_time or now

    return IncomingTelemetry(
        source_timestamp=source_timestamp,
        edge_received_at=edge_received_at,
        payload=payload,
        vin=vin,
        record_type=record_type,
        transport_message_id=attributes.get("txid") or None,
        pubsub_message_id=message_id,
        pubsub_publish_time=publish_time,
        pubsub_delivery_attempt=delivery_attempt,
        telemetry_client_version=attributes.get("device_client_version") or None,
        receiver_record_version=_optional_int(attributes.get("version")),
        validation_error=validation_error,
        synthetic_fixture_id=synthetic_fixture_id,
        synthetic_fail_first=(attributes.get("tpp_fixture_fail_first") == "true"),
    )


def _required_string(value: dict[str, object], key: str) -> str:
    item = value.get(key)
    if not isinstance(item, str) or not item:
        raise InvalidPushMessageError(f"missing_{key}")
    return item


def _optional_timestamp(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(UTC)


def _milliseconds_timestamp(value: str | None) -> datetime | None:
    if value is None:
        return None
    try:
        return datetime.fromtimestamp(int(value) / 1000, tz=UTC)
    except (ValueError, OSError, OverflowError):
        return None


def _optional_int(value: object) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    if not isinstance(value, (int, str)):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _synthetic_fixture_id(attributes: dict[str, str]) -> str | None:
    if attributes.get("tpp_synthetic_fixture") != SYNTHETIC_MARKER:
        return None
    fixture_id = attributes.get("tpp_fixture_id", "")
    if not FIXTURE_ID.fullmatch(fixture_id):
        raise InvalidPushMessageError("invalid_synthetic_fixture_id")
    return fixture_id
