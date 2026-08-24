"""Firestore persistence for Tesla OAuth state, tokens, and vehicle ownership."""

import secrets
from collections.abc import Mapping
from datetime import UTC, datetime
from hashlib import sha256
from typing import Any, cast

from google.cloud import firestore
from google.cloud.firestore_v1 import Client
from google.cloud.firestore_v1.base_query import FieldFilter
from google.cloud.firestore_v1.transaction import Transaction
from tesla_personal_platform.auth import CrossUserAccessError
from tesla_personal_platform.mcp_gateway.telemetry_control import TelemetryConfigurationState
from tesla_personal_platform.mcp_gateway.tesla_onboarding import (
    ConcurrentTokenRotationError,
    InvalidOAuthStateError,
    PendingAuthorization,
    TeslaConnection,
    TeslaOnboardingError,
    VehicleOwnershipConflictError,
    VehicleRecord,
    require_vehicle_owner,
    stable_vehicle_id,
    virtual_key_status,
)
from tesla_personal_platform.mcp_gateway.token_crypto import TokenCipher
from tesla_personal_platform.tesla_client import FleetStatus, TeslaVehicle, TokenSet
from tesla_personal_platform.tesla_client.models import JsonObject

OAUTH_STATES = "tesla_oauth_states"
CONNECTIONS = "tesla_connections"
VEHICLES = "vehicles"
VIN_INDEX = "vehicle_vin_index"
COMMAND_AUDITS = "tesla_command_audits"
TELEMETRY_CONFIG_AUDITS = "tesla_telemetry_config_audits"


