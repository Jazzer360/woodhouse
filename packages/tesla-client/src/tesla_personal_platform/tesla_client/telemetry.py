"""Versioned Fleet Telemetry profile, hashing, and safe diff helpers."""

from __future__ import annotations

import csv
import hashlib
import json
import re
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, replace
from importlib.resources import files
from io import StringIO
from typing import Final, Literal, cast, overload

from cryptography import x509
from cryptography.hazmat.primitives import serialization
from tesla_personal_platform.tesla_client.models import JsonObject, JsonValue
from tesla_personal_platform.tesla_client.requests import (
    FleetTelemetryConfig,
    FleetTelemetryField,
)

_SELF_DRIVING_FIELDS: Final = frozenset({"MilesSinceReset", "SelfDrivingMilesSinceReset"})
_BASELINE_RESOURCE: Final = "data/fleet_telemetry_tessie_baseline.toml"
_PROFILE_RESOURCE: Final = "data/fleet_telemetry_woodhouse.toml"
_SEMVER = re.compile(r"^(\d+)\.(\d+)(?:\.(\d+))?")
_PEM_CERTIFICATE = re.compile(
    rb"-----BEGIN CERTIFICATE-----\s+.*?-----END CERTIFICATE-----", re.DOTALL
)


@dataclass(frozen=True, slots=True)
class TelemetryFieldDefinition:
    name: str
    category: str
    value_type: str
    description: str


@dataclass(frozen=True, slots=True)
class FleetTelemetryProfile:
    version: str
    schema_version: str
    baseline_name: str
    fields: dict[str, FleetTelemetryField]
    excluded_fields: dict[str, str]
    capability_omissions: dict[str, str]
    baseline_comparison: JsonObject
    alert_types: tuple[str, ...] = ("service", "customer", "service-fix")
    delivery_policy: str = "latest"

    @property
    def field_config_hash(self) -> str:
        return _sha256_json(
            {
                "alert_types": list(self.alert_types),
                "delivery_policy": self.delivery_policy,
                "fields": {name: field.to_payload() for name, field in sorted(self.fields.items())},
                "baseline": self.baseline_name,
                "schema_version": self.schema_version,
                "version": self.version,
            }
        )


@dataclass(frozen=True, slots=True)
class ServerTrustProfile:
    profile_id: str
    hostname: str
    port: int
    ca_pem: str
    ca_hash: str

    @classmethod
    def from_pem(cls, profile_id: str, hostname: str, port: int, ca_pem: str) -> ServerTrustProfile:
        if not profile_id or not re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,63}", profile_id):
            raise ValueError("Telemetry trust profile ID is invalid")
        if not hostname or "://" in hostname or "/" in hostname:
            raise ValueError("Telemetry hostname must be a bare hostname")
        if port != 443:
            raise ValueError("Fleet Telemetry transport must use port 443")
        certificates = x509.load_pem_x509_certificates(ca_pem.encode("ascii"))
        if not certificates:
            raise ValueError("Telemetry trust profile must contain at least one certificate")
        for certificate in certificates:
            try:
                constraints = certificate.extensions.get_extension_for_class(
                    x509.BasicConstraints
                ).value
            except x509.ExtensionNotFound as error:
                raise ValueError("Telemetry trust profile contains a non-CA certificate") from error
            if not constraints.ca:
                raise ValueError("Telemetry trust profile contains a non-CA certificate")
        normalized = "".join(
            certificate.public_bytes(serialization.Encoding.PEM).decode("ascii")
            for certificate in certificates
        )
        der = sorted(
            certificate.public_bytes(serialization.Encoding.DER) for certificate in certificates
        )
        digest = hashlib.sha256(b"".join(der)).hexdigest()
        return cls(profile_id, hostname.lower(), port, normalized, digest)

    def build_config(self, profile: FleetTelemetryProfile) -> FleetTelemetryConfig:
        return FleetTelemetryConfig(
            hostname=self.hostname,
            ca=self.ca_pem,
            fields=profile.fields,
            port=self.port,
            alert_types=profile.alert_types,  # type: ignore[arg-type]
            delivery_policy=profile.delivery_policy,
        )


