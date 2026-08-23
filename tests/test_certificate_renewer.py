"""Unattended Fleet Telemetry certificate renewal tests."""

import io
import json
import subprocess
import tarfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID
from google.api_core.exceptions import NotFound
from tesla_personal_platform.certificate_renewer import main as renewal

HOSTNAME = "telemetry.example.com"


class FakeSecrets:
    def __init__(self, initial: dict[str, list[bytes]] | None = None) -> None:
        self.values = initial or {}
        self.added: list[str] = []

    def access_secret_version(self, request: dict[str, object]) -> Any:
        name = str(request["name"])
        secret = name.split("/secrets/", 1)[1].split("/versions/", 1)[0]
        values = self.values.get(secret, [])
        if not values:
            raise NotFound("missing")  # type: ignore[no-untyped-call]
        return SimpleNamespace(
            name=f"projects/test/secrets/{secret}/versions/{len(values)}",
            payload=SimpleNamespace(data=values[-1]),
        )

    def add_secret_version(self, request: dict[str, object]) -> Any:
        parent = str(request["parent"])
        secret = parent.rsplit("/", 1)[-1]
        payload = request["payload"]
        assert isinstance(payload, dict)
        data = payload["data"]
        assert isinstance(data, bytes)
        self.values.setdefault(secret, []).append(data)
        self.added.append(secret)
        return SimpleNamespace(
            name=f"projects/test/secrets/{secret}/versions/{len(self.values[secret])}"
        )


class UnusedSession:
    def get(self, url: str | bytes, *args: Any, **kwargs: Any) -> Any:
        raise AssertionError(f"unexpected GET {url!r}")

    def post(self, url: str | bytes, *args: Any, **kwargs: Any) -> Any:
        raise AssertionError(f"unexpected POST {url!r}")


class RecordingSession:
    def __init__(self) -> None:
        self.posts: list[str | bytes] = []

    def get(self, url: str | bytes, *args: Any, **kwargs: Any) -> Any:
        raise AssertionError(f"unexpected GET {url!r}")

    def post(self, url: str | bytes, *args: Any, **kwargs: Any) -> Any:
        self.posts.append(url)
        return SimpleNamespace(raise_for_status=lambda: None)


def settings() -> renewal.Settings:
    return renewal.Settings(
        project_id="test",
        zone="us-central1-a",
        instance="edge",
        hostname=HOSTNAME,
        acme_email="operator@example.com",
        cloudflare_api_token="not-a-real-token",
        cert_secret="cert",
        key_secret="key",
        state_secret="state",
        release_secret="release",
        trust_profile_secret="trust",
        trust_profile_id="lets-encrypt-2026",
        trust_readiness_secret="trust-readiness",
    )


