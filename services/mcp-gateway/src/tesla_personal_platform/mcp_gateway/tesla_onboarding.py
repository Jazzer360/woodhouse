"""Server-side Tesla onboarding orchestration and tenant-safe models."""

import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from typing import Protocol
from urllib.parse import quote

from tesla_personal_platform.auth import CrossUserAccessError, UserContext
from tesla_personal_platform.tesla_client import (
    FleetStatus,
    TeslaReauthorizationRequired,
    TeslaRegion,
    TeslaVehicle,
    TokenSet,
)
from tesla_personal_platform.tesla_client.oauth import TeslaOAuthConfig

OAUTH_STATE_LIFETIME = timedelta(minutes=10)
ACCESS_TOKEN_REFRESH_SKEW = timedelta(minutes=1)


class TeslaOnboardingError(Exception):
    """A safe onboarding failure that does not expose credentials."""


class InvalidOAuthStateError(TeslaOnboardingError):
    """The OAuth state is missing, expired, reused, or otherwise invalid."""


class ConcurrentTokenRotationError(TeslaOnboardingError):
    """Another request already committed a newer rotating refresh token."""


class VehicleOwnershipConflictError(TeslaOnboardingError):
    """A VIN is already bound to a different internal platform user."""


@dataclass(frozen=True, slots=True)
class PendingAuthorization:
    """Short-lived server-side binding between OAuth state and platform user."""

    owner_user_id: str
    nonce: str
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class TeslaConnection:
    """One platform user's encrypted-at-rest Tesla connection state."""

    connection_id: str
    owner_user_id: str
    tokens: TokenSet
    token_version: int
    region: str | None
    base_url: str
    status: str


@dataclass(frozen=True, slots=True)
class VehicleRecord:
    """Trusted internal ownership and per-vehicle Virtual Key state."""

    vehicle_id: str
    owner_user_id: str
    connection_id: str
    vin: str
    tesla_vehicle_id: str
    display_name: str | None
    state: str | None
    authorization_status: str
    virtual_key_status: str
    command_protocol_required: bool | None
    firmware_version: str | None
    fleet_telemetry_version: str | None
    total_number_of_keys: int | None

    def public_document(self, app_domain: str) -> dict[str, object]:
        return {
            "vehicle_id": self.vehicle_id,
            "vin": self.vin,
            "display_name": self.display_name,
            "state": self.state,
            "authorization_status": self.authorization_status,
            "virtual_key_status": self.virtual_key_status,
            "virtual_key_pairing_url": virtual_key_pairing_url(app_domain, self.vin),
            "vehicle_command_protocol_required": self.command_protocol_required,
            "firmware_version": self.firmware_version,
            "fleet_telemetry_version": self.fleet_telemetry_version,
            "total_number_of_keys": self.total_number_of_keys,
        }


@dataclass(frozen=True, slots=True)
class OnboardingResult:
    """Safe callback result; credentials are intentionally absent."""

    owner_user_id: str
    connection_id: str
    region: str
    base_url: str
    vehicles: tuple[VehicleRecord, ...]


class TeslaOnboardingStore(Protocol):
    """Persistence contract with atomic state consumption and token rotation."""

    def create_oauth_state(self, state: str, pending: PendingAuthorization) -> None: ...

    def consume_oauth_state(self, state: str, *, now: datetime) -> PendingAuthorization: ...

    def save_connection(
        self, owner_user_id: str, tokens: TokenSet, base_url: str
    ) -> TeslaConnection: ...

    def complete_connection(
        self, owner_user_id: str, *, region: str, base_url: str
    ) -> TeslaConnection: ...

    def get_connection(self, owner_user_id: str) -> TeslaConnection: ...

    def rotate_tokens(
        self,
        owner_user_id: str,
        *,
        expected_version: int,
        tokens: TokenSet,
    ) -> TeslaConnection: ...

    def mark_reauthorization_required(self, owner_user_id: str) -> None: ...

    def sync_vehicles(
        self,
        owner_user_id: str,
        connection_id: str,
        vehicles: list[TeslaVehicle],
        statuses: dict[str, FleetStatus],
    ) -> list[VehicleRecord]: ...

    def list_vehicles(self, owner_user_id: str) -> list[VehicleRecord]: ...

    def get_vehicle(self, owner_user_id: str, vehicle_id: str) -> VehicleRecord: ...

    def update_fleet_status(
        self,
        owner_user_id: str,
        vehicle_id: str,
        status: FleetStatus,
    ) -> VehicleRecord: ...


