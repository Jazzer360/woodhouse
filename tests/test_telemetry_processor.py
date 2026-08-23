"""Permanent raw telemetry routing and retention tests."""

import base64
import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from typing import Any

import pytest
from tesla_personal_platform.shared_models import RawTelemetryEvent
from tesla_personal_platform.telemetry_processor.gcp import (
    BigQueryTelemetrySink,
    FirestoreVehicleRegistry,
)
from tesla_personal_platform.telemetry_processor.processor import (
    IncomingTelemetry,
    RetryableProcessingError,
    TelemetryProcessor,
    VehicleRoute,
    decode_pubsub_push,
)

NOW = datetime(2026, 8, 23, 12, tzinfo=UTC)


class FakeRegistry:
    def __init__(self, routes: dict[str, VehicleRoute]) -> None:
        self.routes = routes

    def resolve(self, vin: str) -> VehicleRoute | None:
        return self.routes.get(vin)


class RecordingSink:
    def __init__(self) -> None:
        self.user_rows: list[tuple[str, RawTelemetryEvent]] = []
        self.quarantine_rows: list[tuple[RawTelemetryEvent, str]] = []
        self.synthetic_rows: list[tuple[RawTelemetryEvent, str, bool]] = []
        self.fail_user_appends = 0

    def append_user(self, dataset_id: str, event: RawTelemetryEvent) -> None:
        if self.fail_user_appends:
            self.fail_user_appends -= 1
            raise RetryableProcessingError("transient")
        self.user_rows.append((dataset_id, event))

    def append_quarantine(self, event: RawTelemetryEvent, reason: str) -> None:
        self.quarantine_rows.append((event, reason))

    def append_synthetic(
        self,
        event: RawTelemetryEvent,
        fixture_id: str,
        *,
        first_failure_recorded: bool,
    ) -> None:
        self.synthetic_rows.append((event, fixture_id, first_failure_recorded))


class FakeRetryGate:
    def __init__(self) -> None:
        self.seen: set[str] = set()

    def should_fail_first(self, fixture_id: str, *, now: datetime) -> bool:
        del now
        if fixture_id in self.seen:
            return False
        self.seen.add(fixture_id)
        return True


def incoming(vin: str = "VIN-A", message_id: str = "message-1") -> IncomingTelemetry:
    return IncomingTelemetry(
        source_timestamp=NOW - timedelta(days=7),
        edge_received_at=NOW - timedelta(seconds=2),
        payload={"createdAt": (NOW - timedelta(days=7)).isoformat(), "vin": vin, "data": []},
        vin=vin,
        record_type="V",
        transport_message_id="tesla-transaction",
        pubsub_message_id=message_id,
        pubsub_publish_time=NOW - timedelta(seconds=1),
        pubsub_delivery_attempt=1,
        telemetry_client_version="1.3.0",
        receiver_record_version=2,
        validation_error=None,
        synthetic_fixture_id=None,
        synthetic_fail_first=False,
    )


def processor(routes: dict[str, VehicleRoute], sink: RecordingSink) -> TelemetryProcessor:
    return TelemetryProcessor(
        FakeRegistry(routes), sink, FakeRetryGate(), receiver_version="v0.9.4"
    )


def test_multiple_users_and_vehicles_route_only_to_server_derived_datasets() -> None:
    routes = {
        "VIN-A": VehicleRoute("user-a", "tesla_u_a", "vehicle-a", "tesla-a"),
        "VIN-B": VehicleRoute("user-b", "tesla_u_b", "vehicle-b", "tesla-b"),
        "VIN-C": VehicleRoute("user-a", "tesla_u_a", "vehicle-c", "tesla-c"),
    }
    sink = RecordingSink()
    service = processor(routes, sink)

    results = [service.process(incoming(vin, f"message-{vin}"), now=NOW) for vin in routes]

    assert [dataset for dataset, _ in sink.user_rows] == [
        "tesla_u_a",
        "tesla_u_b",
        "tesla_u_a",
    ]
    assert [(result.user_id, result.vehicle_id) for result in results] == [
        ("user-a", "vehicle-a"),
        ("user-b", "vehicle-b"),
        ("user-a", "vehicle-c"),
    ]


def test_buffered_source_timestamp_is_preserved_independently_of_ingestion_time() -> None:
    sink = RecordingSink()
    service = processor({"VIN-A": VehicleRoute("user-a", "tesla_u_a", "vehicle-a", None)}, sink)

    service.process(incoming(), now=NOW)

    event = sink.user_rows[0][1]
    assert event.source_timestamp == NOW - timedelta(days=7)
    assert event.ingested_at == NOW - timedelta(seconds=2)
    assert event.processed_at == NOW


