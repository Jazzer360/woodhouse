"""Shared append-only telemetry schema and transport models."""

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Final

RAW_TELEMETRY_TABLE: Final = "raw_telemetry_events"


@dataclass(frozen=True, slots=True)
class TelemetrySchemaField:
    """A Google-free description of one BigQuery telemetry column."""

    name: str
    field_type: str
    mode: str = "NULLABLE"
    description: str = ""


RAW_TELEMETRY_SCHEMA: Final = (
    TelemetrySchemaField(
        "source_timestamp", "TIMESTAMP", "REQUIRED", "Timestamp supplied by the vehicle."
    ),
    TelemetrySchemaField(
        "ingested_at",
        "TIMESTAMP",
        "REQUIRED",
        "Timestamp at which telemetry-edge accepted the record.",
    ),
    TelemetrySchemaField(
        "processed_at",
        "TIMESTAMP",
        "REQUIRED",
        "Timestamp at which telemetry-processor handled this delivery.",
    ),
    TelemetrySchemaField("vehicle_id", "STRING", "REQUIRED", "Opaque internal vehicle ID."),
    TelemetrySchemaField("vin", "STRING", "REQUIRED", "VIN retained for source provenance."),
    TelemetrySchemaField(
        "tesla_vehicle_id", "STRING", "NULLABLE", "Tesla account vehicle identifier."
    ),
    TelemetrySchemaField("record_type", "STRING", "REQUIRED", "Tesla record type."),
    TelemetrySchemaField("payload", "JSON", "REQUIRED", "Complete decoded Tesla payload."),
    TelemetrySchemaField(
        "telemetry_config_version",
        "STRING",
        "NULLABLE",
        "Server-recorded source configuration version when available.",
    ),
    TelemetrySchemaField(
        "telemetry_config_hash",
        "STRING",
        "NULLABLE",
        "Server-recorded source configuration hash when available.",
    ),
    TelemetrySchemaField(
        "transport_message_id", "STRING", "NULLABLE", "Tesla Fleet Telemetry transaction ID."
    ),
    TelemetrySchemaField("pubsub_message_id", "STRING", "REQUIRED", "Google Pub/Sub message ID."),
    TelemetrySchemaField(
        "pubsub_publish_time", "TIMESTAMP", "NULLABLE", "Google Pub/Sub publish timestamp."
    ),
    TelemetrySchemaField(
        "pubsub_delivery_attempt",
        "INTEGER",
        "NULLABLE",
        "Approximate Pub/Sub delivery attempt when supplied.",
    ),
    TelemetrySchemaField(
        "telemetry_client_version",
        "STRING",
        "NULLABLE",
        "Vehicle Fleet Telemetry client version.",
    ),
    TelemetrySchemaField(
        "telemetry_receiver_version",
        "STRING",
        "REQUIRED",
        "Pinned Tesla Fleet Telemetry receiver version.",
    ),
    TelemetrySchemaField(
        "receiver_record_version",
        "INTEGER",
        "NULLABLE",
        "Receiver transport record version when supplied.",
    ),
)


@dataclass(frozen=True, slots=True)
class RawTelemetryEvent:
    """One delivery to permanent raw telemetry history."""

    source_timestamp: datetime
    ingested_at: datetime
    processed_at: datetime
    vehicle_id: str | None
    vin: str | None
    tesla_vehicle_id: str | None
    record_type: str
    payload: dict[str, Any]
    telemetry_config_version: str | None
    telemetry_config_hash: str | None
    transport_message_id: str | None
    pubsub_message_id: str
    pubsub_publish_time: datetime | None
    pubsub_delivery_attempt: int | None
    telemetry_client_version: str | None
    telemetry_receiver_version: str
    receiver_record_version: int | None

    def bigquery_row(self) -> dict[str, object]:
        """Return the JSON-compatible row without an insert/deduplication ID."""
        return {
            "source_timestamp": self.source_timestamp.isoformat(),
            "ingested_at": self.ingested_at.isoformat(),
            "processed_at": self.processed_at.isoformat(),
            "vehicle_id": self.vehicle_id,
            "vin": self.vin,
            "tesla_vehicle_id": self.tesla_vehicle_id,
            "record_type": self.record_type,
            "payload": self.payload,
            "telemetry_config_version": self.telemetry_config_version,
            "telemetry_config_hash": self.telemetry_config_hash,
            "transport_message_id": self.transport_message_id,
            "pubsub_message_id": self.pubsub_message_id,
            "pubsub_publish_time": (
                self.pubsub_publish_time.isoformat() if self.pubsub_publish_time else None
            ),
            "pubsub_delivery_attempt": self.pubsub_delivery_attempt,
            "telemetry_client_version": self.telemetry_client_version,
            "telemetry_receiver_version": self.telemetry_receiver_version,
            "receiver_record_version": self.receiver_record_version,
        }
