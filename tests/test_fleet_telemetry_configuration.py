"""Phase 8 broad profile and guarded per-vehicle lifecycle tests."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID
from tesla_personal_platform.auth import UserContext
from tesla_personal_platform.mcp_gateway.telemetry_control import (
    FleetTelemetryControlService,
    TelemetryConfigurationError,
    TelemetryConfigurationState,
)
from tesla_personal_platform.mcp_gateway.tesla_onboarding import (
    TeslaConnection,
    VehicleRecord,
)
from tesla_personal_platform.tesla_client import (
    ObjectResponse,
    ServerTrustProfile,
    TeslaAccessContext,
    ValueResponse,
    broad_profile,
    ca_profile_from_served_chain,
    config_diff,
    safe_config_document,
    telemetry_config_hash,
)
from tesla_personal_platform.tesla_client.models import JsonValue, TokenSet
from tesla_personal_platform.tesla_client.requests import (
    FleetTelemetryConfig,
    FleetTelemetryConfigRequest,
)
from tesla_personal_platform.tesla_client.telemetry import load_field_catalog

NOW = datetime(2026, 8, 23, tzinfo=UTC)
BASE_URL = "https://fleet-api.prd.na.vn.cloud.tesla.com"


def ca_pem(name: str = "Woodhouse test CA") -> str:
    key = ec.generate_private_key(ec.SECP256R1())
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, name)])
    certificate = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(NOW - timedelta(days=1))
        .not_valid_after(NOW + timedelta(days=3650))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )
    return certificate.public_bytes(serialization.Encoding.PEM).decode("ascii")


def trust() -> ServerTrustProfile:
    return ServerTrustProfile.from_pem(
        "woodhouse-ca-v1", "telemetry.woodhouse.example", 443, ca_pem()
    )


def served_chain() -> tuple[str, x509.Certificate, x509.Certificate]:
    root_key = ec.generate_private_key(ec.SECP256R1())
    root_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Issuing CA")])
    root = (
        x509.CertificateBuilder()
        .subject_name(root_name)
        .issuer_name(root_name)
        .public_key(root_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(NOW - timedelta(days=1))
        .not_valid_after(NOW + timedelta(days=3650))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(root_key, hashes.SHA256())
    )
    leaf_key = ec.generate_private_key(ec.SECP256R1())
    leaf_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "telemetry.example")])
    leaf = (
        x509.CertificateBuilder()
        .subject_name(leaf_name)
        .issuer_name(root_name)
        .public_key(leaf_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(NOW - timedelta(days=1))
        .not_valid_after(NOW + timedelta(days=90))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .sign(root_key, hashes.SHA256())
    )
    chain = (
        leaf.public_bytes(serialization.Encoding.PEM)
        + root.public_bytes(serialization.Encoding.PEM)
    ).decode("ascii")
    return chain, leaf, root


def vehicle(vehicle_id: str = "veh_one", vin: str = "VIN00000000000001") -> VehicleRecord:
    return VehicleRecord(
        vehicle_id=vehicle_id,
        owner_user_id="usr_one",
        connection_id="conn_one",
        vin=vin,
        tesla_vehicle_id="101",
        display_name="Woodhouse",
        state="online",
        authorization_status="active",
        virtual_key_status="paired",
        command_protocol_required=True,
        firmware_version="2026.20",
        fleet_telemetry_version="1.2.0",
        total_number_of_keys=2,
    )


class MemoryStore:
    def __init__(self, vehicles: list[VehicleRecord] | None = None) -> None:
        self.vehicles = {item.vehicle_id: item for item in vehicles or [vehicle()]}
        self.states: dict[str, TelemetryConfigurationState] = {}
        self.audits: dict[str, dict[str, object]] = {}
        self.connection = TeslaConnection(
            "conn_one",
            "usr_one",
            TokenSet(
                "access",
                "refresh",
                NOW + timedelta(hours=1),
                ("vehicle_device_data", "vehicle_location", "offline_access"),
                "tesla-subject",
            ),
            1,
            "na",
            BASE_URL,
            "connected",
        )

    def get_connection(self, owner_user_id: str) -> TeslaConnection:
        assert owner_user_id == "usr_one"
        return self.connection

    def get_vehicle(self, owner_user_id: str, vehicle_id: str) -> VehicleRecord:
        record = self.vehicles[vehicle_id]
        if record.owner_user_id != owner_user_id:
            raise AssertionError("cross-user fixture access")
        return record

    def list_vehicles(self, owner_user_id: str) -> list[VehicleRecord]:
        return [item for item in self.vehicles.values() if item.owner_user_id == owner_user_id]

    def get_telemetry_configuration(
        self, owner_user_id: str, vehicle_id: str
    ) -> TelemetryConfigurationState:
        self.get_vehicle(owner_user_id, vehicle_id)
        return self.states.get(vehicle_id, TelemetryConfigurationState(vehicle_id))

    def save_telemetry_configuration(
        self,
        *,
        owner_user_id: str,
        vehicle_id: str,
        state: TelemetryConfigurationState,
    ) -> None:
        self.get_vehicle(owner_user_id, vehicle_id)
        self.states[vehicle_id] = state

    def begin_telemetry_config_audit(self, **values: object) -> None:
        self.audits[str(values["audit_id"])] = dict(values, result="attempted")

    def complete_telemetry_config_audit(
        self, *, audit_id: str, result: str, error_category: str | None
    ) -> None:
        self.audits[audit_id].update(result=result, error_category=error_category)


class Access:
    def access_for_user(self, owner_user_id: str, **kwargs: object) -> TeslaAccessContext:
        assert owner_user_id == "usr_one"
        return TeslaAccessContext("access", BASE_URL)


class Fleet:
    def __init__(self) -> None:
        self.configs: dict[str, FleetTelemetryConfig] = {}
        self.errors: dict[str, list[JsonValue]] = {}
        self.created: list[FleetTelemetryConfigRequest] = []
        self.fail_vins: set[str] = set()
        self.malformed_vins: set[str] = set()

    def fleet_telemetry_config_get(
        self, access_token: str, *, base_url: str, vin: str
    ) -> ObjectResponse:
        if vin in self.malformed_vins:
            return ObjectResponse({"synced": True, "config": {"hostname": 7}})
        config = self.configs.get(vin)
        return ObjectResponse(
            {
                "synced": config is not None,
                "config": config.to_payload() if config is not None else None,
                "limit_reached": False,
                "key_paired": True,
            }
        )

    def fleet_telemetry_errors(
        self, access_token: str, *, base_url: str, vin: str
    ) -> ValueResponse:
        return ValueResponse(self.errors.get(vin, []))

    def fleet_telemetry_config_create(
        self, access_token: str, *, base_url: str, request: FleetTelemetryConfigRequest
    ) -> ObjectResponse:
        self.created.append(request)
        vin = request.vins[0]
        if vin in self.fail_vins:
            return ObjectResponse({"skipped_vehicles": {"unsupported_firmware": [vin]}})
        self.configs[vin] = request.config
        return ObjectResponse({"updated_vehicles": 1, "skipped_vehicles": {}})

    def fleet_telemetry_config_delete(
        self, access_token: str, *, base_url: str, vin: str
    ) -> ValueResponse:
        self.configs.pop(vin, None)
        return ValueResponse({"result": True})


def service(
    store: MemoryStore | None = None, fleet: Fleet | None = None
) -> tuple[FleetTelemetryControlService, MemoryStore, Fleet]:
    actual_store = store or MemoryStore()
    actual_fleet = fleet or Fleet()
    return (
        FleetTelemetryControlService(
            fleet=actual_fleet,  # type: ignore[arg-type]
            signed_fleet=actual_fleet,  # type: ignore[arg-type]
            credentials=Access(),
            store=actual_store,
            trust_profile=trust(),
            sync_attempts=2,
            sync_delay_seconds=0,
        ),
        actual_store,
        actual_fleet,
    )


def context() -> UserContext:
    return UserContext("usr_one", "tesla_u_one", "issuer", "subject")


def test_profile_is_a_complete_declarative_tessie_comparison() -> None:
    profile = broad_profile("1.3.0")
    definitions = {item.name: item for item in load_field_catalog()}
    comparison = profile.baseline_comparison

    assert len(definitions) == 239
    assert profile.baseline_name == "tessie-operator-snapshot-2026-08-23"
    assert len(profile.fields) == 131
    assert len(profile.excluded_fields) == 108
    assert set(profile.fields) | set(profile.excluded_fields) == set(definitions)
    assert profile.capability_omissions == {}
    assert comparison["baseline_field_count"] == 93
    assert comparison["woodhouse_field_count"] == 131
    assert len(comparison["overrides"]) == 14  # type: ignore[arg-type]
    assert len(comparison["additions"]) == 40  # type: ignore[arg-type]
    assert len(comparison["removals"]) == 2  # type: ignore[arg-type]
    assert len(comparison["catalog_omissions"]) == 106  # type: ignore[arg-type]


def test_profile_uses_deltas_only_for_defensible_measurements() -> None:
    profile = broad_profile("1.3.0")

    assert profile.fields["Location"].interval_seconds == 10
    assert profile.fields["Location"].minimum_delta == 10
    assert profile.fields["VehicleSpeed"].interval_seconds == 1
    assert profile.fields["VehicleSpeed"].minimum_delta == 1
    assert profile.fields["VehicleSpeed"].include_fields == ("LongitudinalAcceleration",)
    assert profile.fields["LongitudinalAcceleration"].interval_seconds == 1
    assert profile.fields["LongitudinalAcceleration"].minimum_delta == 1.0
    assert profile.fields["LongitudinalAcceleration"].include_fields == ("VehicleSpeed",)
    assert profile.fields["BrakePedal"].interval_seconds == 1
    assert profile.fields["SelfDrivingMilesSinceReset"].minimum_delta == 1
    assert profile.fields["SelfDrivingMilesSinceReset"].include_fields == (
        "MilesSinceReset",
    )
    assert profile.fields["MilesSinceReset"].include_fields == (
        "SelfDrivingMilesSinceReset",
    )
    assert profile.fields["HvacFanSpeed"].minimum_delta is None
    assert profile.fields["ChargeCurrentRequest"].minimum_delta is None
    assert profile.fields["MediaAudioVolume"].interval_seconds == 60
    assert "BrakePedalPos" not in profile.fields
    assert profile.fields["PackCurrent"].interval_seconds == 120
    assert profile.fields["PackCurrent"].minimum_delta == 0.1
    assert "RouteLine" not in profile.fields


def test_tessie_removals_and_woodhouse_additions_are_operator_visible() -> None:
    comparison = broad_profile("1.3.0").baseline_comparison

    assert set(comparison["removals"]) == {  # type: ignore[arg-type]
        "MediaAudioVolumeIncrement",
        "MediaAudioVolumeMax",
    }
    assert "ChargeState" in comparison["additions"]  # type: ignore[operator]
    assert "DriverSeatBelt" in comparison["additions"]  # type: ignore[operator]
    assert "LongitudinalAcceleration" in comparison["additions"]  # type: ignore[operator]
    assert "Location" in comparison["overrides"]  # type: ignore[operator]
    assert "SelfDrivingMilesSinceReset" in comparison["overrides"]  # type: ignore[operator]


def test_capability_projection_handles_self_driving_and_synchronized_includes() -> None:
    profile = broad_profile("1.1.0")

    assert len(profile.fields) == 129
    assert profile.fields["VehicleSpeed"].include_fields == ()
    assert "LongitudinalAcceleration" in profile.fields
    assert set(profile.capability_omissions) == {
        "LongitudinalAcceleration.include_fields",
        "MilesSinceReset",
        "SelfDrivingMilesSinceReset",
        "VehicleSpeed.include_fields",
    }

    version_1_2 = broad_profile("1.2.0")
    assert version_1_2.fields["VehicleSpeed"].include_fields == ()
    assert version_1_2.fields["LongitudinalAcceleration"].include_fields == ()
    assert set(version_1_2.capability_omissions) == {
        "LongitudinalAcceleration.include_fields",
        "MilesSinceReset.include_fields",
        "SelfDrivingMilesSinceReset.include_fields",
        "VehicleSpeed.include_fields",
    }

    version_1_3 = broad_profile("1.3.0")
    assert version_1_3.fields["VehicleSpeed"].include_fields == ("LongitudinalAcceleration",)
    assert version_1_3.fields["LongitudinalAcceleration"].include_fields == ("VehicleSpeed",)
    assert version_1_3.fields["SelfDrivingMilesSinceReset"].include_fields == (
        "MilesSinceReset",
    )
    assert version_1_3.fields["MilesSinceReset"].include_fields == (
        "SelfDrivingMilesSinceReset",
    )
    assert version_1_3.capability_omissions == {}


def test_config_hash_separates_field_profile_from_stable_trust_profile() -> None:
    profile = broad_profile("1.2.0")
    first = trust()
    second = ServerTrustProfile.from_pem(
        "woodhouse-ca-v2", first.hostname, first.port, ca_pem("Replacement CA")
    )

    assert telemetry_config_hash(profile, first) != telemetry_config_hash(profile, second)
    assert profile.field_config_hash == broad_profile("1.2.0").field_config_hash
    safe = safe_config_document(first.build_config(profile), trust_profile_id=first.profile_id)
    assert "BEGIN CERTIFICATE" not in str(safe)


def test_ca_profile_extraction_drops_expiring_server_leaf() -> None:
    chain, leaf, root = served_chain()

    profile = ca_profile_from_served_chain(
        "served-chain-v1", "telemetry.example", 443, f"CONNECTED\n{chain}\nDONE"
    )
    extracted = x509.load_pem_x509_certificates(profile.ca_pem.encode("ascii"))

    assert [certificate.serial_number for certificate in extracted] == [root.serial_number]
    assert leaf.serial_number not in {certificate.serial_number for certificate in extracted}
    assert len(profile.ca_hash) == 64


def test_diff_reports_exact_field_and_transport_changes_without_ca_pem() -> None:
    profile = broad_profile("1.2.0")
    desired = trust().build_config(profile)
    current = replace(
        desired,
        hostname="old.example.com",
        fields={"Soc": profile.fields["Soc"]},
    )

    difference = config_diff(current, desired, desired_trust_profile_id="woodhouse-ca-v1")

    assert difference["status"] == "drifted"
    changes = difference["changes"]
    assert isinstance(changes, dict)
    fields = changes["fields"]
    assert isinstance(fields, dict)
    added = fields["added"]
    assert isinstance(added, dict)
    assert "Location" in added
    assert "BEGIN CERTIFICATE" not in str(difference)

    same = config_diff(desired, desired, desired_trust_profile_id="woodhouse-ca-v1")
    assert same == {"status": "in_sync", "changes": {}}


def test_diff_detects_missing_synchronized_include_fields() -> None:
    profile = broad_profile("1.3.0")
    desired = trust().build_config(profile)
    current = replace(
        desired,
        fields={
            **desired.fields,
            "VehicleSpeed": replace(desired.fields["VehicleSpeed"], include_fields=()),
            "LongitudinalAcceleration": replace(
                desired.fields["LongitudinalAcceleration"], include_fields=()
            ),
        },
    )

    difference = config_diff(current, desired, desired_trust_profile_id="woodhouse-ca-v1")
    changes = difference["changes"]
    assert isinstance(changes, dict)
    fields = changes["fields"]
    assert isinstance(fields, dict)
    changed = fields["changed"]
    assert isinstance(changed, dict)
    assert changed["VehicleSpeed"] == {
        "current": {"interval_seconds": 1, "minimum_delta": 1},
        "desired": {
            "interval_seconds": 1,
            "minimum_delta": 1,
            "include_fields": ["LongitudinalAcceleration"],
        },
    }
    assert changed["LongitudinalAcceleration"] == {
        "current": {"interval_seconds": 1, "minimum_delta": 1.0},
        "desired": {
            "interval_seconds": 1,
            "minimum_delta": 1.0,
            "include_fields": ["VehicleSpeed"],
        },
    }


def test_inspect_fails_safely_when_tesla_returns_malformed_configuration() -> None:
    controller, _, fleet = service()
    fleet.malformed_vins.add(vehicle().vin)

    with pytest.raises(TelemetryConfigurationError) as failure:
        controller.inspect(context(), "veh_one")

    assert failure.value.category == "invalid_tesla_configuration"


def test_inspect_fails_safely_when_tesla_returns_invalid_ca_material() -> None:
    controller, _, fleet = service()
    desired = trust().build_config(broad_profile("1.2.0"))
    fleet.configs[vehicle().vin] = replace(desired, ca="not a certificate")

    with pytest.raises(TelemetryConfigurationError) as failure:
        controller.inspect(context(), "veh_one")

    assert failure.value.category == "invalid_tesla_configuration"


def test_apply_requires_exact_confirmation_persists_only_after_sync_and_audits() -> None:
    controller, store, fleet = service()
    plan = controller.inspect(context(), "veh_one")
    desired_hash = str(plan["desired_config_hash"])
    comparison = plan["baseline_comparison"]

    assert isinstance(comparison, dict)
    assert comparison["baseline_field_count"] == 93
    assert comparison["woodhouse_field_count"] == 131

    with pytest.raises(TelemetryConfigurationError, match="approval"):
        controller.apply(context(), "veh_one", expected_config_hash=desired_hash, confirm=False)
    with pytest.raises(TelemetryConfigurationError, match="changed"):
        controller.apply(context(), "veh_one", expected_config_hash="0" * 64, confirm=True)

    result = controller.apply(
        context(),
        "veh_one",
        expected_config_hash=desired_hash,
        confirm=True,
        transport_maintenance_opt_in=True,
    )

    assert result["status"] == "synced"
    assert len(fleet.created) == 1
    assert store.states["veh_one"].status == "synced"
    assert store.states["veh_one"].transport_maintenance_opt_in is True
    assert list(store.audits.values())[0]["result"] == "succeeded"


def test_vehicle_client_without_delta_support_is_not_configured() -> None:
    old_vehicle = replace(vehicle(), fleet_telemetry_version="0.9.0")
    controller, _, fleet = service(MemoryStore([old_vehicle]))

    with pytest.raises(TelemetryConfigurationError, match="1.0.0"):
        controller.inspect(context(), old_vehicle.vehicle_id)

    assert fleet.created == []


def test_vehicle_errors_block_persistence_and_finalize_failed_audit() -> None:
    controller, store, fleet = service()
    fleet.errors[vehicle().vin] = [{"error_name": "config_manager_error"}]
    desired_hash = str(controller.inspect(context(), "veh_one")["desired_config_hash"])

    with pytest.raises(TelemetryConfigurationError, match="reports"):
        controller.apply(context(), "veh_one", expected_config_hash=desired_hash, confirm=True)

    assert "veh_one" not in store.states
    audit = list(store.audits.values())[0]
    assert audit["result"] == "failed"
    assert audit["error_category"] == "tesla_reported_errors"


def test_historical_vehicle_errors_do_not_block_a_new_verified_configuration() -> None:
    controller, store, fleet = service()
    fleet.errors[vehicle().vin] = [
        {"created_at": "2020-01-01T00:00:00Z", "error_name": "resolved_old_error"}
    ]
    desired_hash = str(controller.inspect(context(), "veh_one")["desired_config_hash"])

    result = controller.apply(context(), "veh_one", expected_config_hash=desired_hash, confirm=True)

    assert result["status"] == "synced"
    assert store.states["veh_one"].status == "synced"


def test_transport_reconciler_canaries_and_does_not_block_independent_vehicles() -> None:
    first = vehicle()
    second = replace(vehicle("veh_two", "VIN00000000000002"), tesla_vehicle_id="202")
    store = MemoryStore([first, second])
    fleet = Fleet()
    controller, _, _ = service(store, fleet)
    profile = broad_profile("1.2.0")
    old = TelemetryConfigurationState(
        vehicle_id="veh_one",
        profile_version=profile.version,
        config_hash="old",
        field_config_hash=profile.field_config_hash,
        trust_profile_id="old",
        trust_profile_hash="old",
        status="synced",
        transport_maintenance_opt_in=True,
    )
    store.states = {"veh_one": old, "veh_two": replace(old, vehicle_id="veh_two")}
    fleet.fail_vins.add(second.vin)

    result = controller.reconcile_opted_in_transport(context(), canary_vehicle_id="veh_one")

    assert result["status"] == "blocked"
    vehicle_results = result["vehicles"]
    assert isinstance(vehicle_results, list)
    assert all(isinstance(item, dict) for item in vehicle_results)
    statuses = {
        str(item["vehicle_id"]): item["status"]
        for item in vehicle_results
        if isinstance(item, dict)
    }
    assert statuses == {"veh_one": "synced", "veh_two": "failed"}
    assert store.states["veh_one"].status == "synced"


def test_remove_is_explicit_and_vehicle_scoped() -> None:
    controller, store, fleet = service()
    plan_hash = str(controller.inspect(context(), "veh_one")["desired_config_hash"])
    controller.apply(context(), "veh_one", expected_config_hash=plan_hash, confirm=True)

    with pytest.raises(TelemetryConfigurationError, match="approval"):
        controller.remove(context(), "veh_one", confirm=False)
    result = controller.remove(context(), "veh_one", confirm=True)

    assert result == {"status": "removed", "vehicle_id": "veh_one"}
    assert vehicle().vin not in fleet.configs
    assert store.states["veh_one"].status == "removed"
