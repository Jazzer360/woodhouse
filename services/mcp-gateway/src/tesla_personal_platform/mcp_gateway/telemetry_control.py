"""Authenticated, per-vehicle Fleet Telemetry configuration control plane."""

from __future__ import annotations

import re
import secrets
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from tesla_personal_platform.auth import UserContext
from tesla_personal_platform.mcp_gateway.tesla_onboarding import (
    TeslaConnection,
    VehicleRecord,
)
from tesla_personal_platform.tesla_client import (
    PerUserTeslaClient,
    ServerTrustProfile,
    TeslaAccessProvider,
    TeslaAPIError,
    TeslaFleetClient,
    broad_profile,
    config_diff,
    parse_tesla_config,
    supports_broad_profile,
    telemetry_config_hash,
)
from tesla_personal_platform.tesla_client.models import JsonObject, JsonValue, json_object
from tesla_personal_platform.tesla_client.requests import (
    FleetTelemetryConfig,
    FleetTelemetryConfigRequest,
)

REQUIRED_SCOPES = frozenset({"vehicle_device_data", "vehicle_location"})
_VIN = re.compile(r"\b[A-HJ-NPR-Z0-9]{17}\b", re.IGNORECASE)
_TOKEN = re.compile(r"\beyJ[A-Za-z0-9_-]{8,}(?:\.[A-Za-z0-9_-]+){1,2}\b")


class TelemetryConfigurationError(Exception):
    """A safe control-plane error that contains no VIN, token, CA, or location."""

    def __init__(self, category: str, message: str) -> None:
        super().__init__(message)
        self.category = category


@dataclass(frozen=True, slots=True)
class TelemetryConfigurationState:
    vehicle_id: str
    profile_version: str | None = None
    config_hash: str | None = None
    field_config_hash: str | None = None
    trust_profile_id: str | None = None
    trust_profile_hash: str | None = None
    status: str = "not_configured"
    transport_maintenance_opt_in: bool = False


class TelemetryControlStore(Protocol):
    def get_connection(self, owner_user_id: str) -> TeslaConnection: ...

    def get_vehicle(self, owner_user_id: str, vehicle_id: str) -> VehicleRecord: ...

    def list_vehicles(self, owner_user_id: str) -> list[VehicleRecord]: ...

    def get_telemetry_configuration(
        self, owner_user_id: str, vehicle_id: str
    ) -> TelemetryConfigurationState: ...

    def save_telemetry_configuration(
        self,
        *,
        owner_user_id: str,
        vehicle_id: str,
        state: TelemetryConfigurationState,
    ) -> None: ...

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
    ) -> None: ...

    def complete_telemetry_config_audit(
        self,
        *,
        audit_id: str,
        result: str,
        error_category: str | None,
    ) -> None: ...