class FirestoreTeslaOnboardingStore:
    """Persist sensitive Tesla state with one-time state and optimistic rotation."""

    def __init__(self, client: Client, cipher: TokenCipher) -> None:
        self.client = client
        self._cipher = cipher

    def create_oauth_state(self, state: str, pending: PendingAuthorization) -> None:
        self._state(state).create(
            {
                "owner_user_id": pending.owner_user_id,
                "nonce": pending.nonce,
                "expires_at": pending.expires_at,
                "completion_mode": pending.completion_mode,
                "browser_session_binding": pending.browser_session_binding,
                "created_at": firestore.SERVER_TIMESTAMP,
            }
        )

    def consume_oauth_state(self, state: str, *, now: datetime) -> PendingAuthorization:
        transaction = self.client.transaction()
        return cast(PendingAuthorization, _consume_state(transaction, self, state, now))

    def save_connection(
        self,
        owner_user_id: str,
        tokens: TokenSet,
        base_url: str,
    ) -> TeslaConnection:
        encrypted_tokens = self._cipher.encrypt(tokens, owner_user_id=owner_user_id)
        transaction = self.client.transaction()
        return cast(
            TeslaConnection,
            _save_connection(
                transaction,
                self,
                owner_user_id,
                tokens,
                encrypted_tokens,
                base_url,
            ),
        )

    def complete_connection(
        self,
        owner_user_id: str,
        *,
        region: str,
        base_url: str,
    ) -> TeslaConnection:
        reference = self._connection(owner_user_id)
        reference.update(
            {
                "region": region,
                "base_url": base_url,
                "status": "connected",
                "updated_at": firestore.SERVER_TIMESTAMP,
            }
        )
        return self.get_connection(owner_user_id)

    def get_connection(self, owner_user_id: str) -> TeslaConnection:
        snapshot = self._connection(owner_user_id).get()
        if not snapshot.exists:
            raise TeslaOnboardingError("Tesla connection is not configured")
        return self._connection_from_data(owner_user_id, snapshot.to_dict())

    def rotate_tokens(
        self,
        owner_user_id: str,
        *,
        expected_version: int,
        tokens: TokenSet,
    ) -> TeslaConnection:
        encrypted_tokens = self._cipher.encrypt(tokens, owner_user_id=owner_user_id)
        transaction = self.client.transaction()
        return cast(
            TeslaConnection,
            _rotate_tokens(
                transaction,
                self,
                owner_user_id,
                expected_version,
                tokens,
                encrypted_tokens,
            ),
        )

    def mark_reauthorization_required(self, owner_user_id: str) -> None:
        self._connection(owner_user_id).update(
            {
                "status": "reauthorization_required",
                "updated_at": firestore.SERVER_TIMESTAMP,
            }
        )

    def sync_vehicles(
        self,
        owner_user_id: str,
        connection_id: str,
        vehicles: list[TeslaVehicle],
        statuses: dict[str, FleetStatus],
    ) -> list[VehicleRecord]:
        transaction = self.client.transaction()
        return cast(
            list[VehicleRecord],
            _sync_vehicles(
                transaction,
                self,
                owner_user_id,
                connection_id,
                vehicles,
                statuses,
            ),
        )

    def list_vehicles(self, owner_user_id: str) -> list[VehicleRecord]:
        query = self.client.collection(VEHICLES).where(
            filter=FieldFilter("owner_user_id", "==", owner_user_id)
        )
        return [
            self._vehicle_from_data(snapshot.id, snapshot.to_dict()) for snapshot in query.stream()
        ]

    def get_vehicle(self, owner_user_id: str, vehicle_id: str) -> VehicleRecord:
        snapshot = self.client.collection(VEHICLES).document(vehicle_id).get()
        if not snapshot.exists:
            raise TeslaOnboardingError("Vehicle does not exist")
        record = self._vehicle_from_data(snapshot.id, snapshot.to_dict())
        require_vehicle_owner(record, owner_user_id)
        return record

    def update_fleet_status(
        self,
        owner_user_id: str,
        vehicle_id: str,
        status: FleetStatus,
    ) -> VehicleRecord:
        transaction = self.client.transaction()
        return cast(
            VehicleRecord,
            _update_fleet_status(
                transaction,
                self,
                owner_user_id,
                vehicle_id,
                status,
            ),
        )

    def get_telemetry_configuration(
        self, owner_user_id: str, vehicle_id: str
    ) -> TelemetryConfigurationState:
        snapshot = self.client.collection(VEHICLES).document(vehicle_id).get()
        if not snapshot.exists:
            raise TeslaOnboardingError("Vehicle does not exist")
        data = snapshot.to_dict()
        record = self._vehicle_from_data(vehicle_id, data)
        require_vehicle_owner(record, owner_user_id)
        values = data or {}
        return TelemetryConfigurationState(
            vehicle_id=vehicle_id,
            profile_version=_optional_string(values, "telemetry_config_version"),
            config_hash=_optional_string(values, "telemetry_config_hash"),
            field_config_hash=_optional_string(values, "telemetry_field_config_hash"),
            trust_profile_id=_optional_string(values, "telemetry_trust_profile_id"),
            trust_profile_hash=_optional_string(values, "telemetry_trust_profile_hash"),
            status=str(values.get("telemetry_config_status", "not_configured")),
            transport_maintenance_opt_in=bool(
                values.get("telemetry_transport_maintenance_opt_in", False)
            ),
        )

    def save_telemetry_configuration(
        self,
        *,
        owner_user_id: str,
        vehicle_id: str,
        state: TelemetryConfigurationState,
    ) -> None:
        reference = self.client.collection(VEHICLES).document(vehicle_id)
        snapshot = reference.get()
        if not snapshot.exists:
            raise TeslaOnboardingError("Vehicle does not exist")
        require_vehicle_owner(
            self._vehicle_from_data(vehicle_id, snapshot.to_dict()), owner_user_id
        )
        reference.update(
            {
                "telemetry_config_version": state.profile_version,
                "telemetry_config_hash": state.config_hash,
                "telemetry_field_config_hash": state.field_config_hash,
                "telemetry_trust_profile_id": state.trust_profile_id,
                "telemetry_trust_profile_hash": state.trust_profile_hash,
                "telemetry_config_status": state.status,
                "telemetry_transport_maintenance_opt_in": (state.transport_maintenance_opt_in),
                "telemetry_config_updated_at": firestore.SERVER_TIMESTAMP,
                "updated_at": firestore.SERVER_TIMESTAMP,
            }
        )

    def begin_telemetry_config_audit(
        self,
        *,
        audit_id: str,
        timestamp: datetime,
        owner_user_id: str,
        vehicle_id: str,
        operation: str,
        desired_config_hash: str | None,
        source: str,
    ) -> None:
        self.client.collection(TELEMETRY_CONFIG_AUDITS).document(audit_id).create(
            {
                "timestamp": timestamp,
                "owner_user_id": owner_user_id,
                "vehicle_id": vehicle_id,
                "operation": operation,
                "desired_config_hash": desired_config_hash,
                "result": "attempted",
                "error_category": None,
                "source": source,
                "created_at": firestore.SERVER_TIMESTAMP,
            }
        )

    def complete_telemetry_config_audit(
        self,
        *,
        audit_id: str,
        result: str,
        error_category: str | None,
    ) -> None:
        self.client.collection(TELEMETRY_CONFIG_AUDITS).document(audit_id).update(
            {
                "result": result,
                "error_category": error_category,
                "completed_at": firestore.SERVER_TIMESTAMP,
            }
        )

    def begin_command_audit(
        self,
        *,
        audit_id: str,
        timestamp: datetime,
        owner_user_id: str,
        vehicle_id: str,
        tool_name: str,
        redacted_parameters: JsonObject,
        correlation_id: str,
        source: str,
    ) -> None:
        """Persist a pending record before a write reaches Tesla."""
        self.client.collection(COMMAND_AUDITS).document(audit_id).create(
            {
                "timestamp": timestamp,
                "owner_user_id": owner_user_id,
                "vehicle_id": vehicle_id,
                "tool_name": tool_name,
                "parameters": redacted_parameters,
                "result": "attempted",
                "error_category": None,
                "correlation_id": correlation_id,
                "source": source,
                "created_at": firestore.SERVER_TIMESTAMP,
            }
        )

    def complete_command_audit(
        self,
        *,
        audit_id: str,
        result: str,
        error_category: str | None,
    ) -> None:
        """Finalize a pre-existing write attempt without storing response bodies."""
        self.client.collection(COMMAND_AUDITS).document(audit_id).update(
            {
                "result": result,
                "error_category": error_category,
                "completed_at": firestore.SERVER_TIMESTAMP,
            }
        )

    def _state(self, state: str):  # type: ignore[no-untyped-def]
        identifier = sha256(state.encode("utf-8")).hexdigest()
        return self.client.collection(OAUTH_STATES).document(identifier)

    def _connection(self, owner_user_id: str):  # type: ignore[no-untyped-def]
        return self.client.collection(CONNECTIONS).document(owner_user_id)

    def _vin_index(self, vin: str):  # type: ignore[no-untyped-def]
        identifier = sha256(vin.encode("utf-8")).hexdigest()
        return self.client.collection(VIN_INDEX).document(identifier)

    def _connection_from_data(
        self,
        owner_user_id: str,
        data: Mapping[str, Any] | None,
    ) -> TeslaConnection:
        if data is None or data.get("owner_user_id") != owner_user_id:
            raise TeslaOnboardingError("Tesla connection ownership is inconsistent")
        try:
            encrypted_tokens = str(data["encrypted_tokens"])
            return TeslaConnection(
                connection_id=str(data["connection_id"]),
                owner_user_id=owner_user_id,
                tokens=self._cipher.decrypt(encrypted_tokens, owner_user_id=owner_user_id),
                token_version=int(data["token_version"]),
                region=str(data["region"]) if data.get("region") is not None else None,
                base_url=str(data["base_url"]),
                status=str(data["status"]),
            )
        except (KeyError, TypeError, ValueError) as error:
            raise TeslaOnboardingError("Tesla connection state is invalid") from error

    @staticmethod
    def _vehicle_from_data(
        vehicle_id: str,
        data: Mapping[str, Any] | None,
    ) -> VehicleRecord:
        if data is None:
            raise TeslaOnboardingError("Vehicle registry record is empty")
        try:
            return VehicleRecord(
                vehicle_id=vehicle_id,
                owner_user_id=str(data["owner_user_id"]),
                connection_id=str(data["connection_id"]),
                vin=str(data["vin"]),
                tesla_vehicle_id=str(data["tesla_vehicle_id"]),
                display_name=_optional_string(data, "display_name"),
                state=_optional_string(data, "state"),
                authorization_status=str(data.get("authorization_status", "active")),
                virtual_key_status=str(data["virtual_key_status"]),
                command_protocol_required=_optional_bool(data, "vehicle_command_protocol_required"),
                firmware_version=_optional_string(data, "firmware_version"),
                fleet_telemetry_version=_optional_string(data, "fleet_telemetry_version"),
                total_number_of_keys=_optional_int(data, "total_number_of_keys"),
            )
        except (KeyError, TypeError, ValueError) as error:
            raise TeslaOnboardingError("Vehicle registry record is invalid") from error