def ca_profile_from_served_chain(
    profile_id: str, hostname: str, port: int, served_chain_pem: str
) -> ServerTrustProfile:
    """Extract CA-only trust from a leaf-first TLS chain without retaining the leaf."""
    blocks = _PEM_CERTIFICATE.findall(served_chain_pem.encode("ascii"))
    certificates = [x509.load_pem_x509_certificate(block) for block in blocks]
    if len(certificates) < 2:
        raise ValueError("Served telemetry certificate chain has no issuing CA")
    ca_pem = "".join(
        certificate.public_bytes(serialization.Encoding.PEM).decode("ascii")
        for certificate in certificates[1:]
    )
    return ServerTrustProfile.from_pem(profile_id, hostname, port, ca_pem)


def load_field_catalog() -> tuple[TelemetryFieldDefinition, ...]:
    resource = files("tesla_personal_platform.tesla_client").joinpath(
        "data/fleet_streaming_fields_v0_9_4.csv"
    )
    reader = csv.DictReader(StringIO(resource.read_text(encoding="utf-8")))
    definitions = tuple(
        TelemetryFieldDefinition(
            name=row["Field"].strip(),
            category=row["Category"].strip(),
            value_type=row["Type"].strip(),
            description=row["Description"].strip(),
        )
        for row in reader
    )
    if len(definitions) != 239 or len({item.name for item in definitions}) != 239:
        raise RuntimeError("Pinned Fleet Telemetry field catalog is incomplete")
    return definitions


def broad_profile(fleet_telemetry_version: str | None) -> FleetTelemetryProfile:
    """Load the reviewed Tessie baseline plus explicit Woodhouse deviations."""
    catalog = {definition.name: definition for definition in load_field_catalog()}
    baseline_document = _load_toml_resource(_BASELINE_RESOURCE)
    profile_document = _load_toml_resource(_PROFILE_RESOURCE)
    baseline_meta = _require_table(baseline_document, "baseline")
    profile_meta = _require_table(profile_document, "profile")
    baseline_name = _require_string(baseline_meta, "name")
    if _require_string(profile_meta, "baseline") != baseline_name:
        raise RuntimeError("Woodhouse profile names a different Tessie baseline")
    if _require_string(profile_meta, "delivery_policy") != _require_string(
        baseline_meta, "delivery_policy"
    ) or _require_alert_types(profile_meta) != _require_alert_types(baseline_meta):
        raise RuntimeError("Transport policy changes require an explicit implementation decision")

    baseline_fields = _load_field_section(
        baseline_document, "fields", catalog, require_rationale=False
    )
    overrides = _load_field_section(profile_document, "overrides", catalog)
    additions = _load_field_section(profile_document, "additions", catalog)
    removals = _load_reason_section(profile_document, "removals", catalog)
    omissions = _load_reason_section(profile_document, "omissions", catalog)
    _validate_declarative_partition(
        catalog=catalog,
        baseline=baseline_fields,
        overrides=overrides,
        additions=additions,
        removals=removals,
        omissions=omissions,
    )

    configured = dict(baseline_fields)
    for name in removals:
        del configured[name]
    configured.update({name: field for name, (field, _) in overrides.items()})
    configured.update({name: field for name, (field, _) in additions.items()})

    supports_self_driving = _version_at_least(fleet_telemetry_version, (1, 2, 0))
    capability_omissions: dict[str, str] = {}
    if not supports_self_driving:
        for name in _SELF_DRIVING_FIELDS & configured.keys():
            capability_omissions[name] = (
                "Requires Fleet Telemetry client 1.2.0 and supported HW4 firmware."
            )
            del configured[name]
    if not _version_at_least(fleet_telemetry_version, (1, 3, 0)):
        for name, field in tuple(configured.items()):
            if field.include_fields:
                configured[name] = replace(field, include_fields=())
                capability_omissions[f"{name}.include_fields"] = (
                    "Synchronized included fields require Fleet Telemetry client 1.3.0. "
                    "The included field remains independently configured."
                )

    excluded_fields = {**omissions, **removals}
    baseline_comparison = _build_baseline_comparison(
        baseline_name=baseline_name,
        baseline_fields=baseline_fields,
        configured_fields=configured,
        overrides=overrides,
        additions=additions,
        removals=removals,
        omissions=omissions,
        capability_omissions=capability_omissions,
    )
    return FleetTelemetryProfile(
        version=_require_string(profile_meta, "version"),
        schema_version=_require_string(profile_meta, "schema_version"),
        baseline_name=baseline_name,
        fields=configured,
        excluded_fields=excluded_fields,
        capability_omissions=capability_omissions,
        baseline_comparison=baseline_comparison,
        alert_types=_require_alert_types(profile_meta),
        delivery_policy=_require_string(profile_meta, "delivery_policy"),
    )