def test_duplicate_and_unchanged_deliveries_are_all_preserved() -> None:
    sink = RecordingSink()
    service = processor({"VIN-A": VehicleRoute("user-a", "tesla_u_a", "vehicle-a", None)}, sink)
    delivery = incoming()

    for _ in range(3):
        service.process(delivery, now=NOW)

    assert len(sink.user_rows) == 3
    assert [row.pubsub_message_id for _, row in sink.user_rows] == ["message-1"] * 3


def test_unknown_vin_is_quarantined_and_never_written_to_a_user_dataset() -> None:
    sink = RecordingSink()

    result = processor({}, sink).process(incoming("SYNTHETIC-UNKNOWN"), now=NOW)

    assert result.disposition == "quarantined"
    assert result.quarantine_reason == "unknown_vin"
    assert not sink.user_rows
    assert sink.quarantine_rows[0][1] == "unknown_vin"


def test_transient_persistence_failure_is_retried_without_acknowledging_early() -> None:
    sink = RecordingSink()
    sink.fail_user_appends = 1
    service = processor({"VIN-A": VehicleRoute("user-a", "tesla_u_a", "vehicle-a", None)}, sink)

    with pytest.raises(RetryableProcessingError, match="transient"):
        service.process(incoming(), now=NOW)
    assert not sink.user_rows

    service.process(incoming(), now=NOW)
    assert len(sink.user_rows) == 1


def test_invalid_vehicle_payload_is_quarantined_without_registry_routing() -> None:
    sink = RecordingSink()
    malformed = replace(incoming(), validation_error="payload_vin_mismatch")

    result = processor({}, sink).process(malformed, now=NOW)

    assert result.quarantine_reason == "payload_vin_mismatch"
    assert not sink.user_rows


def test_synthetic_fixture_is_isolated_and_can_prove_one_redelivery() -> None:
    sink = RecordingSink()
    service = processor({}, sink)
    fixture = replace(
        incoming("SYNTHETIC-NON-VEHICLE"),
        record_type="synthetic",
        synthetic_fixture_id="phase7_retry-proof",
        synthetic_fail_first=True,
    )

    with pytest.raises(RetryableProcessingError, match="synthetic_first_delivery_failure"):
        service.process(fixture, now=NOW)
    service.process(fixture, now=NOW)

    assert not sink.user_rows
    assert not sink.quarantine_rows
    assert sink.synthetic_rows[0][1] == "phase7_retry-proof"
    assert sink.synthetic_rows[0][2] is True


def push_body(
    payload: dict[str, Any], attributes: dict[str, str], *, message_id: str = "pubsub-1"
) -> bytes:
    return json.dumps(
        {
            "message": {
                "messageId": message_id,
                "publishTime": "2026-08-23T12:00:01Z",
                "attributes": attributes,
                "data": base64.b64encode(json.dumps(payload).encode()).decode(),
            },
            "deliveryAttempt": 2,
        }
    ).encode()


def test_official_receiver_payload_and_transport_metadata_are_preserved() -> None:
    body = push_body(
        {"createdAt": "2026-08-20T01:02:03Z", "vin": "VIN-A", "data": []},
        {
            "vin": "VIN-A",
            "receivedat": "1787486400000",
            "txid": "tx-1",
            "txtype": "V",
            "version": "2",
            "device_client_version": "1.3.0",
        },
    )

    decoded = decode_pubsub_push(body, now=NOW)

    assert decoded.source_timestamp == datetime(2026, 8, 20, 1, 2, 3, tzinfo=UTC)
    assert decoded.edge_received_at == datetime.fromtimestamp(1787486400, tz=UTC)
    assert decoded.transport_message_id == "tx-1"
    assert decoded.pubsub_message_id == "pubsub-1"
    assert decoded.pubsub_delivery_attempt == 2
    assert decoded.telemetry_client_version == "1.3.0"
    assert decoded.receiver_record_version == 2


class FakeBigQueryClient:
    def __init__(self, errors: list[dict[str, object]] | None = None) -> None:
        self.calls: list[tuple[str, list[dict[str, object]], list[None], int]] = []
        self.errors = errors or []

    def insert_rows_json(
        self,
        table: str,
        rows: list[dict[str, object]],
        *,
        row_ids: list[None],
        timeout: int,
    ) -> list[dict[str, object]]:
        self.calls.append((table, rows, row_ids, timeout))
        return self.errors


