"""Tesla onboarding orchestration tests; no request reaches Tesla."""

import base64
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlsplit

import pytest
from tesla_personal_platform.auth import CrossUserAccessError, UserContext
from tesla_personal_platform.mcp_gateway.tesla_onboarding import (
    ConcurrentTokenRotationError,
    InvalidOAuthStateError,
    OnboardingResult,
    PendingAuthorization,
    TeslaConnection,
    TeslaOnboardingError,
    TeslaOnboardingService,
    VehicleOwnershipConflictError,
    VehicleRecord,
    require_vehicle_owner,
    stable_vehicle_id,
    virtual_key_status,
)
from tesla_personal_platform.mcp_gateway.token_crypto import TokenCipher
from tesla_personal_platform.tesla_client import (
    FleetStatus,
    TeslaOAuthConfig,
    TeslaReauthorizationRequired,
    TeslaRegion,
    TeslaVehicle,
    TokenSet,
)

NOW = datetime(2026, 8, 22, 12, 0, tzinfo=UTC)
NA_BASE = "https://fleet-api.prd.na.vn.cloud.tesla.com"


def token_set(
    access: str = "access-initial",
    refresh: str = "refresh-initial",
    *,
    expires_at: datetime | None = None,
) -> TokenSet:
    return TokenSet(
        access,
        refresh,
        expires_at or NOW + timedelta(hours=1),
        ("openid", "offline_access", "vehicle_device_data"),
        "tesla-account-subject",
    )


def status(vin: str, paired: bool | None) -> FleetStatus:
    return FleetStatus(vin, paired, True, "2026.20", "1.0", 2, {})


class MemoryTeslaStore:
    def __init__(self) -> None:
        self.states: dict[str, PendingAuthorization] = {}
        self.connections: dict[str, TeslaConnection] = {}
        self.vehicles: dict[str, VehicleRecord] = {}
        self.vin_owners: dict[str, str] = {}

    def create_oauth_state(self, state: str, pending: PendingAuthorization) -> None:
        self.states[state] = pending

    def consume_oauth_state(self, state: str, *, now: datetime) -> PendingAuthorization:
        pending = self.states.pop(state, None)
        if pending is None or pending.expires_at <= now:
            raise InvalidOAuthStateError("invalid state")
        return pending

    def save_connection(
        self, owner_user_id: str, tokens: TokenSet, base_url: str
    ) -> TeslaConnection:
        previous = self.connections.get(owner_user_id)
        connection = TeslaConnection(
            previous.connection_id if previous else f"conn_{owner_user_id}",
            owner_user_id,
            tokens,
            (previous.token_version + 1) if previous else 1,
            None,
            base_url,
            "discovery_pending",
        )
        self.connections[owner_user_id] = connection
        return connection

    def complete_connection(
        self, owner_user_id: str, *, region: str, base_url: str
    ) -> TeslaConnection:
        connection = replace(
            self.connections[owner_user_id],
            region=region,
            base_url=base_url,
            status="connected",
        )
        self.connections[owner_user_id] = connection
        return connection

    def get_connection(self, owner_user_id: str) -> TeslaConnection:
        return self.connections[owner_user_id]

    def rotate_tokens(
        self,
        owner_user_id: str,
        *,
        expected_version: int,
        tokens: TokenSet,
    ) -> TeslaConnection:
        current = self.connections[owner_user_id]
        if current.token_version != expected_version:
            raise ConcurrentTokenRotationError
        updated = replace(current, tokens=tokens, token_version=expected_version + 1)
        self.connections[owner_user_id] = updated
        return updated

    def mark_reauthorization_required(self, owner_user_id: str) -> None:
        current = self.connections[owner_user_id]
        self.connections[owner_user_id] = replace(current, status="reauthorization_required")

    def sync_vehicles(
        self,
        owner_user_id: str,
        connection_id: str,
        vehicles: list[TeslaVehicle],
        statuses: dict[str, FleetStatus],
    ) -> list[VehicleRecord]:
        records = []
        active_vehicle_ids: set[str] = set()
        for vehicle in vehicles:
            existing_owner = self.vin_owners.get(vehicle.vin)
            if existing_owner is not None and existing_owner != owner_user_id:
                raise VehicleOwnershipConflictError
            self.vin_owners[vehicle.vin] = owner_user_id
            fleet = statuses.get(vehicle.vin)
            record = VehicleRecord(
                stable_vehicle_id(owner_user_id, vehicle.vin),
                owner_user_id,
                connection_id,
                vehicle.vin,
                vehicle.tesla_vehicle_id,
                vehicle.display_name,
                vehicle.state,
                "active",
                virtual_key_status(fleet),
                fleet.vehicle_command_protocol_required if fleet else None,
                fleet.firmware_version if fleet else None,
                fleet.fleet_telemetry_version if fleet else None,
                fleet.total_number_of_keys if fleet else None,
            )
            self.vehicles[record.vehicle_id] = record
            active_vehicle_ids.add(record.vehicle_id)
            records.append(record)
        for vehicle_id, record in list(self.vehicles.items()):
            if (
                record.owner_user_id == owner_user_id
                and record.connection_id == connection_id
                and vehicle_id not in active_vehicle_ids
            ):
                self.vehicles[vehicle_id] = replace(record, authorization_status="not_returned")
        return records

    def list_vehicles(self, owner_user_id: str) -> list[VehicleRecord]:
        return [v for v in self.vehicles.values() if v.owner_user_id == owner_user_id]

    def get_vehicle(self, owner_user_id: str, vehicle_id: str) -> VehicleRecord:
        record = self.vehicles[vehicle_id]
        require_vehicle_owner(record, owner_user_id)
        return record

    def update_fleet_status(
        self, owner_user_id: str, vehicle_id: str, fleet: FleetStatus
    ) -> VehicleRecord:
        record = self.get_vehicle(owner_user_id, vehicle_id)
        updated = replace(
            record,
            virtual_key_status=virtual_key_status(fleet),
            command_protocol_required=fleet.vehicle_command_protocol_required,
            firmware_version=fleet.firmware_version,
            fleet_telemetry_version=fleet.fleet_telemetry_version,
            total_number_of_keys=fleet.total_number_of_keys,
        )
        self.vehicles[vehicle_id] = updated
        return updated


