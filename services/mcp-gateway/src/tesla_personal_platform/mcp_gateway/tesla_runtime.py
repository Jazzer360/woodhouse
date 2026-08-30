"""Fail-closed construction of the optional Phase 4 Tesla onboarding runtime."""

from dataclasses import dataclass

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from google.cloud import bigquery, firestore
from tesla_personal_platform.analytics import BigQueryAnalyticsService
from tesla_personal_platform.mcp_gateway.mcp_tools import TeslaMCPService
from tesla_personal_platform.mcp_gateway.settings import (
    CommandProxySettings,
    TelemetryControlSettings,
    TeslaSettings,
    require_setting,
)
from tesla_personal_platform.mcp_gateway.telemetry_control import FleetTelemetryControlService
from tesla_personal_platform.mcp_gateway.tesla_firestore import FirestoreTeslaOnboardingStore
from tesla_personal_platform.mcp_gateway.tesla_onboarding import TeslaOnboardingService
from tesla_personal_platform.mcp_gateway.token_crypto import TokenCipher
from tesla_personal_platform.tesla_client import (
    HttpxTransport,
    LocalCommandProxyTransport,
    ServerTrustProfile,
    TeslaFleetClient,
    TeslaIDTokenVerifier,
    TeslaOAuthClient,
    TeslaOAuthConfig,
)


@dataclass(frozen=True, slots=True)
class TeslaRuntime:
    """Configured onboarding service and public-only application key document."""

    onboarding: TeslaOnboardingService
    public_key_pem: bytes
    mcp_service: TeslaMCPService | None = None
    telemetry: FleetTelemetryControlService | None = None
    transports: tuple[HttpxTransport | LocalCommandProxyTransport, ...] = ()

    def close(self) -> None:
        """Release the runtime's owned HTTPX2 connection pools."""
        for transport in self.transports:
            transport.close()


def build_tesla_runtime(
    project_id: str,
    *,
    settings: TeslaSettings | None = None,
    command_proxy_settings: CommandProxySettings | None = None,
    telemetry_settings: TelemetryControlSettings | None = None,
    analytics_location: str = "us-central1",
) -> TeslaRuntime | None:
    """Build Tesla support only when Secret Manager-backed env injection is enabled."""
    settings = settings or TeslaSettings()
    if not settings.enabled:
        return None

    client_id = require_setting(settings.client_id, "TESLA_CLIENT_ID")
    client_secret = require_setting(settings.client_secret, "TESLA_CLIENT_SECRET")
    redirect_uri = require_setting(settings.oauth_redirect_uri, "TESLA_OAUTH_REDIRECT_URI")
    audience = require_setting(settings.initial_audience, "TESLA_INITIAL_AUDIENCE")
    app_domain = require_setting(settings.app_domain, "TESLA_APP_DOMAIN")
    public_key = require_setting(settings.public_key_pem, "TESLA_PUBLIC_KEY_PEM")
    token_key = require_setting(settings.token_encryption_key, "TESLA_TOKEN_ENCRYPTION_KEY")
    public_key_pem = _validated_public_key(public_key.get_secret_value())
    transport = HttpxTransport()
    oauth = TeslaOAuthClient(
        TeslaOAuthConfig(
            client_id=client_id,
            client_secret=client_secret.get_secret_value(),
            redirect_uri=str(redirect_uri),
            audience=str(audience),
        ),
        transport,
        TeslaIDTokenVerifier(transport=transport),
    )
    store = FirestoreTeslaOnboardingStore(
        firestore.Client(project=project_id),
        TokenCipher.from_base64(token_key.get_secret_value()),
    )
    fleet = TeslaFleetClient(transport)
    onboarding = TeslaOnboardingService(oauth, fleet, store, app_domain)
    command_transport = _build_command_transport(command_proxy_settings or CommandProxySettings())
    command_fleet = TeslaFleetClient(command_transport) if command_transport is not None else None
    mcp_service = (
        _build_mcp_runtime(
            project_id,
            fleet,
            command_fleet,
            onboarding,
            store,
            analytics_location=analytics_location,
        )
        if command_fleet is not None
        else None
    )
    telemetry = _build_telemetry_runtime(
        fleet,
        command_fleet,
        onboarding,
        store,
        telemetry_settings or TelemetryControlSettings(),
    )
    return TeslaRuntime(
        onboarding=onboarding,
        public_key_pem=public_key_pem,
        mcp_service=mcp_service,
        telemetry=telemetry,
        transports=(transport,) + ((command_transport,) if command_transport is not None else ()),
    )


def _build_command_transport(
    settings: CommandProxySettings,
) -> LocalCommandProxyTransport | None:
    if not settings.enabled:
        return None
    ca_file = require_setting(settings.ca_file, "TESLA_COMMAND_PROXY_CA_FILE")
    return LocalCommandProxyTransport(
        proxy_origin=str(settings.origin),
        ca_file=str(ca_file),
    )


def _build_mcp_runtime(
    project_id: str,
    fleet: TeslaFleetClient,
    command_fleet: TeslaFleetClient,
    onboarding: TeslaOnboardingService,
    store: FirestoreTeslaOnboardingStore,
    *,
    analytics_location: str,
) -> TeslaMCPService | None:
    return TeslaMCPService(
        fleet=fleet,
        command_fleet=command_fleet,
        credentials=onboarding,
        store=store,
        audit_store=store,
        analytics=BigQueryAnalyticsService(
            bigquery.Client(project=project_id),
            project_id,
            analytics_location,
        ),
    )


def _build_telemetry_runtime(
    fleet: TeslaFleetClient,
    command_fleet: TeslaFleetClient | None,
    onboarding: TeslaOnboardingService,
    store: FirestoreTeslaOnboardingStore,
    settings: TelemetryControlSettings,
) -> FleetTelemetryControlService | None:
    if not settings.enabled:
        return None
    if command_fleet is None:
        raise RuntimeError("Fleet Telemetry control requires the Vehicle Command Proxy")
    ca_pem = require_setting(settings.server_ca_pem, "TELEMETRY_SERVER_CA_PEM")
    profile_id = require_setting(settings.trust_profile_id, "TELEMETRY_TRUST_PROFILE_ID")
    hostname = require_setting(settings.hostname, "TELEMETRY_HOSTNAME")
    trust = ServerTrustProfile.from_pem(
        profile_id,
        hostname,
        settings.port,
        ca_pem.get_secret_value(),
    )
    return FleetTelemetryControlService(
        fleet=fleet,
        signed_fleet=command_fleet,
        credentials=onboarding,
        store=store,
        trust_profile=trust,
        receiver_version=settings.receiver_version,
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