def certificate_material(tmp_path: Path, hostname: str = HOSTNAME) -> renewal.CertificateMaterial:
    root_key = ec.generate_private_key(ec.SECP256R1())
    root_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Test Root")])
    root = (
        x509.CertificateBuilder()
        .subject_name(root_name)
        .issuer_name(root_name)
        .public_key(root_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.now(UTC) - timedelta(days=1))
        .not_valid_after(datetime.now(UTC) + timedelta(days=365))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(root_key, hashes.SHA256())
    )
    leaf_key = ec.generate_private_key(ec.SECP256R1())
    leaf_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, hostname)])
    leaf = (
        x509.CertificateBuilder()
        .subject_name(leaf_name)
        .issuer_name(root_name)
        .public_key(leaf_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.now(UTC) - timedelta(hours=1))
        .not_valid_after(datetime.now(UTC) + timedelta(days=89))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName(hostname)]), critical=False)
        .sign(root_key, hashes.SHA256())
    )
    live = tmp_path / "live" / renewal.CERT_NAME
    live.mkdir(parents=True)
    (live / "fullchain.pem").write_bytes(
        leaf.public_bytes(serialization.Encoding.PEM)
        + root.public_bytes(serialization.Encoding.PEM)
    )
    (live / "privkey.pem").write_bytes(
        leaf_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    return renewal._load_and_validate_material(tmp_path, hostname, minimum_days=45)


def trust_pem(material: renewal.CertificateMaterial) -> bytes:
    return bytes(renewal.PEM_CERTIFICATE.findall(material.fullchain)[-1])


def trust_readiness(material: renewal.CertificateMaterial) -> bytes:
    profile = renewal._load_trust_profile(settings().trust_profile_id, trust_pem(material))
    return json.dumps(
        {
            "ready": True,
            "required_vehicle_count": 0,
            "trust_profile_id": profile.profile_id,
            "trust_profile_sha256": profile.sha256,
        }
    ).encode()


def test_certificate_validation_requires_hostname_chain_and_matching_key(tmp_path: Path) -> None:
    material = certificate_material(tmp_path)

    assert material.not_after > datetime.now(UTC) + timedelta(days=45)
    assert len(material.leaf_sha256) == 64
    assert len(material.fullchain_sha256) == 64

    with pytest.raises(ValueError, match="SAN"):
        renewal._load_and_validate_material(tmp_path, "wrong.example.com", minimum_days=45)


def test_acme_state_rejects_path_traversal(tmp_path: Path) -> None:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        info = tarfile.TarInfo("../../outside")
        info.size = 1
        archive.addfile(info, io.BytesIO(b"x"))

    with pytest.raises((tarfile.TarError, ValueError, OSError)):
        renewal._extract_state(buffer.getvalue(), tmp_path / "state")


def test_new_release_is_published_only_after_pair_and_state(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    material = certificate_material(tmp_path / "certificate")
    secrets = FakeSecrets(
        {
            "trust": [trust_pem(material)],
            "trust-readiness": [trust_readiness(material)],
        }
    )
    deployed: list[tuple[str, str]] = []
    monkeypatch.setattr(renewal, "_run_certbot", lambda *args, **kwargs: None)
    monkeypatch.setattr(renewal, "_load_and_validate_material", lambda *args, **kwargs: material)
    monkeypatch.setattr(renewal, "_archive_state", lambda path: b"state")
    monkeypatch.setattr(
        renewal,
        "_deploy_release",
        lambda session, configured, *, release_version, leaf_sha256, **kwargs: deployed.append(
            (release_version, leaf_sha256)
        ),
    )

    outcome = renewal.renew_certificate(
        settings(),
        secrets,
        UnusedSession(),
        runner=lambda *args, **kwargs: subprocess.CompletedProcess(
            args=[], returncode=0, stdout=b"", stderr=b""
        ),
    )

    assert outcome == "renewed"
    assert secrets.added == ["cert", "key", "state", "release"]
    manifest = json.loads(secrets.values["release"][0])
    assert manifest["cert_version"] == "1"
    assert manifest["key_version"] == "1"
    assert manifest["state_version"] == "1"
    assert manifest["leaf_sha256"] == material.leaf_sha256
    assert manifest["trust_profile_id"] == "lets-encrypt-2026"
    assert len(manifest["trust_profile_sha256"]) == 64
    assert deployed == [("1", material.leaf_sha256)]


def test_unchanged_certificate_does_not_create_secret_versions(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    configured = settings()
    material = certificate_material(tmp_path / "certificate")
    active = renewal.SecretValue(
        renewal._release_document(
            configured,
            material,
            cert_version="7",
            key_version="9",
            state_version="4",
            trust_profile=renewal._load_trust_profile(
                configured.trust_profile_id, trust_pem(material)
            ),
        ),
        "3",
    )
    secrets = FakeSecrets({"release": [active.data], "trust": [trust_pem(material)]})
    deployed: list[str] = []
    monkeypatch.setattr(renewal, "_run_certbot", lambda *args, **kwargs: None)
    monkeypatch.setattr(renewal, "_load_and_validate_material", lambda *args, **kwargs: material)
    monkeypatch.setattr(
        renewal,
        "_deploy_release",
        lambda session, configured, *, release_version, **kwargs: deployed.append(release_version),
    )

    outcome = renewal.renew_certificate(
        configured,
        secrets,
        UnusedSession(),
        runner=lambda *args, **kwargs: subprocess.CompletedProcess(
            args=[], returncode=0, stdout=b"", stderr=b""
        ),
    )

    assert outcome == "healthy"
    assert secrets.added == []
    assert deployed == ["1"]


def test_changed_trust_profile_cannot_cut_over_without_matching_readiness(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    material = certificate_material(tmp_path / "certificate")
    secrets = FakeSecrets({"trust": [trust_pem(material)]})
    monkeypatch.setattr(renewal, "_run_certbot", lambda *args, **kwargs: None)
    monkeypatch.setattr(renewal, "_load_and_validate_material", lambda *args, **kwargs: material)

    with pytest.raises(ValueError, match="cutover is not approved"):
        renewal.renew_certificate(
            settings(),
            secrets,
            UnusedSession(),
            runner=lambda *args, **kwargs: subprocess.CompletedProcess(
                args=[], returncode=0, stdout=b"", stderr=b""
            ),
        )

    assert secrets.added == []


def test_reported_release_still_requires_public_certificate_match(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected = "a" * 64
    marker = f":tls=8:{expected[:16]}"
    guest_statuses = iter([marker, marker])
    fingerprints = iter(["b" * 64, expected])
    session = RecordingSession()
    monkeypatch.setattr(renewal, "_guest_status", lambda *args: next(guest_statuses))
    monkeypatch.setattr(renewal, "_public_leaf_fingerprint", lambda *args: next(fingerprints))

    renewal._deploy_release(
        session,
        settings(),
        release_version="8",
        leaf_sha256=expected,
        runner=lambda *args, **kwargs: subprocess.CompletedProcess(
            args=[], returncode=0, stdout=b"", stderr=b""
        ),
        sleeper=lambda seconds: None,
    )

    assert len(session.posts) == 1
