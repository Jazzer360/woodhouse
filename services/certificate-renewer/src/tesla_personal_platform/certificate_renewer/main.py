"""Scheduled, fail-closed ACME renewal for the Fleet Telemetry receiver."""

from __future__ import annotations

import hashlib
import io
import json
import os
import re
import subprocess
import tarfile
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Final, Protocol, cast
from urllib.parse import quote

import google.auth
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from google.api_core.exceptions import NotFound
from google.auth.transport.requests import AuthorizedSession
from google.cloud import secretmanager

ACME_SERVER: Final = "https://acme-v02.api.letsencrypt.org/directory"
CERT_NAME: Final = "telemetry-edge"
SERVICE_NAME: Final = "telemetry-certificate-renewer"
MAX_STATE_BYTES: Final = 60_000
MAX_EXTRACTED_STATE_BYTES: Final = 5_000_000
PEM_CERTIFICATE = re.compile(
    rb"-----BEGIN CERTIFICATE-----\s+.*?-----END CERTIFICATE-----",
    re.DOTALL,
)


class SecretClient(Protocol):
    def access_secret_version(self, request: dict[str, object]) -> Any: ...

    def add_secret_version(self, request: dict[str, object]) -> Any: ...


class HTTPSession(Protocol):
    def get(self, url: str | bytes, *args: Any, **kwargs: Any) -> Any: ...

    def post(self, url: str | bytes, *args: Any, **kwargs: Any) -> Any: ...


@dataclass(frozen=True, slots=True)
class Settings:
    project_id: str
    zone: str
    instance: str
    hostname: str
    acme_email: str
    cloudflare_api_token: str
    cert_secret: str
    key_secret: str
    state_secret: str
    release_secret: str
    trust_profile_secret: str
    trust_profile_id: str
    trust_readiness_secret: str
    renewal_minimum_days: int = 45
    deployment_timeout_seconds: int = 600

    @classmethod
    def from_environment(cls) -> Settings:
        minimum_days = int(os.environ.get("RENEWAL_MINIMUM_DAYS", "45"))
        deployment_timeout = int(os.environ.get("DEPLOYMENT_TIMEOUT_SECONDS", "600"))
        if minimum_days < 30 or minimum_days > 60:
            raise ValueError("RENEWAL_MINIMUM_DAYS must be between 30 and 60")
        if deployment_timeout < 120 or deployment_timeout > 1200:
            raise ValueError("DEPLOYMENT_TIMEOUT_SECONDS must be between 120 and 1200")
        return cls(
            project_id=_required_env("GOOGLE_CLOUD_PROJECT"),
            zone=_required_env("TELEMETRY_EDGE_ZONE"),
            instance=_required_env("TELEMETRY_EDGE_INSTANCE"),
            hostname=_validated_hostname(_required_env("TELEMETRY_HOSTNAME")),
            acme_email=_required_env("ACME_EMAIL"),
            cloudflare_api_token=_required_env("CLOUDFLARE_API_TOKEN"),
            cert_secret=_required_env("TLS_CERT_SECRET"),
            key_secret=_required_env("TLS_KEY_SECRET"),
            state_secret=_required_env("ACME_STATE_SECRET"),
            release_secret=_required_env("TLS_RELEASE_SECRET"),
            trust_profile_secret=_required_env("TELEMETRY_TRUST_PROFILE_SECRET"),
            trust_profile_id=_required_env("TELEMETRY_TRUST_PROFILE_ID"),
            trust_readiness_secret=_required_env("TELEMETRY_TRUST_READINESS_SECRET"),
            renewal_minimum_days=minimum_days,
            deployment_timeout_seconds=deployment_timeout,
        )


@dataclass(frozen=True, slots=True)
class CertificateMaterial:
    fullchain: bytes
    private_key: bytes
    leaf_sha256: str
    fullchain_sha256: str
    not_after: datetime


@dataclass(frozen=True, slots=True)
class TrustProfile:
    profile_id: str
    pem: bytes
    sha256: str


@dataclass(frozen=True, slots=True)
class SecretValue:
    data: bytes
    version: str