def _load_toml_resource(name: str) -> dict[str, object]:
    resource = files("tesla_personal_platform.tesla_client").joinpath(name)
    return cast(dict[str, object], tomllib.loads(resource.read_text(encoding="utf-8")))


def _require_table(document: Mapping[str, object], key: str) -> dict[str, object]:
    value = document.get(key)
    if not isinstance(value, dict) or not all(isinstance(name, str) for name in value):
        raise RuntimeError(f"Telemetry profile section [{key}] is missing or invalid")
    return cast(dict[str, object], value)


def _require_string(document: Mapping[str, object], key: str) -> str:
    value = document.get(key)
    if not isinstance(value, str) or not value.strip():
        raise RuntimeError(f"Telemetry profile value {key!r} must be a non-empty string")
    return value


def _require_alert_types(document: Mapping[str, object]) -> tuple[str, ...]:
    value = document.get("alert_types")
    allowed = {"service", "customer", "service-fix"}
    if (
        not isinstance(value, list)
        or not value
        or not all(isinstance(item, str) and item in allowed for item in value)
    ):
        raise RuntimeError("Telemetry profile alert_types are invalid")
    return tuple(cast(list[str], value))


@overload
def _load_field_section(
    document: Mapping[str, object],
    section: str,
    catalog: Mapping[str, TelemetryFieldDefinition],
    *,
    require_rationale: Literal[False],
) -> dict[str, FleetTelemetryField]: ...


@overload
def _load_field_section(
    document: Mapping[str, object],
    section: str,
    catalog: Mapping[str, TelemetryFieldDefinition],
    *,
    require_rationale: Literal[True] = True,
) -> dict[str, tuple[FleetTelemetryField, str]]: ...