class FakeOAuth:
    def __init__(self) -> None:
        self.config = TeslaOAuthConfig("client", "secret", "https://example/callback", NA_BASE)
        self.exchanged_nonce: str | None = None
        self.refreshes: list[str] = []
        self.refresh_error: Exception | None = None

    def authorization_url(self, *, state: str, nonce: str) -> str:
        return f"https://auth.tesla.test/authorize?state={state}&nonce={nonce}"

    def exchange_code(self, code: str, *, nonce: str) -> TokenSet:
        assert code == "authorization-code"
        self.exchanged_nonce = nonce
        return token_set()

    def refresh(self, refresh_token: str, *, tesla_subject: str) -> TokenSet:
        assert tesla_subject == "tesla-account-subject"
        self.refreshes.append(refresh_token)
        if self.refresh_error is not None:
            raise self.refresh_error
        return token_set("access-rotated", "refresh-rotated")


class FakeFleet:
    def __init__(self) -> None:
        self.vehicles = [
            TeslaVehicle("VIN-ONE", "101", "Roadrunner", "online"),
            TeslaVehicle("VIN-TWO", "202", "Coyote", "asleep"),
        ]
        self.statuses = {
            "VIN-ONE": status("VIN-ONE", True),
            "VIN-TWO": status("VIN-TWO", False),
        }
        self.access_tokens: list[str] = []
        self.status_error: Exception | None = None

    def region(self, access_token: str, *, base_url: str) -> TeslaRegion:
        self.access_tokens.append(access_token)
        assert base_url == NA_BASE
        return TeslaRegion("na", NA_BASE)

    def list_vehicles(self, access_token: str, *, base_url: str) -> list[TeslaVehicle]:
        self.access_tokens.append(access_token)
        assert base_url == NA_BASE
        return self.vehicles

    def fleet_status(
        self, access_token: str, *, base_url: str, vins: list[str]
    ) -> dict[str, FleetStatus]:
        self.access_tokens.append(access_token)
        assert base_url == NA_BASE
        if self.status_error is not None:
            raise self.status_error
        return {vin: self.statuses[vin] for vin in vins}