def _required_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise ValueError(f"{name} is required")
    return value


def _validated_hostname(value: str) -> str:
    if re.fullmatch(r"[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?", value) is None:
        raise ValueError("TELEMETRY_HOSTNAME is invalid")
    return value


def _secret_version_name(project_id: str, secret: str, version: str = "latest") -> str:
    return f"projects/{project_id}/secrets/{secret}/versions/{version}"


def _secret_parent(project_id: str, secret: str) -> str:
    return f"projects/{project_id}/secrets/{secret}"


def _read_secret(
    client: SecretClient, project_id: str, secret: str, *, missing_ok: bool = False
) -> SecretValue | None:
    try:
        response = client.access_secret_version(
            request={"name": _secret_version_name(project_id, secret)}
        )
    except NotFound:
        if missing_ok:
            return None
        raise
    return SecretValue(bytes(response.payload.data), response.name.rsplit("/", 1)[-1])


def _add_secret_version(client: SecretClient, project_id: str, secret: str, payload: bytes) -> str:
    response = client.add_secret_version(
        request={
            "parent": _secret_parent(project_id, secret),
            "payload": {"data": payload},
        }
    )
    return str(response.name).rsplit("/", 1)[-1]


def _extract_state(payload: bytes, destination: Path) -> None:
    if len(payload) > MAX_STATE_BYTES:
        raise ValueError("stored ACME state exceeds its size limit")
    with tarfile.open(fileobj=io.BytesIO(payload), mode="r:gz") as archive:
        members = archive.getmembers()
        if sum(member.size for member in members) > MAX_EXTRACTED_STATE_BYTES:
            raise ValueError("stored ACME state expands beyond its size limit")
        archive.extractall(destination, members=members, filter="data")


def _prune_certificate_history(config_dir: Path) -> None:
    live_dir = config_dir / "live" / CERT_NAME
    archive_dir = config_dir / "archive" / CERT_NAME
    if not live_dir.is_dir() or not archive_dir.is_dir():
        return
    keep = {path.resolve() for path in live_dir.iterdir() if path.is_symlink()}
    for candidate in archive_dir.iterdir():
        if candidate.is_file() and candidate.resolve() not in keep:
            candidate.unlink()


def _archive_state(config_dir: Path) -> bytes:
    _prune_certificate_history(config_dir)
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz", dereference=False) as archive:
        archive.add(config_dir, arcname=".")
    payload = buffer.getvalue()
    if len(payload) > MAX_STATE_BYTES:
        raise ValueError("ACME state exceeds the Secret Manager payload budget")
    return payload


def _run_certbot(
    settings: Settings,
    config_dir: Path,
    cloudflare_credentials: Path,
    runner: Callable[..., subprocess.CompletedProcess[bytes]],
) -> None:
    result = runner(
        [
            "certbot",
            "certonly",
            "--server",
            ACME_SERVER,
            "--dns-cloudflare",
            "--dns-cloudflare-credentials",
            str(cloudflare_credentials),
            "--dns-cloudflare-propagation-seconds",
            "60",
            "--config-dir",
            str(config_dir),
            "--work-dir",
            str(config_dir.parent / "work"),
            "--logs-dir",
            str(config_dir.parent / "logs"),
            "--cert-name",
            CERT_NAME,
            "--domain",
            settings.hostname,
            "--email",
            settings.acme_email,
            "--agree-tos",
            "--no-eff-email",
            "--non-interactive",
            "--keep-until-expiring",
        ],
        check=False,
        capture_output=True,
        timeout=900,
    )
    if result.returncode != 0:
        raise RuntimeError(f"certbot_failed_exit_{result.returncode}")