def _load_field_section(
    document: Mapping[str, object],
    section: str,
    catalog: Mapping[str, TelemetryFieldDefinition],
    *,
    require_rationale: bool = True,
) -> dict[str, tuple[FleetTelemetryField, str]] | dict[str, FleetTelemetryField]:
    values = _require_table(document, section)
    with_reasons: dict[str, tuple[FleetTelemetryField, str]] = {}
    without_reasons: dict[str, FleetTelemetryField] = {}
    for name, raw in values.items():
        if name not in catalog:
            raise RuntimeError(f"Telemetry profile field {name!r} is not in the pinned catalog")
        if not isinstance(raw, dict):
            raise RuntimeError(f"Telemetry profile field {name!r} must be a TOML inline table")
        spec = cast(dict[str, object], raw)
        allowed = {"include_fields", "interval_seconds", "minimum_delta"}
        if require_rationale:
            allowed.add("rationale")
        unknown = set(spec) - allowed
        if unknown:
            raise RuntimeError(f"Telemetry profile field {name!r} has unknown keys: {unknown}")
        interval = spec.get("interval_seconds")
        if not isinstance(interval, int) or isinstance(interval, bool) or interval <= 0:
            raise RuntimeError(f"Telemetry profile field {name!r} has an invalid interval")
        minimum_delta = spec.get("minimum_delta")
        if minimum_delta is not None:
            if (
                not isinstance(minimum_delta, (int, float))
                or isinstance(minimum_delta, bool)
                or minimum_delta <= 0
            ):
                raise RuntimeError(f"Telemetry profile field {name!r} has an invalid delta")
            if catalog[name].value_type not in {"integer", "real", "Location"}:
                raise RuntimeError(f"Telemetry profile field {name!r} cannot use minimum_delta")
        raw_include_fields = spec.get("include_fields", [])
        if (
            not isinstance(raw_include_fields, list)
            or not all(isinstance(item, str) for item in raw_include_fields)
            or len(set(raw_include_fields)) != len(raw_include_fields)
        ):
            raise RuntimeError(f"Telemetry profile field {name!r} has invalid include_fields")
        include_fields = tuple(cast(list[str], raw_include_fields))
        invalid_includes = set(include_fields) - set(catalog)
        if invalid_includes or name in include_fields:
            raise RuntimeError(
                f"Telemetry profile field {name!r} includes invalid fields: {invalid_includes}"
            )
        field = FleetTelemetryField(
            interval_seconds=interval,
            minimum_delta=minimum_delta,
            include_fields=include_fields,
        )
        if require_rationale:
            rationale = _require_string(spec, "rationale")
            with_reasons[name] = (field, rationale)
        else:
            without_reasons[name] = field
    return with_reasons if require_rationale else without_reasons


def _load_reason_section(
    document: Mapping[str, object],
    section: str,
    catalog: Mapping[str, TelemetryFieldDefinition],
) -> dict[str, str]:
    values = _require_table(document, section)
    reasons: dict[str, str] = {}
    for name, reason in values.items():
        if name not in catalog:
            raise RuntimeError(f"Telemetry profile field {name!r} is not in the pinned catalog")
        if not isinstance(reason, str) or not reason.strip():
            raise RuntimeError(f"Telemetry profile field {name!r} requires a rationale")
        reasons[name] = reason
    return reasons


def _validate_declarative_partition(
    *,
    catalog: Mapping[str, TelemetryFieldDefinition],
    baseline: Mapping[str, FleetTelemetryField],
    overrides: Mapping[str, tuple[FleetTelemetryField, str]],
    additions: Mapping[str, tuple[FleetTelemetryField, str]],
    removals: Mapping[str, str],
    omissions: Mapping[str, str],
) -> None:
    baseline_names = set(baseline)
    if len(baseline_names) != 93:
        raise RuntimeError("Pinned Tessie telemetry baseline must contain exactly 93 fields")
    if not set(overrides) <= baseline_names:
        raise RuntimeError("Woodhouse overrides must name Tessie baseline fields")
    if not set(removals) <= baseline_names:
        raise RuntimeError("Woodhouse removals must name Tessie baseline fields")
    if set(overrides) & set(removals):
        raise RuntimeError("A Tessie field cannot be both overridden and removed")
    nonbaseline = set(catalog) - baseline_names
    if set(additions) & baseline_names:
        raise RuntimeError("Woodhouse additions must not already be in the Tessie baseline")
    if set(omissions) & baseline_names:
        raise RuntimeError("Catalog omissions must not name Tessie baseline fields")
    if set(additions) & set(omissions):
        raise RuntimeError("A non-baseline field cannot be both added and omitted")
    if set(additions) | set(omissions) != nonbaseline:
        missing = sorted(nonbaseline - set(additions) - set(omissions))
        raise RuntimeError(f"Every non-baseline catalog field requires a decision: {missing}")


