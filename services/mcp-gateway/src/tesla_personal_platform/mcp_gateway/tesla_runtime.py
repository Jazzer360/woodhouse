"""Fail-closed construction of the optional Phase 4 Tesla onboarding runtime."""

import os
from dataclasses import dataclass

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from google.cloud import firestore
from tesla_personal_platform.mcp_gateway.mcp_tools import MCPProtocol, TeslaMCPService
from tesla_personal_platform.mcp_gateway.telemetry_control import FleetTelemetryControlService
from tesla_personal_platform.mcp_gateway.tesla_firestore import FirestoreTeslaOnboardingStore
from tesla_personal_platform.mcp_gateway.tesla_onboarding import TeslaOnboardingService
from tesla_personal_platform.mcp_gateway.token_crypto import TokenCipher
from tesla_personal_platform.tesla_client import (
    LocalCommandProxyTransport,
    ServerTrustProfile,
    TeslaFleetClient,
    TeslaIDTokenVerifier,
    TeslaOAuthClient,
    TeslaOAuthConfig,
    UrllibTransport,
)


@dataclass(frozen=True, slots=True)
class TeslaRuntime:
    """Configured onboarding service and public-only application key document."""

    onboarding: TeslaOnboardingService
    public_key_pem: bytes
    mcp: MCPProtocol | None = None
    telemetry: FleetTelemetryControlService | None = None


def build_tesla_runtime(
    project_id: str,
    *,
    oauth_protected: bool = False,
) -> TeslaRuntime | None:
    """Build Tesla support only when Secret Manager-backed env injection is enabled."""
    enabled = os.environ.get("TESLA_ONBOARDING_ENABLED", "false").strip().casefold()
    if enabled not in {"true", "1"}:
        return None

    values = {
        "TESLA_CLIENT_ID": os.environ.get("TESLA_CLIENT_ID", ""),
        "TESLA_CLIENT_SECRET": os.environ.get("TESLA_CLIENT_SECRET", ""),
        "TESLA_OAUTH_REDIRECT_URI": os.environ.get("TESLA_OAUTH_REDIRECT_URI", ""),
        "TESLA_INITIAL_AUDIENCE": os.environ.get("TESLA_INITIAL_AUDIENCE", ""),
        "TESLA_APP_DOMAIN": os.environ.get("TESLA_APP_DOMAIN", ""),
        "TESLA_PUBLIC_KEY_PEM": os.environ.get("TESLA_PUBLIC_KEY_PEM", ""),
        "TESLA_TOKEN_ENCRYPTION_KEY": os.environ.get("TESLA_TOKEN_ENCRYPTION_KEY", ""),
    }
    missing = sorted(name for name, value in values.items() if not value.strip())
    if missing:
        raise RuntimeError(f"Tesla onboarding configuration is incomplete: {', '.join(missing)}")

    public_key_pem = _validated_public_key(values["TESLA_PUBLIC_KEY_PEM"])
    transport = UrllibTransport()
    oauth = TeslaOAuthClient(
        TeslaOAuthConfig(
            client_id=values["TESLA_CLIENT_ID"],
            client_secret=values["TESLA_CLIENT_SECRET"],
            redirect_uri=values["TESLA_OAUTH_REDIRECT_URI"],
            audience=values["TESLA_INITIAL_AUDIENCE"],
        ),
        transport,
        TeslaIDTokenVerifier(),
    )
    store = FirestoreTeslaOnboardingStore(
        firestore.Client(project=project_id),
        TokenCipher.from_base64(values["TESLA_TOKEN_ENCRYPTION_KEY"]),
    )
    fleet = TeslaFleetClient(transport)
    onboarding = TeslaOnboardingService(oauth, fleet, store, values["TESLA_APP_DOMAIN"])
    command_fleet = _build_command_fleet()
    mcp = (
        _build_mcp_runtime(
            fleet,
            command_fleet,
            onboarding,
            store,
            oauth_protected=oauth_protected,
        )
        if command_fleet is not None
        else None
    )
    telemetry = _build_telemetry_runtime(fleet, command_fleet, onboarding, store)
    return TeslaRuntime(
        onboarding=onboarding,
        public_key_pem=public_key_pem,
        mcp=mcp,
        telemetry=telemetry,
    )


def _build_command_fleet() -> TeslaFleetClient | None:
    enabled = os.environ.get("TESLA_COMMAND_PROXY_ENABLED", "false").strip().casefold()
    if enabled not in {"true", "1"}:
        return None
    ca_file = os.environ.get("TESLA_COMMAND_PROXY_CA_FILE", "").strip()
    if not ca_file:
        raise RuntimeError("TESLA_COMMAND_PROXY_CA_FILE must be configured")
    return TeslaFleetClient(
        LocalCommandProxyTransport(
            proxy_origin=os.environ.get("TESLA_COMMAND_PROXY_ORIGIN", "https://localhost:4443"),
            ca_file=ca_file,
        )
    )


def _build_mcp_runtime(
    fleet: TeslaFleetClient,
    command_fleet: TeslaFleetClient,
    onboarding: TeslaOnboardingService,
    store: FirestoreTeslaOnboardingStore,
    *,
    oauth_protected: bool,
) -> MCPProtocol | None:
    return MCPProtocol(
        TeslaMCPService(
            fleet=fleet,
            command_fleet=command_fleet,
            credentials=onboarding,
            store=store,
            audit_store=store,
            oauth_protected=oauth_protected,
        )
    )


def _build_telemetry_runtime(
    fleet: TeslaFleetClient,
    command_fleet: TeslaFleetClient | None,
    onboarding: TeslaOnboardingService,
    store: FirestoreTeslaOnboardingStore,
) -> FleetTelemetryControlService | None:
    enabled = os.environ.get("FLEET_TELEMETRY_CONTROL_ENABLED", "false").strip().casefold()
    if enabled not in {"true", "1"}:
        return None
    if command_fleet is None:
        raise RuntimeError("Fleet Telemetry control requires the Vehicle Command Proxy")
    ca_pem = os.environ.get("TELEMETRY_SERVER_CA_PEM", "").strip()
    profile_id = os.environ.get("TELEMETRY_TRUST_PROFILE_ID", "").strip()
    hostname = os.environ.get("TELEMETRY_HOSTNAME", "").strip()
    if not all((ca_pem, profile_id, hostname)):
        raise RuntimeError("Fleet Telemetry trust profile configuration is incomplete")
    try:
        port = int(os.environ.get("TELEMETRY_PORT", "443"))
    except ValueError as error:
        raise RuntimeError("TELEMETRY_PORT must be an integer") from error
    trust = ServerTrustProfile.from_pem(profile_id, hostname, port, ca_pem)
    return FleetTelemetryControlService(
        fleet=fleet,
        signed_fleet=command_fleet,
        credentials=onboarding,
        store=store,
        trust_profile=trust,
    )


def _validated_public_key(value: str) -> bytes:
    pem = value.strip().encode("ascii") + b"\n"
    try:
        key = serialization.load_pem_public_key(pem)
    except (ValueError, TypeError) as error:
        raise RuntimeError("TESLA_PUBLIC_KEY_PEM is not a valid PEM public key") from error
    if not isinstance(key, ec.EllipticCurvePublicKey) or not isinstance(key.curve, ec.SECP256R1):
        raise RuntimeError("Tesla application public key must use prime256v1")
    return pem