def _load_and_validate_material(
    config_dir: Path, hostname: str, *, minimum_days: int
) -> CertificateMaterial:
    live_dir = config_dir / "live" / CERT_NAME
    fullchain = (live_dir / "fullchain.pem").read_bytes()
    private_key = (live_dir / "privkey.pem").read_bytes()
    certificates = PEM_CERTIFICATE.findall(fullchain)
    if len(certificates) < 2:
        raise ValueError("certificate chain is incomplete")
    leaf = x509.load_pem_x509_certificate(certificates[0])
    names = leaf.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    if hostname not in names.get_values_for_type(x509.DNSName):
        raise ValueError("certificate SAN does not contain the telemetry hostname")
    not_after = leaf.not_valid_after_utc
    if not_after < datetime.now(UTC) + timedelta(days=minimum_days):
        raise ValueError("certificate validity is shorter than the deployment safety window")
    key = serialization.load_pem_private_key(private_key, password=None)
    public_format = {
        "encoding": serialization.Encoding.DER,
        "format": serialization.PublicFormat.SubjectPublicKeyInfo,
    }
    leaf_public = leaf.public_key().public_bytes(**public_format)  # type: ignore[arg-type]
    key_public = key.public_key().public_bytes(**public_format)  # type: ignore[arg-type]
    if leaf_public != key_public:
        raise ValueError("certificate and private key do not match")
    return CertificateMaterial(
        fullchain=fullchain,
        private_key=private_key,
        leaf_sha256=leaf.fingerprint(hashes.SHA256()).hex(),
        fullchain_sha256=hashlib.sha256(fullchain).hexdigest(),
        not_after=not_after,
    )


def _load_trust_profile(profile_id: str, pem: bytes) -> TrustProfile:
    certificates = x509.load_pem_x509_certificates(pem)
    if not certificates:
        raise ValueError("telemetry trust profile is empty")
    for certificate in certificates:
        try:
            constraints = certificate.extensions.get_extension_for_class(
                x509.BasicConstraints
            ).value
        except x509.ExtensionNotFound as error:
            raise ValueError("telemetry trust profile contains a non-CA certificate") from error
        if not constraints.ca:
            raise ValueError("telemetry trust profile contains a non-CA certificate")
    normalized = b"".join(
        certificate.public_bytes(serialization.Encoding.PEM) for certificate in certificates
    )
    digest = hashlib.sha256(
        b"".join(
            sorted(
                certificate.public_bytes(serialization.Encoding.DER) for certificate in certificates
            )
        )
    ).hexdigest()
    return TrustProfile(profile_id=profile_id, pem=normalized, sha256=digest)


def _validate_candidate_against_trust_profile(
    material: CertificateMaterial,
    trust: TrustProfile,
    directory: Path,
    runner: Callable[..., subprocess.CompletedProcess[bytes]],
) -> None:
    certificates = PEM_CERTIFICATE.findall(material.fullchain)
    leaf_file = directory / "candidate-leaf.pem"
    chain_file = directory / "candidate-chain.pem"
    trust_file = directory / "configured-trust.pem"
    leaf_file.write_bytes(certificates[0])
    chain_file.write_bytes(b"\n".join(certificates[1:]))
    trust_file.write_bytes(trust.pem)
    command = [
        "openssl",
        "verify",
        "-CAfile",
        str(trust_file),
        "-untrusted",
        str(chain_file),
        str(leaf_file),
    ]
    result = runner(command, check=False, capture_output=True, timeout=15)
    if result.returncode != 0:
        result = runner(
            [*command[:2], "-partial_chain", *command[2:]],
            check=False,
            capture_output=True,
            timeout=15,
        )
    if result.returncode != 0:
        raise ValueError("certificate candidate is outside the configured telemetry trust profile")


def _release_document(
    settings: Settings,
    material: CertificateMaterial,
    *,
    cert_version: str,
    key_version: str,
    state_version: str,
    trust_profile: TrustProfile,
) -> bytes:
    return json.dumps(
        {
            "cert_secret": settings.cert_secret,
            "cert_version": cert_version,
            "fullchain_sha256": material.fullchain_sha256,
            "hostname": settings.hostname,
            "key_secret": settings.key_secret,
            "key_version": key_version,
            "leaf_sha256": material.leaf_sha256,
            "not_after": material.not_after.isoformat(),
            "state_version": state_version,
            "trust_profile_id": trust_profile.profile_id,
            "trust_profile_sha256": trust_profile.sha256,
        },
        separators=(",", ":"),
        sort_keys=True,
    ).encode()