def _build_baseline_comparison(
    *,
    baseline_name: str,
    baseline_fields: Mapping[str, FleetTelemetryField],
    configured_fields: Mapping[str, FleetTelemetryField],
    overrides: Mapping[str, tuple[FleetTelemetryField, str]],
    additions: Mapping[str, tuple[FleetTelemetryField, str]],
    removals: Mapping[str, str],
    omissions: Mapping[str, str],
    capability_omissions: Mapping[str, str],
) -> JsonObject:
    override_document: JsonObject = {
        name: {
            "baseline": baseline_fields[name].to_payload(),
            "woodhouse": (
                configured_fields[name].to_payload() if name in configured_fields else None
            ),
            "rationale": rationale,
        }
        for name, (field, rationale) in sorted(overrides.items())
    }
    addition_document: JsonObject = {
        name: {
            "woodhouse": (
                configured_fields[name].to_payload() if name in configured_fields else None
            ),
            "rationale": rationale,
        }
        for name, (field, rationale) in sorted(additions.items())
    }
    removal_document: JsonObject = {
        name: {"baseline": baseline_fields[name].to_payload(), "rationale": rationale}
        for name, rationale in sorted(removals.items())
    }
    return {
        "baseline": baseline_name,
        "baseline_field_count": len(baseline_fields),
        "woodhouse_field_count": len(configured_fields),
        "overrides": override_document,
        "additions": addition_document,
        "removals": removal_document,
        "catalog_omissions": dict(sorted(omissions.items())),
        "capability_omissions": dict(sorted(capability_omissions.items())),
    }


def supports_broad_profile(fleet_telemetry_version: str | None) -> bool:
    """Return whether the vehicle client supports deltas and delivery-policy semantics."""
    return _version_at_least(fleet_telemetry_version, (1, 0, 0))


def telemetry_config_hash(profile: FleetTelemetryProfile, trust: ServerTrustProfile) -> str:
    return _sha256_json(
        {
            "field_config_hash": profile.field_config_hash,
            "hostname": trust.hostname,
            "port": trust.port,
            "trust_profile_hash": trust.ca_hash,
            "trust_profile_id": trust.profile_id,
        }
    )


def safe_config_document(
    config: FleetTelemetryConfig, *, trust_profile_id: str | None = None
) -> JsonObject:
    """Return an operator-safe exact document with CA material replaced by its hash."""
    certificates = x509.load_pem_x509_certificates(config.ca.encode("ascii"))
    if not certificates:
        raise ValueError("Telemetry configuration CA is empty")
    digest = hashlib.sha256(
        b"".join(
            sorted(
                certificate.public_bytes(serialization.Encoding.DER) for certificate in certificates
            )
        )
    ).hexdigest()
    contains_non_ca = False
    for certificate in certificates:
        try:
            constraints = certificate.extensions.get_extension_for_class(
                x509.BasicConstraints
            ).value
        except x509.ExtensionNotFound:
            contains_non_ca = True
            continue
        contains_non_ca = contains_non_ca or not constraints.ca
    alert_types: list[JsonValue] = [str(item) for item in sorted(config.alert_types)]
    return {
        "hostname": config.hostname,
        "port": config.port,
        "ca": {
            "profile_id": trust_profile_id,
            "sha256": digest,
            "certificate_count": len(certificates),
            "contains_non_ca": contains_non_ca,
        },
        "fields": {name: field.to_payload() for name, field in sorted(config.fields.items())},
        "alert_types": alert_types,
        "delivery_policy": config.delivery_policy,
    }