class OAuthClient(Protocol):
    """Narrow OAuth dependency used by onboarding orchestration."""

    config: TeslaOAuthConfig

    def authorization_url(self, *, state: str, nonce: str) -> str: ...

    def exchange_code(self, code: str, *, nonce: str) -> TokenSet: ...

    def refresh(self, refresh_token: str, *, tesla_subject: str) -> TokenSet: ...


class FleetClient(Protocol):
    """Narrow Fleet API dependency used by onboarding orchestration."""

    def region(self, access_token: str, *, base_url: str) -> TeslaRegion: ...

    def list_vehicles(self, access_token: str, *, base_url: str) -> list[TeslaVehicle]: ...

    def fleet_status(
        self,
        access_token: str,
        *,
        base_url: str,
        vins: list[str],
    ) -> dict[str, FleetStatus]: ...


class TeslaOnboardingService:
    """Bind Tesla authorization and all discovered vehicles to one trusted user."""

    def __init__(
        self,
        oauth: OAuthClient,
        fleet: FleetClient,
        store: TeslaOnboardingStore,
        app_domain: str,
    ) -> None:
        self._oauth = oauth
        self._fleet = fleet
        self._store = store
        self._app_domain = app_domain.strip().lower()
        if not self._app_domain or "://" in self._app_domain or "/" in self._app_domain:
            raise ValueError("Tesla application domain must be a bare hostname")

    def start(self, context: UserContext, *, now: datetime | None = None) -> str:
        current = now or datetime.now(UTC)
        state = secrets.token_urlsafe(32)
        nonce = secrets.token_urlsafe(32)
        self._store.create_oauth_state(
            state,
            PendingAuthorization(context.user_id, nonce, current + OAUTH_STATE_LIFETIME),
        )
        return self._oauth.authorization_url(state=state, nonce=nonce)

    def callback(
        self,
        *,
        state: str,
        code: str,
        now: datetime | None = None,
    ) -> OnboardingResult:
        current = now or datetime.now(UTC)
        pending = self._store.consume_oauth_state(state, now=current)
        tokens = self._oauth.exchange_code(code, nonce=pending.nonce)
        connection = self._store.save_connection(
            pending.owner_user_id,
            tokens,
            self._oauth.config.audience,
        )
        region = self._fleet.region(tokens.access_token, base_url=connection.base_url)
        vehicles = self._fleet.list_vehicles(tokens.access_token, base_url=region.base_url)
        statuses = self._fleet.fleet_status(
            tokens.access_token,
            base_url=region.base_url,
            vins=[vehicle.vin for vehicle in vehicles],
        )
        records = self._store.sync_vehicles(
            pending.owner_user_id,
            connection.connection_id,
            vehicles,
            statuses,
        )
        connection = self._store.complete_connection(
            pending.owner_user_id,
            region=region.region,
            base_url=region.base_url,
        )
        return OnboardingResult(
            owner_user_id=pending.owner_user_id,
            connection_id=connection.connection_id,
            region=region.region,
            base_url=region.base_url,
            vehicles=tuple(records),
        )

    def decline(self, *, state: str, now: datetime | None = None) -> None:
        """Consume state after a Tesla denial so it cannot be replayed."""
        self._store.consume_oauth_state(state, now=now or datetime.now(UTC))

    def list_vehicles(self, context: UserContext) -> list[dict[str, object]]:
        return self.vehicle_documents(self._store.list_vehicles(context.user_id))

    def vehicle_documents(
        self, vehicles: tuple[VehicleRecord, ...] | list[VehicleRecord]
    ) -> list[dict[str, object]]:
        """Render safe per-vehicle onboarding state without credentials."""
        return [vehicle.public_document(self._app_domain) for vehicle in vehicles]

    def refresh_fleet_status(
        self,
        context: UserContext,
        vehicle_id: str,
        *,
        now: datetime | None = None,
    ) -> dict[str, object]:
        vehicle = self._store.get_vehicle(context.user_id, vehicle_id)
        token, connection = self._access_token(context.user_id, now=now)
        try:
            statuses = self._fleet.fleet_status(
                token,
                base_url=connection.base_url,
                vins=[vehicle.vin],
            )
        except TeslaReauthorizationRequired:
            self._store.mark_reauthorization_required(context.user_id)
            raise
        status = statuses.get(vehicle.vin)
        if status is None:
            raise TeslaOnboardingError("Tesla fleet_status omitted the selected vehicle")
        updated = self._store.update_fleet_status(
            context.user_id,
            vehicle.vehicle_id,
            status,
        )
        return updated.public_document(self._app_domain)

    def rotate_refresh_token(self, context: UserContext) -> dict[str, object]:
        """Deliberately exercise single-use refresh rotation without exposing tokens."""
        connection = self._store.get_connection(context.user_id)
        if connection.status == "reauthorization_required":
            raise TeslaReauthorizationRequired("Tesla authorization must be renewed")
        try:
            rotated = self._oauth.refresh(
                connection.tokens.refresh_token,
                tesla_subject=connection.tokens.tesla_subject,
            )
        except TeslaReauthorizationRequired:
            self._store.mark_reauthorization_required(context.user_id)
            raise
        try:
            connection = self._store.rotate_tokens(
                context.user_id,
                expected_version=connection.token_version,
                tokens=rotated,
            )
        except ConcurrentTokenRotationError:
            connection = self._store.get_connection(context.user_id)
        return {
            "status": connection.status,
            "token_version": connection.token_version,
            "access_token_expires_at": connection.tokens.expires_at.isoformat(),
        }

    def _access_token(
        self,
        owner_user_id: str,
        *,
        now: datetime | None = None,
    ) -> tuple[str, TeslaConnection]:
        current = now or datetime.now(UTC)
        connection = self._store.get_connection(owner_user_id)
        if connection.status == "reauthorization_required":
            raise TeslaReauthorizationRequired("Tesla authorization must be renewed")
        if connection.tokens.expires_at > current + ACCESS_TOKEN_REFRESH_SKEW:
            return connection.tokens.access_token, connection
        try:
            rotated = self._oauth.refresh(
                connection.tokens.refresh_token,
                tesla_subject=connection.tokens.tesla_subject,
            )
        except TeslaReauthorizationRequired:
            self._store.mark_reauthorization_required(owner_user_id)
            raise
        try:
            connection = self._store.rotate_tokens(
                owner_user_id,
                expected_version=connection.token_version,
                tokens=rotated,
            )
        except ConcurrentTokenRotationError:
            connection = self._store.get_connection(owner_user_id)
        return connection.tokens.access_token, connection


def virtual_key_pairing_url(app_domain: str, vin: str) -> str:
    """Return Tesla's user-in-the-loop pairing deep link for one vehicle."""
    return f"https://www.tesla.com/_ak/{app_domain}?vin={quote(vin, safe='')}"


def stable_vehicle_id(owner_user_id: str, vin: str) -> str:
    """Return an opaque stable ID without exposing VIN in the document key."""
    digest = sha256(f"{owner_user_id}\0{vin}".encode()).hexdigest()
    return f"veh_{digest[:32]}"


def virtual_key_status(status: FleetStatus | None) -> str:
    if status is None or status.key_paired is None:
        return "unknown"
    return "paired" if status.key_paired else "pending"


def require_vehicle_owner(record: VehicleRecord, owner_user_id: str) -> None:
    if record.owner_user_id != owner_user_id:
        raise CrossUserAccessError("Vehicle is outside the authenticated user boundary")
    if record.authorization_status != "active":
        raise TeslaOnboardingError("Vehicle is no longer returned by the Tesla connection")
