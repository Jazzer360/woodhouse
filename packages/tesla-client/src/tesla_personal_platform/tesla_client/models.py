"""Typed models used only by the Phase 4 Tesla onboarding surface."""

from dataclasses import dataclass, field
from datetime import datetime


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
    """The minimal vehicle identity returned during onboarding."""

    vin: str
    tesla_vehicle_id: str
    display_name: str | None
    state: str | None


@dataclass(frozen=True, slots=True)
class FleetStatus:
    """Per-vehicle onboarding capabilities reported by fleet_status."""

    vin: str
    key_paired: bool | None
    vehicle_command_protocol_required: bool | None
    firmware_version: str | None
    fleet_telemetry_version: str | None
    total_number_of_keys: int | None
    raw: dict[str, object] = field(repr=False)
