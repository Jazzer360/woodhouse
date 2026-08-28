"""Typed Tesla OAuth, partner, and complete personal-vehicle Fleet API boundary."""

from tesla_personal_platform.tesla_client.errors import (
    TeslaAPIError,
    TeslaAuthenticationError,
    TeslaConfigurationError,
    TeslaReauthorizationRequired,
    TeslaTransportError,
)
from tesla_personal_platform.tesla_client.fleet import TeslaFleetClient
from tesla_personal_platform.tesla_client.models import (
    BinaryDocument,
    CommandResult,
    FleetStatus,
    ListResponse,
    ObjectResponse,
    Pagination,
    TeslaRegion,
    TeslaVehicle,
    TokenSet,
    ValueResponse,
    VehicleData,
)
from tesla_personal_platform.tesla_client.oauth import (
    TeslaIDTokenVerifier,
    TeslaOAuthClient,
    TeslaOAuthConfig,
)
from tesla_personal_platform.tesla_client.observability import (
    configure_json_logging,
    tesla_api_log_context,
)
from tesla_personal_platform.tesla_client.partner import PartnerRegistrar, TeslaPartnerClient
from tesla_personal_platform.tesla_client.session import (
    PerUserTeslaClient,
    TeslaAccessContext,
    TeslaAccessProvider,
)
from tesla_personal_platform.tesla_client.telemetry import (
    FleetTelemetryProfile,
    ServerTrustProfile,
    broad_profile,
    ca_profile_from_served_chain,
    config_diff,
    parse_tesla_config,
    safe_config_document,
    supports_broad_profile,
    telemetry_config_hash,
)
from tesla_personal_platform.tesla_client.transport import (
    HttpxTransport,
    LocalCommandProxyTransport,
)

COMPONENT = "tesla-client"

__all__ = [
    "COMPONENT",
    "BinaryDocument",
    "CommandResult",
    "FleetStatus",
    "FleetTelemetryProfile",
    "HttpxTransport",
    "ListResponse",
    "LocalCommandProxyTransport",
    "ObjectResponse",
    "Pagination",
    "PartnerRegistrar",
    "PerUserTeslaClient",
    "ServerTrustProfile",
    "TeslaAPIError",
    "configure_json_logging",
    "tesla_api_log_context",
    "TeslaAccessContext",
    "TeslaAccessProvider",
    "TeslaAuthenticationError",
    "TeslaConfigurationError",
    "TeslaFleetClient",
    "TeslaIDTokenVerifier",
    "TeslaOAuthClient",
    "TeslaOAuthConfig",
    "TeslaPartnerClient",
    "TeslaReauthorizationRequired",
    "TeslaRegion",
    "TeslaVehicle",
    "TeslaTransportError",
    "TokenSet",
    "ValueResponse",
    "VehicleData",
    "broad_profile",
    "ca_profile_from_served_chain",
    "config_diff",
    "parse_tesla_config",
    "safe_config_document",
    "supports_broad_profile",
    "telemetry_config_hash",
]