@firestore.transactional
def _consume_state(
    transaction: Transaction,
    store: FirestoreTeslaOnboardingStore,
    state: str,
    now: datetime,
) -> PendingAuthorization:
    if not state:
        raise InvalidOAuthStateError("Tesla OAuth state is missing")
    reference = store._state(state)
    snapshot = reference.get(transaction=transaction)
    if not snapshot.exists:
        raise InvalidOAuthStateError("Tesla OAuth state is invalid or already used")
    data = snapshot.to_dict()
    transaction.delete(reference)
    if data is None:
        raise InvalidOAuthStateError("Tesla OAuth state is invalid")
    owner = data.get("owner_user_id")
    nonce = data.get("nonce")
    expires_at = data.get("expires_at")
    completion_mode = data.get("completion_mode", "api")
    browser_session_binding = data.get("browser_session_binding")
    if (
        not isinstance(owner, str)
        or not isinstance(nonce, str)
        or not isinstance(expires_at, datetime)
        or not isinstance(completion_mode, str)
        or completion_mode not in {"api", "browser"}
        or (completion_mode == "browser" and not isinstance(browser_session_binding, str))
        or (completion_mode == "api" and browser_session_binding is not None)
    ):
        raise InvalidOAuthStateError("Tesla OAuth state is invalid")
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    if expires_at <= now:
        raise InvalidOAuthStateError("Tesla OAuth state has expired")
    return PendingAuthorization(
        owner,
        nonce,
        expires_at,
        completion_mode,
        browser_session_binding,
    )