class FleetTelemetryControlService:
    """Plan and perform explicit signed configuration operations for owned vehicles."""

    def __init__(
        self,
        *,
        fleet: TeslaFleetClient,
        signed_fleet: TeslaFleetClient,
        credentials: TeslaAccessProvider,
        store: TelemetryControlStore,
        trust_profile: ServerTrustProfile,
        sync_attempts: int = 12,
        sync_delay_seconds: float = 5.0,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        if sync_attempts < 1 or sync_attempts > 60:
            raise ValueError("Telemetry sync attempts must be between 1 and 60")
        if sync_delay_seconds < 0 or sync_delay_seconds > 30:
            raise ValueError("Telemetry sync delay must be between zero and 30 seconds")
        self._fleet = PerUserTeslaClient(fleet, credentials)
        self._signed_fleet = PerUserTeslaClient(signed_fleet, credentials)
        self._store = store
        self._trust = trust_profile
        self._sync_attempts = sync_attempts
        self._sync_delay_seconds = sync_delay_seconds
        self._sleep = sleeper

    def inspect(self, context: UserContext, vehicle_id: str) -> JsonObject:
        vehicle = self._eligible_vehicle(context.user_id, vehicle_id)
        profile = broad_profile(vehicle.fleet_telemetry_version)
        desired = self._trust.build_config(profile)
        desired_hash = telemetry_config_hash(profile, self._trust)
        response = self._fleet.execute(
            context.user_id,
            lambda fleet, token, base_url: fleet.fleet_telemetry_config_get(
                token, base_url=base_url, vin=vehicle.vin
            ),
        ).data
        current = _parse_tesla_config(response)
        errors = self._fleet.execute(
            context.user_id,
            lambda fleet, token, base_url: fleet.fleet_telemetry_errors(
                token, base_url=base_url, vin=vehicle.vin
            ),
        ).value
        state = self._store.get_telemetry_configuration(context.user_id, vehicle_id)
        return {
            "vehicle_id": vehicle.vehicle_id,
            "display_name": vehicle.display_name,
            "profile_version": profile.version,
            "schema_version": profile.schema_version,
            "desired_config_hash": desired_hash,
            "field_config_hash": profile.field_config_hash,
            "trust_profile": {
                "id": self._trust.profile_id,
                "sha256": self._trust.ca_hash,
                "hostname": self._trust.hostname,
                "port": self._trust.port,
            },
            "field_count": len(profile.fields),
            "baseline_comparison": profile.baseline_comparison,
            "intentional_exclusions": json_object(profile.excluded_fields),
            "capability_omissions": json_object(profile.capability_omissions),
            "tesla": {
                "synced": response.get("synced"),
                "limit_reached": response.get("limit_reached"),
                "key_paired": response.get("key_paired"),
                "update_available": response.get("update_available"),
                "errors": _safe_error_summary(errors, vehicle.vin),
            },
            "persisted": _state_document(state),
            "diff": _config_diff(current, desired, self._trust.profile_id),
        }

    def apply(
        self,
        context: UserContext,
        vehicle_id: str,
        *,
        expected_config_hash: str,
        confirm: bool,
        transport_maintenance_opt_in: bool = False,
        source: str = "operator-onboarding",
    ) -> JsonObject:
        if not confirm:
            raise TelemetryConfigurationError(
                "confirmation_required", "Explicit vehicle configuration approval is required"
            )
        vehicle = self._eligible_vehicle(context.user_id, vehicle_id)
        profile = broad_profile(vehicle.fleet_telemetry_version)
        desired = self._trust.build_config(profile)
        desired_hash = telemetry_config_hash(profile, self._trust)
        if not secrets.compare_digest(expected_config_hash, desired_hash):
            raise TelemetryConfigurationError(
                "stale_plan", "The telemetry plan changed; inspect it again before applying"
            )
        audit_id = self._begin_audit(context.user_id, vehicle_id, "apply", desired_hash, source)
        try:
            response = self._signed_fleet.execute(
                context.user_id,
                lambda fleet, token, base_url: fleet.fleet_telemetry_config_create(
                    token,
                    base_url=base_url,
                    request=FleetTelemetryConfigRequest((vehicle.vin,), desired),
                ),
            ).data
            skipped = response.get("skipped_vehicles")
            if isinstance(skipped, (dict, list)) and skipped:
                raise TelemetryConfigurationError(
                    "vehicle_skipped", "Tesla did not accept the selected vehicle configuration"
                )
            verified = self._wait_until_synced(context.user_id, vehicle, desired)
            state = TelemetryConfigurationState(
                vehicle_id=vehicle_id,
                profile_version=profile.version,
                config_hash=desired_hash,
                field_config_hash=profile.field_config_hash,
                trust_profile_id=self._trust.profile_id,
                trust_profile_hash=self._trust.ca_hash,
                status="synced",
                transport_maintenance_opt_in=transport_maintenance_opt_in,
            )
            self._store.save_telemetry_configuration(
                owner_user_id=context.user_id, vehicle_id=vehicle_id, state=state
            )
        except Exception as error:
            self._complete_audit(audit_id, "failed", _error_category(error))
            raise
        self._complete_audit(audit_id, "succeeded", None)
        return {
            "status": "synced",
            "vehicle_id": vehicle_id,
            "config_hash": desired_hash,
            "tesla": verified,
        }

    def remove(
        self,
        context: UserContext,
        vehicle_id: str,
        *,
        confirm: bool,
        source: str = "operator-onboarding",
    ) -> JsonObject:
        if not confirm:
            raise TelemetryConfigurationError(
                "confirmation_required", "Explicit telemetry removal approval is required"
            )
        vehicle = self._eligible_vehicle(context.user_id, vehicle_id)
        audit_id = self._begin_audit(context.user_id, vehicle_id, "remove", None, source)
        try:
            self._fleet.execute(
                context.user_id,
                lambda fleet, token, base_url: fleet.fleet_telemetry_config_delete(
                    token, base_url=base_url, vin=vehicle.vin
                ),
            )
            state = TelemetryConfigurationState(vehicle_id=vehicle_id, status="removed")
            self._store.save_telemetry_configuration(
                owner_user_id=context.user_id, vehicle_id=vehicle_id, state=state
            )
        except Exception as error:
            self._complete_audit(audit_id, "failed", _error_category(error))
            raise
        self._complete_audit(audit_id, "succeeded", None)
        return {"status": "removed", "vehicle_id": vehicle_id}

    def repair(
        self,
        context: UserContext,
        vehicle_id: str,
        *,
        expected_config_hash: str,
        confirm: bool,
    ) -> JsonObject:
        return self.apply(
            context,
            vehicle_id,
            expected_config_hash=expected_config_hash,
            confirm=confirm,
            transport_maintenance_opt_in=self._store.get_telemetry_configuration(
                context.user_id, vehicle_id
            ).transport_maintenance_opt_in,
            source="operator-repair",
        )

    def reconcile_opted_in_transport(
        self, context: UserContext, *, canary_vehicle_id: str
    ) -> JsonObject:
        """Reapply only transport drift, canary first, without changing field selection."""
        vehicles = [
            vehicle
            for vehicle in self._store.list_vehicles(context.user_id)
            if vehicle.authorization_status == "active"
        ]
        selected = {vehicle.vehicle_id: vehicle for vehicle in vehicles}
        if canary_vehicle_id not in selected:
            raise TelemetryConfigurationError("invalid_canary", "Canary vehicle is not eligible")
        ordered = [
            selected[canary_vehicle_id],
            *[v for v in vehicles if v.vehicle_id != canary_vehicle_id],
        ]
        results: list[JsonValue] = []
        for index, vehicle in enumerate(ordered):
            state = self._store.get_telemetry_configuration(context.user_id, vehicle.vehicle_id)
            profile = broad_profile(vehicle.fleet_telemetry_version)
            desired_hash = telemetry_config_hash(profile, self._trust)
            if not state.transport_maintenance_opt_in:
                results.append({"vehicle_id": vehicle.vehicle_id, "status": "opt_in_required"})
                if index == 0:
                    break
                continue
            if state.field_config_hash != profile.field_config_hash:
                results.append({"vehicle_id": vehicle.vehicle_id, "status": "field_drift_blocked"})
                if index == 0:
                    break
                continue
            try:
                result = self.apply(
                    context,
                    vehicle.vehicle_id,
                    expected_config_hash=desired_hash,
                    confirm=True,
                    transport_maintenance_opt_in=True,
                    source="transport-reconciler",
                )
                results.append(result)
            except Exception as error:
                results.append(
                    {
                        "vehicle_id": vehicle.vehicle_id,
                        "status": "failed",
                        "error_category": _error_category(error),
                    }
                )
                if index == 0:
                    break
        ready = len(results) == len(ordered) and all(
            isinstance(result, dict) and result.get("status") == "synced" for result in results
        )
        return {
            "status": "ready_for_server_cutover" if ready else "blocked",
            "trust_profile_id": self._trust.profile_id,
            "trust_profile_hash": self._trust.ca_hash,
            "vehicles": results,
        }

    def _eligible_vehicle(self, owner_user_id: str, vehicle_id: str) -> VehicleRecord:
        vehicle = self._store.get_vehicle(owner_user_id, vehicle_id)
        if vehicle.authorization_status != "active":
            raise TelemetryConfigurationError(
                "vehicle_not_active", "Tesla no longer returns the selected vehicle"
            )
        if vehicle.virtual_key_status != "paired":
            raise TelemetryConfigurationError(
                "virtual_key_required", "Pair the application Virtual Key before configuration"
            )
        if not supports_broad_profile(vehicle.fleet_telemetry_version):
            raise TelemetryConfigurationError(
                "telemetry_client_upgrade_required",
                "The vehicle Fleet Telemetry client must be version 1.0.0 or later",
            )
        connection = self._store.get_connection(owner_user_id)
        missing = sorted(REQUIRED_SCOPES - set(connection.tokens.scopes))
        if missing:
            raise TelemetryConfigurationError(
                "missing_tesla_scope", "Reconnect Tesla with the required telemetry scopes"
            )
        return vehicle

    def _wait_until_synced(
        self, owner_user_id: str, vehicle: VehicleRecord, desired: FleetTelemetryConfig
    ) -> JsonObject:
        for attempt in range(self._sync_attempts):
            response = self._fleet.execute(
                owner_user_id,
                lambda fleet, token, base_url: fleet.fleet_telemetry_config_get(
                    token, base_url=base_url, vin=vehicle.vin
                ),
            ).data
            current = _parse_tesla_config(response)
            difference = _config_diff(current, desired, self._trust.profile_id)
            if response.get("synced") is True and difference.get("status") == "in_sync":
                errors = self._fleet.execute(
                    owner_user_id,
                    lambda fleet, token, base_url: fleet.fleet_telemetry_errors(
                        token, base_url=base_url, vin=vehicle.vin
                    ),
                ).value
                if _has_relevant_errors(errors):
                    raise TelemetryConfigurationError(
                        "tesla_reported_errors", "Tesla reports telemetry configuration errors"
                    )
                return {
                    "synced": True,
                    "errors": _safe_error_summary(errors, vehicle.vin),
                }
            if attempt + 1 < self._sync_attempts:
                self._sleep(self._sync_delay_seconds)
        raise TelemetryConfigurationError(
            "sync_timeout", "Tesla did not report the expected synchronized configuration"
        )

    def _begin_audit(
        self,
        owner_user_id: str,
        vehicle_id: str,
        operation: str,
        desired_hash: str | None,
        source: str,
    ) -> str:
        audit_id = f"tca_{secrets.token_hex(16)}"
        self._store.begin_telemetry_config_audit(
            audit_id=audit_id,
            timestamp=datetime.now(UTC),
            owner_user_id=owner_user_id,
            vehicle_id=vehicle_id,
            operation=operation,
            desired_config_hash=desired_hash,
            source=source,
        )
        return audit_id

    def _complete_audit(self, audit_id: str, result: str, error_category: str | None) -> None:
        self._store.complete_telemetry_config_audit(
            audit_id=audit_id, result=result, error_category=error_category
        )


def _state_document(state: TelemetryConfigurationState) -> JsonObject:
    return {
        "status": state.status,
        "profile_version": state.profile_version,
        "config_hash": state.config_hash,
        "field_config_hash": state.field_config_hash,
        "trust_profile_id": state.trust_profile_id,
        "trust_profile_hash": state.trust_profile_hash,
        "transport_maintenance_opt_in": state.transport_maintenance_opt_in,
    }


def _parse_tesla_config(document: JsonObject) -> FleetTelemetryConfig | None:
    try:
        return parse_tesla_config(document)
    except (UnicodeError, ValueError) as error:
        raise TelemetryConfigurationError(
            "invalid_tesla_configuration", "Tesla returned an invalid telemetry configuration"
        ) from error


def _config_diff(
    current: FleetTelemetryConfig | None,
    desired: FleetTelemetryConfig,
    trust_profile_id: str,
) -> JsonObject:
    try:
        return config_diff(current, desired, desired_trust_profile_id=trust_profile_id)
    except (UnicodeError, ValueError) as error:
        raise TelemetryConfigurationError(
            "invalid_tesla_configuration", "Tesla returned an invalid telemetry configuration"
        ) from error


def _safe_error_summary(value: JsonValue, vin: str) -> JsonValue:
    if not isinstance(value, list):
        return []
    output: list[JsonValue] = []
    for item in value[-20:]:
        if not isinstance(item, dict):
            continue
        safe: JsonObject = {}
        for key in ("created_at", "error_name", "error"):
            field = item.get(key)
            if isinstance(field, str):
                text = field.replace(vin, "[REDACTED_VIN]")
                text = _VIN.sub("[REDACTED_VIN]", text)
                safe[key] = _TOKEN.sub("[REDACTED_TOKEN]", text)[:512]
            elif isinstance(field, (int, float, bool)):
                safe[key] = field
        output.append(safe)
    return output


def _has_relevant_errors(value: JsonValue) -> bool:
    if not isinstance(value, list):
        return False
    cutoff = datetime.now(UTC).timestamp() - 15 * 60
    for item in value:
        if not isinstance(item, dict):
            continue
        created_at = item.get("created_at")
        if not isinstance(created_at, str):
            # An undated Tesla error cannot safely be classified as historical.
            return True
        try:
            created = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
        except ValueError:
            return True
        if created.tzinfo is None:
            created = created.replace(tzinfo=UTC)
        if created.timestamp() >= cutoff:
            return True
    return False


def _error_category(error: Exception) -> str:
    if isinstance(error, TelemetryConfigurationError):
        return error.category
    if isinstance(error, TeslaAPIError):
        return error.category
    return "unexpected_error"