def context(user_id: str = "usr_homer") -> UserContext:
    return UserContext(user_id, "tesla_u_homer", "issuer", "platform-subject")


def service(
    store: MemoryTeslaStore | None = None,
    oauth: FakeOAuth | None = None,
    fleet: FakeFleet | None = None,
) -> tuple[TeslaOnboardingService, MemoryTeslaStore, FakeOAuth, FakeFleet]:
    actual_store = store or MemoryTeslaStore()
    actual_oauth = oauth or FakeOAuth()
    actual_fleet = fleet or FakeFleet()
    return (
        TeslaOnboardingService(
            actual_oauth,
            actual_fleet,
            actual_store,
            "woodhouse.derekjass.com",
        ),
        actual_store,
        actual_oauth,
        actual_fleet,
    )


def onboard(
    onboarding: TeslaOnboardingService,
    user: UserContext | None = None,
) -> OnboardingResult:
    authorization_url = onboarding.start(user or context(), now=NOW)
    state = parse_qs(urlsplit(authorization_url).query)["state"][0]
    return onboarding.callback(state=state, code="authorization-code", now=NOW)


def test_callback_is_bound_to_server_side_platform_user_and_nonce() -> None:
    onboarding, store, oauth, _ = service()
    result = onboard(onboarding)

    assert result.owner_user_id == "usr_homer"
    assert store.connections["usr_homer"].owner_user_id == "usr_homer"
    assert oauth.exchanged_nonce is not None
    assert len(store.states) == 0


def test_oauth_state_is_single_use_and_expires() -> None:
    onboarding, store, _, _ = service()
    url = onboarding.start(context(), now=NOW)
    state = parse_qs(urlsplit(url).query)["state"][0]
    onboarding.callback(state=state, code="authorization-code", now=NOW)
    with pytest.raises(InvalidOAuthStateError):
        onboarding.callback(state=state, code="authorization-code", now=NOW)

    expired = "expired-state"
    store.create_oauth_state(expired, PendingAuthorization("usr_homer", "nonce", NOW))
    with pytest.raises(InvalidOAuthStateError):
        onboarding.callback(state=expired, code="authorization-code", now=NOW)


def test_multiple_vehicles_and_partial_pairing_are_preserved_per_vehicle() -> None:
    onboarding, _, _, _ = service()
    result = onboard(onboarding)
    documents = onboarding.vehicle_documents(result.vehicles)

    assert [item["virtual_key_status"] for item in documents] == ["paired", "pending"]
    assert len({item["vehicle_id"] for item in documents}) == 2
    assert documents[1]["virtual_key_pairing_url"] == (
        "https://www.tesla.com/_ak/woodhouse.derekjass.com?vin=VIN-TWO"
    )


def test_expired_access_token_rotates_refresh_token_before_fleet_status() -> None:
    onboarding, store, oauth, fleet = service()
    result = onboard(onboarding)
    original = store.connections["usr_homer"]
    store.connections["usr_homer"] = replace(
        original,
        tokens=token_set(expires_at=NOW - timedelta(seconds=1)),
    )

    document = onboarding.refresh_fleet_status(
        context(),
        result.vehicles[0].vehicle_id,
        now=NOW,
    )

    assert document["virtual_key_status"] == "paired"
    assert oauth.refreshes == ["refresh-initial"]
    assert fleet.access_tokens[-1] == "access-rotated"
    assert store.connections["usr_homer"].tokens.refresh_token == "refresh-rotated"
    assert store.connections["usr_homer"].token_version == 2


def test_operator_can_verify_refresh_rotation_without_receiving_tokens() -> None:
    onboarding, store, oauth, _ = service()
    onboard(onboarding)

    result = onboarding.rotate_refresh_token(context())

    assert result["status"] == "connected"
    assert result["token_version"] == 2
    assert "access_token" not in result
    assert "refresh_token" not in result
    assert oauth.refreshes == ["refresh-initial"]
    assert store.connections["usr_homer"].tokens.refresh_token == "refresh-rotated"


