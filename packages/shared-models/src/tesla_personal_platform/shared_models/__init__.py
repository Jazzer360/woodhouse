"""Cross-service models shared by trusted platform components."""

from tesla_personal_platform.shared_models.telemetry import (
    RAW_TELEMETRY_SCHEMA,
    RAW_TELEMETRY_TABLE,
    RawTelemetryEvent,
    TelemetrySchemaField,
)

COMPONENT = "shared-models"

__all__ = [
    "COMPONENT",
    "RAW_TELEMETRY_SCHEMA",
    "RAW_TELEMETRY_TABLE",
    "RawTelemetryEvent",
    "TelemetrySchemaField",
]
