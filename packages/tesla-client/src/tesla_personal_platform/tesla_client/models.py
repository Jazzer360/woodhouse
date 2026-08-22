"""Typed, secret-safe models for Tesla OAuth and Fleet API responses."""

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime

type JsonScalar = str | int | float | bool | None
type JsonValue = JsonScalar | list[JsonValue] | dict[str, JsonValue]
type JsonObject = dict[str, JsonValue]


@dataclass(frozen=True, slots=True)
class TokenSet:
    """A rotating Tesla credential set; token values are excluded from repr."""

    access_token: str = field(repr=False)
    refresh_token: str = field(repr=False)
    expires_at: datetime
    scopes: tuple[str, ...]
    tesla_subject: str


@dataclass(frozen=True, slots=True)
class TeslaRegion:
    """Tesla's authoritative region and Fleet API base URL for a user."""

    region: str
    base_url: str


@dataclass(frozen=True, slots=True)
class TeslaVehicle:
    """Vehicle identity returned by list and vehicle endpoints."""

    vin: str = field(repr=False)
    tesla_vehicle_id: str
    display_name: str | None
    state: str | None
    raw: JsonObject = field(default_factory=dict, repr=False)


@dataclass(frozen=True, slots=True)
class FleetStatus:
    """Per-vehicle capabilities reported by fleet_status."""

    vin: str = field(repr=False)
    key_paired: bool | None
    vehicle_command_protocol_required: bool | None
    firmware_version: str | None
    fleet_telemetry_version: str | None
    total_number_of_keys: int | None
    raw: JsonObject = field(repr=False)


@dataclass(frozen=True, slots=True)
class Pagination:
    """Normalized Tesla pagination metadata."""

    current: int | None = None
    per_page: int | None = None
    count: int | None = None
    pages: int | None = None
    next_page: int | None = None
    previous_page: int | None = None


@dataclass(frozen=True, slots=True)
class ObjectResponse:
    """A typed boundary for an endpoint returning a JSON object."""

    data: JsonObject = field(repr=False)


@dataclass(frozen=True, slots=True)
class ValueResponse:
    """A typed boundary for an endpoint returning any JSON value."""

    value: JsonValue = field(repr=False)


@dataclass(frozen=True, slots=True)
class ListResponse:
    """A typed boundary for an endpoint returning a list of JSON objects."""

    items: tuple[JsonObject, ...] = field(repr=False)
    pagination: Pagination | None = None


@dataclass(frozen=True, slots=True)
class CommandResult:
    """Normalized response shared by Tesla vehicle commands."""

    successful: bool
    reason: str | None = None
    raw: JsonObject = field(default_factory=dict, repr=False)


@dataclass(frozen=True, slots=True)
class BinaryDocument:
    """Binary Tesla resource such as a charging invoice."""

    content: bytes = field(repr=False)
    content_type: str


@dataclass(frozen=True, slots=True)
class VehicleData:
    """Targeted live vehicle-data sections returned by Tesla."""

    vehicle: TeslaVehicle
    sections: JsonObject = field(repr=False)


def json_object(value: Mapping[str, object]) -> JsonObject:
    """Copy a decoded JSON mapping into the recursive public JSON type."""

    return {str(key): _json_value(item) for key, item in value.items()}


def _json_value(value: object) -> JsonValue:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Mapping):
        return json_object({str(key): item for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    return str(value)