def test_revoked_refresh_token_marks_connection_for_reauthorization() -> None:
    oauth = FakeOAuth()
    oauth.refresh_error = TeslaReauthorizationRequired("login_required")
    onboarding, store, _, _ = service(oauth=oauth)
    result = onboard(onboarding)
    current = store.connections["usr_homer"]
    store.connections["usr_homer"] = replace(
        current,
        tokens=token_set(expires_at=NOW - timedelta(seconds=1)),
    )

    with pytest.raises(TeslaReauthorizationRequired):
        onboarding.refresh_fleet_status(
            context(),
            result.vehicles[0].vehicle_id,
            now=NOW,
        )
    assert store.connections["usr_homer"].status == "reauthorization_required"


def test_rejected_unexpired_access_token_marks_connection_for_reauthorization() -> None:
    fleet = FakeFleet()
    onboarding, store, _, _ = service(fleet=fleet)
    result = onboard(onboarding)
    fleet.status_error = TeslaReauthorizationRequired("revoked")

    with pytest.raises(TeslaReauthorizationRequired):
        onboarding.refresh_fleet_status(context(), result.vehicles[0].vehicle_id, now=NOW)
    assert store.connections["usr_homer"].status == "reauthorization_required"


def test_cross_user_vehicle_access_is_rejected_before_tesla_call() -> None:
    onboarding, _, _, fleet = service()
    result = onboard(onboarding)
    call_count = len(fleet.access_tokens)

    with pytest.raises(CrossUserAccessError):
        onboarding.refresh_fleet_status(
            context("usr_marge"),
            result.vehicles[0].vehicle_id,
            now=NOW,
        )
    assert len(fleet.access_tokens) == call_count


def test_same_vin_cannot_be_bound_to_two_platform_users() -> None:
    store = MemoryTeslaStore()
    first, _, _, _ = service(store=store)
    onboard(first, context("usr_homer"))
    second, _, _, _ = service(store=store)
    with pytest.raises(VehicleOwnershipConflictError):
        onboard(second, context("usr_marge"))


def test_vehicle_missing_from_later_enumeration_is_not_eligible() -> None:
    fleet = FakeFleet()
    onboarding, store, _, _ = service(fleet=fleet)
    first = onboard(onboarding)
    removed_vehicle_id = first.vehicles[1].vehicle_id
    fleet.vehicles = fleet.vehicles[:1]
    fleet.statuses = {"VIN-ONE": status("VIN-ONE", True)}

    onboard(onboarding)

    assert store.vehicles[removed_vehicle_id].authorization_status == "not_returned"
    with pytest.raises(TeslaOnboardingError, match="no longer returned"):
        onboarding.refresh_fleet_status(context(), removed_vehicle_id, now=NOW)


def test_token_cipher_round_trip_and_repr_do_not_expose_credentials() -> None:
    cipher = TokenCipher.from_base64(base64.b64encode(b"k" * 32).decode("ascii"))
    tokens = token_set()
    ciphertext = cipher.encrypt(tokens, owner_user_id="usr_homer")

    assert "access-initial" not in ciphertext
    assert "refresh-initial" not in ciphertext
    assert "access-initial" not in repr(tokens)
    assert "refresh-initial" not in repr(tokens)
    assert cipher.decrypt(ciphertext, owner_user_id="usr_homer") == tokens
    with pytest.raises(ValueError, match="Encrypted Tesla token state is invalid"):
        cipher.decrypt(ciphertext, owner_user_id="usr_marge")


def test_token_cipher_normalizes_legacy_naive_expiry_and_rejects_malformed_state() -> None:
    cipher = TokenCipher.from_base64(base64.b64encode(b"k" * 32).decode("ascii"))
    ciphertext = cipher.encrypt(
        token_set(expires_at=datetime(2026, 8, 22, 13, 0)),
        owner_user_id="usr_homer",
    )

    restored = cipher.decrypt(ciphertext, owner_user_id="usr_homer")

    assert restored.expires_at == datetime(2026, 8, 22, 13, 0, tzinfo=UTC)
    with pytest.raises(ValueError, match="Encrypted Tesla token state is invalid"):
        cipher.decrypt("not valid base64!", owner_user_id="usr_homer")