@firestore.transactional
def _save_connection(
    transaction: Transaction,
    store: FirestoreTeslaOnboardingStore,
    owner_user_id: str,
    tokens: TokenSet,
    encrypted_tokens: str,
    base_url: str,
) -> TeslaConnection:
    reference = store._connection(owner_user_id)
    snapshot = reference.get(transaction=transaction)
    existing = snapshot.to_dict() if snapshot.exists else None
    connection_id = (
        str(existing["connection_id"])
        if existing is not None and existing.get("connection_id")
        else f"conn_{secrets.token_hex(16)}"
    )
    version = int(existing.get("token_version", 0)) + 1 if existing else 1
    transaction.set(
        reference,
        {
            "connection_id": connection_id,
            "owner_user_id": owner_user_id,
            "encrypted_tokens": encrypted_tokens,
            "token_version": version,
            "access_token_expires_at": tokens.expires_at,
            "granted_scopes": list(tokens.scopes),
            "tesla_subject": tokens.tesla_subject,
            "base_url": base_url,
            "region": None,
            "status": "discovery_pending",
            "updated_at": firestore.SERVER_TIMESTAMP,
            "created_at": existing.get("created_at", firestore.SERVER_TIMESTAMP)
            if existing
            else firestore.SERVER_TIMESTAMP,
        },
    )
    return TeslaConnection(
        connection_id,
        owner_user_id,
        tokens,
        version,
        None,
        base_url,
        "discovery_pending",
    )


@firestore.transactional
def _rotate_tokens(
    transaction: Transaction,
    store: FirestoreTeslaOnboardingStore,
    owner_user_id: str,
    expected_version: int,
    tokens: TokenSet,
    encrypted_tokens: str,
) -> TeslaConnection:
    reference = store._connection(owner_user_id)
    snapshot = reference.get(transaction=transaction)
    if not snapshot.exists:
        raise TeslaOnboardingError("Tesla connection is not configured")
    data = snapshot.to_dict()
    if data is None or int(data.get("token_version", 0)) != expected_version:
        raise ConcurrentTokenRotationError("Tesla token was already rotated")
    if data.get("owner_user_id") != owner_user_id:
        raise TeslaOnboardingError("Tesla connection ownership is inconsistent")
    new_version = expected_version + 1
    transaction.update(
        reference,
        {
            "encrypted_tokens": encrypted_tokens,
            "token_version": new_version,
            "access_token_expires_at": tokens.expires_at,
            "granted_scopes": list(tokens.scopes),
            "status": "connected",
            "updated_at": firestore.SERVER_TIMESTAMP,
        },
    )
    updated = dict(data)
    updated.update(
        {
            "encrypted_tokens": encrypted_tokens,
            "token_version": new_version,
            "status": "connected",
        }
    )
    return store._connection_from_data(owner_user_id, updated)