def _parse_release(value: SecretValue | None, settings: Settings) -> dict[str, object] | None:
    if value is None:
        return None
    document = json.loads(value.data)
    if not isinstance(document, dict) or document.get("hostname") != settings.hostname:
        raise ValueError("active TLS release manifest is invalid")
    for field in ("cert_version", "key_version", "leaf_sha256", "fullchain_sha256"):
        if not isinstance(document.get(field), str) or not document[field]:
            raise ValueError("active TLS release manifest is incomplete")
    document["release_version"] = value.version
    return document


def _require_trust_cutover_readiness(
    client: SecretClient, settings: Settings, trust: TrustProfile
) -> None:
    value = _read_secret(
        client,
        settings.project_id,
        settings.trust_readiness_secret,
        missing_ok=True,
    )
    if value is None:
        raise ValueError("telemetry trust-profile cutover is not approved")
    document = json.loads(value.data)
    if (
        not isinstance(document, dict)
        or document.get("ready") is not True
        or document.get("trust_profile_id") != trust.profile_id
        or document.get("trust_profile_sha256") != trust.sha256
        or not isinstance(document.get("required_vehicle_count"), int)
        or int(document["required_vehicle_count"]) < 0
    ):
        raise ValueError("telemetry trust-profile cutover approval does not match")


def _compute_url(settings: Settings, suffix: str) -> str:
    return (
        "https://compute.googleapis.com/compute/v1/projects/"
        f"{quote(settings.project_id, safe='')}/zones/{quote(settings.zone, safe='')}/instances/"
        f"{quote(settings.instance, safe='')}/{suffix}"
    )


def _guest_status(session: HTTPSession, settings: Settings) -> str | None:
    response = session.get(
        _compute_url(settings, "getGuestAttributes"),
        params={"queryPath": "telemetry-edge/status"},
        timeout=30,
    )
    if response.status_code == 404:
        return None
    response.raise_for_status()
    query_value = response.json().get("queryValue", {})
    for item in query_value.get("items", []):
        if item.get("namespace") == "telemetry-edge" and item.get("key") == "status":
            value = item.get("value")
            return value if isinstance(value, str) else None
    return None


def _public_leaf_fingerprint(
    hostname: str,
    runner: Callable[..., subprocess.CompletedProcess[bytes]],
) -> str | None:
    result = runner(
        [
            "openssl",
            "s_client",
            "-connect",
            f"{hostname}:443",
            "-servername",
            hostname,
            "-showcerts",
        ],
        input=b"",
        check=False,
        capture_output=True,
        timeout=15,
    )
    certificates = PEM_CERTIFICATE.findall(result.stdout + result.stderr)
    if not certificates:
        return None
    leaf = x509.load_pem_x509_certificate(certificates[0])
    return leaf.fingerprint(hashes.SHA256()).hex()


def _deploy_release(
    session: HTTPSession,
    settings: Settings,
    *,
    release_version: str,
    leaf_sha256: str,
    runner: Callable[..., subprocess.CompletedProcess[bytes]],
    sleeper: Callable[[float], None],
) -> None:
    marker = f":tls={release_version}:{leaf_sha256[:16]}"
    if (
        marker in (_guest_status(session, settings) or "")
        and _public_leaf_fingerprint(settings.hostname, runner) == leaf_sha256
    ):
        return
    response = session.post(_compute_url(settings, "reset"), timeout=30)
    response.raise_for_status()
    deadline = time.monotonic() + settings.deployment_timeout_seconds
    while time.monotonic() < deadline:
        sleeper(10)
        if marker not in (_guest_status(session, settings) or ""):
            continue
        if _public_leaf_fingerprint(settings.hostname, runner) == leaf_sha256:
            return
    raise RuntimeError("certificate_deployment_verification_timed_out")