def parse_tesla_config(document: JsonObject) -> FleetTelemetryConfig | None:
    raw = document.get("config")
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ValueError("Tesla telemetry response config is invalid")
    hostname = raw.get("hostname")
    ca = raw.get("ca")
    port = raw.get("port", 443)
    fields = raw.get("fields")
    alerts = raw.get("alert_types", [])
    if (
        not isinstance(hostname, str)
        or not isinstance(ca, str)
        or not isinstance(port, int)
        or not isinstance(fields, dict)
        or not isinstance(alerts, list)
    ):
        raise ValueError("Tesla telemetry response config is incomplete")
    parsed_fields: dict[str, FleetTelemetryField] = {}
    for name, value in fields.items():
        if not isinstance(name, str) or not isinstance(value, dict):
            raise ValueError("Tesla telemetry response field is invalid")
        interval = value.get("interval_seconds")
        minimum_delta = value.get("minimum_delta")
        resend = value.get("resend_interval_seconds")
        include = value.get("include_fields", [])
        if not isinstance(interval, int) or isinstance(interval, bool):
            raise ValueError("Tesla telemetry response interval is invalid")
        if minimum_delta is not None and (
            not isinstance(minimum_delta, (int, float)) or isinstance(minimum_delta, bool)
        ):
            raise ValueError("Tesla telemetry response delta is invalid")
        if resend is not None and (not isinstance(resend, int) or isinstance(resend, bool)):
            raise ValueError("Tesla telemetry response resend interval is invalid")
        if not isinstance(include, list) or not all(isinstance(item, str) for item in include):
            raise ValueError("Tesla telemetry response include_fields is invalid")
        parsed_fields[name] = FleetTelemetryField(
            interval_seconds=interval,
            minimum_delta=minimum_delta,
            resend_interval_seconds=resend,
            include_fields=tuple(str(item) for item in include),
        )
    allowed_alerts = {"service", "customer", "service-fix"}
    if not all(isinstance(item, str) and item in allowed_alerts for item in alerts):
        raise ValueError("Tesla telemetry response alert_types is invalid")
    delivery = raw.get("delivery_policy")
    if delivery is not None and not isinstance(delivery, str):
        raise ValueError("Tesla telemetry response delivery policy is invalid")
    return FleetTelemetryConfig(
        hostname=hostname,
        ca=ca,
        fields=parsed_fields,
        port=port,
        alert_types=tuple(alerts),  # type: ignore[arg-type]
        delivery_policy=delivery,
    )


def config_diff(
    current: FleetTelemetryConfig | None,
    desired: FleetTelemetryConfig,
    *,
    desired_trust_profile_id: str,
) -> JsonObject:
    desired_safe = safe_config_document(desired, trust_profile_id=desired_trust_profile_id)
    if current is None:
        return {"status": "absent", "desired": desired_safe}
    current_safe = safe_config_document(current)
    changes: JsonObject = {}
    for key in ("hostname", "port", "ca", "alert_types", "delivery_policy"):
        current_value = current_safe[key]
        desired_value = desired_safe[key]
        if key == "ca" and isinstance(current_value, dict) and isinstance(desired_value, dict):
            current_value = {
                name: value for name, value in current_value.items() if name != "profile_id"
            }
            desired_value = {
                name: value for name, value in desired_value.items() if name != "profile_id"
            }
        if current_value != desired_value:
            changes[key] = {"current": current_safe[key], "desired": desired_safe[key]}
    current_fields = current_safe["fields"]
    desired_fields = desired_safe["fields"]
    if not isinstance(current_fields, dict) or not isinstance(desired_fields, dict):
        raise RuntimeError("Safe telemetry documents have invalid field maps")
    added: JsonObject = {
        key: desired_fields[key] for key in sorted(desired_fields.keys() - current_fields.keys())
    }
    removed: JsonObject = {
        key: current_fields[key] for key in sorted(current_fields.keys() - desired_fields.keys())
    }
    changed: JsonObject = {
        key: {"current": current_fields[key], "desired": desired_fields[key]}
        for key in sorted(current_fields.keys() & desired_fields.keys())
        if current_fields[key] != desired_fields[key]
    }
    if added or removed or changed:
        changes["fields"] = {"added": added, "removed": removed, "changed": changed}
    return {"status": "in_sync" if not changes else "drifted", "changes": changes}


def _version_at_least(value: str | None, minimum: tuple[int, int, int]) -> bool:
    if value is None:
        return False
    match = _SEMVER.match(value.strip().removeprefix("v"))
    if match is None:
        return False
    current = tuple(int(part or 0) for part in match.groups())
    return current >= minimum


def _sha256_json(value: object) -> str:
    encoded = json.dumps(value, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