@firestore.transactional
def _sync_vehicles(
    transaction: Transaction,
    store: FirestoreTeslaOnboardingStore,
    owner_user_id: str,
    connection_id: str,
    vehicles: list[TeslaVehicle],
    statuses: dict[str, FleetStatus],
) -> list[VehicleRecord]:
    indexed = [(vehicle, store._vin_index(vehicle.vin)) for vehicle in vehicles]
    snapshots = [reference.get(transaction=transaction) for _, reference in indexed]
    existing_snapshots = list(
        store.client.collection(VEHICLES)
        .where(filter=FieldFilter("owner_user_id", "==", owner_user_id))
        .stream(transaction=transaction)
    )
    records: list[VehicleRecord] = []
    active_vehicle_ids: set[str] = set()
    for (vehicle, index_reference), index_snapshot in zip(indexed, snapshots, strict=True):
        if index_snapshot.exists:
            data = index_snapshot.to_dict()
            if data is None or data.get("owner_user_id") != owner_user_id:
                raise VehicleOwnershipConflictError(
                    "Tesla vehicle is already bound to another platform user"
                )
            vehicle_id = str(data.get("vehicle_id", ""))
            if not vehicle_id:
                raise TeslaOnboardingError("VIN ownership index is invalid")
        else:
            vehicle_id = stable_vehicle_id(owner_user_id, vehicle.vin)

        status = statuses.get(vehicle.vin)
        record = VehicleRecord(
            vehicle_id=vehicle_id,
            owner_user_id=owner_user_id,
            connection_id=connection_id,
            vin=vehicle.vin,
            tesla_vehicle_id=vehicle.tesla_vehicle_id,
            display_name=vehicle.display_name,
            state=vehicle.state,
            authorization_status="active",
            virtual_key_status=virtual_key_status(status),
            command_protocol_required=(
                status.vehicle_command_protocol_required if status is not None else None
            ),
            firmware_version=status.firmware_version if status is not None else None,
            fleet_telemetry_version=(
                status.fleet_telemetry_version if status is not None else None
            ),
            total_number_of_keys=status.total_number_of_keys if status is not None else None,
        )
        transaction.set(
            index_reference,
            {
                "owner_user_id": owner_user_id,
                "vehicle_id": vehicle_id,
                "updated_at": firestore.SERVER_TIMESTAMP,
            },
        )
        transaction.set(
            store.client.collection(VEHICLES).document(vehicle_id),
            {
                "owner_user_id": record.owner_user_id,
                "connection_id": record.connection_id,
                "vin": record.vin,
                "tesla_vehicle_id": record.tesla_vehicle_id,
                "display_name": record.display_name,
                "state": record.state,
                "authorization_status": record.authorization_status,
                "virtual_key_status": record.virtual_key_status,
                "vehicle_command_protocol_required": record.command_protocol_required,
                "firmware_version": record.firmware_version,
                "fleet_telemetry_version": record.fleet_telemetry_version,
                "total_number_of_keys": record.total_number_of_keys,
                "updated_at": firestore.SERVER_TIMESTAMP,
            },
            merge=True,
        )
        active_vehicle_ids.add(vehicle_id)
        records.append(record)
    for snapshot in existing_snapshots:
        data = snapshot.to_dict()
        if (
            snapshot.id not in active_vehicle_ids
            and data is not None
            and data.get("connection_id") == connection_id
        ):
            transaction.update(
                snapshot.reference,
                {
                    "authorization_status": "not_returned",
                    "updated_at": firestore.SERVER_TIMESTAMP,
                },
            )
    return records


@firestore.transactional
def _update_fleet_status(
    transaction: Transaction,
    store: FirestoreTeslaOnboardingStore,
    owner_user_id: str,
    vehicle_id: str,
    status: FleetStatus,
) -> VehicleRecord:
    reference = store.client.collection(VEHICLES).document(vehicle_id)
    snapshot = reference.get(transaction=transaction)
    if not snapshot.exists:
        raise TeslaOnboardingError("Vehicle does not exist")
    record = store._vehicle_from_data(vehicle_id, snapshot.to_dict())
    require_vehicle_owner(record, owner_user_id)
    if record.vin != status.vin:
        raise CrossUserAccessError("Tesla fleet status does not match selected vehicle")
    updated = VehicleRecord(
        vehicle_id=record.vehicle_id,
        owner_user_id=record.owner_user_id,
        connection_id=record.connection_id,
        vin=record.vin,
        tesla_vehicle_id=record.tesla_vehicle_id,
        display_name=record.display_name,
        state=record.state,
        authorization_status=record.authorization_status,
        virtual_key_status=virtual_key_status(status),
        command_protocol_required=status.vehicle_command_protocol_required,
        firmware_version=status.firmware_version,
        fleet_telemetry_version=status.fleet_telemetry_version,
        total_number_of_keys=status.total_number_of_keys,
    )
    transaction.update(
        reference,
        {
            "virtual_key_status": updated.virtual_key_status,
            "vehicle_command_protocol_required": updated.command_protocol_required,
            "firmware_version": updated.firmware_version,
            "fleet_telemetry_version": updated.fleet_telemetry_version,
            "total_number_of_keys": updated.total_number_of_keys,
            "updated_at": firestore.SERVER_TIMESTAMP,
        },
    )
    return updated


def _optional_string(data: Mapping[str, Any], key: str) -> str | None:
    value = data.get(key)
    return value if isinstance(value, str) else None


def _optional_bool(data: Mapping[str, Any], key: str) -> bool | None:
    value = data.get(key)
    return value if isinstance(value, bool) else None


def _optional_int(data: Mapping[str, Any], key: str) -> int | None:
    value = data.get(key)
    return value if isinstance(value, int) and not isinstance(value, bool) else None