def renew_certificate(
    settings: Settings,
    secret_client: SecretClient,
    session: HTTPSession,
    *,
    runner: Callable[..., subprocess.CompletedProcess[bytes]] = subprocess.run,
    sleeper: Callable[[float], None] = time.sleep,
) -> str:
    active_value = _read_secret(
        secret_client, settings.project_id, settings.release_secret, missing_ok=True
    )
    active = _parse_release(active_value, settings)
    trust_value = _read_secret(secret_client, settings.project_id, settings.trust_profile_secret)
    if trust_value is None:
        raise ValueError("telemetry trust profile is missing")
    trust = _load_trust_profile(settings.trust_profile_id, trust_value.data)
    with tempfile.TemporaryDirectory(prefix="tpp-acme-") as temporary:
        root = Path(temporary)
        config_dir = root / "letsencrypt"
        config_dir.mkdir(mode=0o700)
        state = _read_secret(
            secret_client, settings.project_id, settings.state_secret, missing_ok=True
        )
        if state is not None:
            _extract_state(state.data, config_dir)
        credentials = root / "cloudflare.ini"
        credentials.write_text(
            f"dns_cloudflare_api_token = {settings.cloudflare_api_token}\n",
            encoding="utf-8",
        )
        credentials.chmod(0o600)
        try:
            _run_certbot(settings, config_dir, credentials, runner)
        finally:
            credentials.unlink(missing_ok=True)
        material = _load_and_validate_material(
            config_dir,
            settings.hostname,
            minimum_days=settings.renewal_minimum_days,
        )
        _validate_candidate_against_trust_profile(material, trust, root, runner)
        trust_profile_changed = (
            active is None
            or active.get("trust_profile_id") != trust.profile_id
            or active.get("trust_profile_sha256") != trust.sha256
        )
        if trust_profile_changed:
            _require_trust_cutover_readiness(secret_client, settings, trust)
        if (
            active is not None
            and active.get("leaf_sha256") == material.leaf_sha256
            and active.get("trust_profile_id") == trust.profile_id
            and active.get("trust_profile_sha256") == trust.sha256
        ):
            release_version = str(active["release_version"])
            _deploy_release(
                session,
                settings,
                release_version=release_version,
                leaf_sha256=material.leaf_sha256,
                runner=runner,
                sleeper=sleeper,
            )
            return "healthy"

        cert_version = _add_secret_version(
            secret_client, settings.project_id, settings.cert_secret, material.fullchain
        )
        key_version = _add_secret_version(
            secret_client, settings.project_id, settings.key_secret, material.private_key
        )
        state_version = _add_secret_version(
            secret_client,
            settings.project_id,
            settings.state_secret,
            _archive_state(config_dir),
        )
        release_version = _add_secret_version(
            secret_client,
            settings.project_id,
            settings.release_secret,
            _release_document(
                settings,
                material,
                cert_version=cert_version,
                key_version=key_version,
                state_version=state_version,
                trust_profile=trust,
            ),
        )
        _deploy_release(
            session,
            settings,
            release_version=release_version,
            leaf_sha256=material.leaf_sha256,
            runner=runner,
            sleeper=sleeper,
        )
        return "renewed"


def _log_event(status: str, *, severity: str = "INFO", error_type: str | None = None) -> None:
    event: dict[str, object] = {
        "event": "telemetry_certificate_check",
        "service": SERVICE_NAME,
        "severity": severity,
        "status": status,
    }
    if error_type is not None:
        event["error_type"] = error_type
    print(json.dumps(event, sort_keys=True), flush=True)


def main() -> None:
    try:
        settings = Settings.from_environment()
        credentials, _ = google.auth.default(
            scopes=["https://www.googleapis.com/auth/cloud-platform"]
        )
        session = cast(HTTPSession, AuthorizedSession(credentials))  # type: ignore[no-untyped-call]
        status = renew_certificate(
            settings,
            secretmanager.SecretManagerServiceClient(),
            session,
        )
    except Exception as exc:
        _log_event("failed", severity="ERROR", error_type=type(exc).__name__)
        raise SystemExit(1) from exc
    _log_event(status)


if __name__ == "__main__":
    main()