def test_bigquery_sink_explicitly_disables_insert_id_deduplication() -> None:
    client = FakeBigQueryClient()
    event = TelemetryProcessor(
        FakeRegistry({}), RecordingSink(), FakeRetryGate(), receiver_version="v0.9.4"
    )._event(incoming(), now=NOW)
    sink = BigQueryTelemetrySink(
        client,  # type: ignore[arg-type]
        "project",
        "project.system.quarantine",
        "project.system.synthetic",
    )

    sink.append_user("tesla_u_a", replace(event, vehicle_id="vehicle-a"))

    assert client.calls[0][0] == "project.tesla_u_a.raw_telemetry_events"
    assert client.calls[0][2] == [None]
    assert json.loads(str(client.calls[0][1][0]["payload"])) == event.payload


def test_bigquery_sink_reports_only_safe_rejection_codes() -> None:
    client = FakeBigQueryClient(
        [
            {
                "index": 0,
                "errors": [
                    {
                        "reason": "invalid",
                        "location": "payload",
                        "message": "must never be logged: sensitive value",
                    }
                ],
            }
        ]
    )
    event = TelemetryProcessor(
        FakeRegistry({}), RecordingSink(), FakeRetryGate(), receiver_version="v0.9.4"
    )._event(incoming(), now=NOW)
    sink = BigQueryTelemetrySink(
        client,  # type: ignore[arg-type]
        "project",
        "project.system.quarantine",
        "project.system.synthetic",
    )

    with pytest.raises(RetryableProcessingError) as captured:
        sink.append_synthetic(event, "phase7_fixture", first_failure_recorded=False)

    assert str(captured.value) == "bigquery_append_rejected:invalid@payload"
    assert "sensitive value" not in str(captured.value)


class FakeSnapshot:
    def __init__(self, data: dict[str, object] | None) -> None:
        self._data = data
        self.exists = data is not None

    def to_dict(self) -> dict[str, object] | None:
        return self._data


class FakeQuery:
    def __init__(self, users: list[dict[str, object]], owner_user_id: str | None = None) -> None:
        self._users = users
        self._owner_user_id = owner_user_id

    def where(self, *, filter: object) -> "FakeQuery":
        owner_user_id = getattr(filter, "value", None)
        return FakeQuery(self._users, owner_user_id)

    def limit(self, value: int) -> "FakeQuery":
        del value
        return self

    def stream(self) -> list[FakeSnapshot]:
        return [
            FakeSnapshot(user) for user in self._users if user.get("user_id") == self._owner_user_id
        ]

    def document(self, identifier: str) -> "FakeDocument":
        raise AssertionError(identifier)


class FakeDocument:
    def __init__(self, data: dict[str, object] | None) -> None:
        self._data = data

    def get(self) -> FakeSnapshot:
        return FakeSnapshot(self._data)


class FakeCollection(FakeQuery):
    def __init__(
        self,
        documents: dict[str, dict[str, object]],
        users: list[dict[str, object]] | None = None,
    ) -> None:
        super().__init__(users or [])
        self._documents = documents

    def document(self, identifier: str) -> FakeDocument:
        return FakeDocument(self._documents.get(identifier))


class FakeFirestore:
    def __init__(self, collections: dict[str, FakeCollection]) -> None:
        self._collections = collections

    def collection(self, name: str) -> FakeCollection:
        return self._collections[name]


def firestore_registry(user_status: str, dataset_id: str) -> FirestoreVehicleRegistry:
    vin = "VIN-A"
    client = FakeFirestore(
        {
            "vehicle_vin_index": FakeCollection(
                {
                    sha256(vin.encode()).hexdigest(): {
                        "vehicle_id": "vehicle-a",
                        "owner_user_id": "user-a",
                    }
                }
            ),
            "vehicles": FakeCollection(
                {
                    "vehicle-a": {
                        "vin": vin,
                        "owner_user_id": "user-a",
                        "tesla_vehicle_id": "tesla-a",
                    }
                }
            ),
            "allowed_users": FakeCollection(
                {},
                [
                    {
                        "user_id": "user-a",
                        "status": user_status,
                        "dataset_id": dataset_id,
                    }
                ],
            ),
        }
    )
    return FirestoreVehicleRegistry(client)  # type: ignore[arg-type]


def test_firestore_registry_requires_active_user_and_opaque_dataset() -> None:
    assert firestore_registry("active", "tesla_u_a").resolve("VIN-A") is not None
    assert firestore_registry("disabled", "tesla_u_a").resolve("VIN-A") is None
    assert firestore_registry("active", "caller_supplied_dataset").resolve("VIN-A") is None
