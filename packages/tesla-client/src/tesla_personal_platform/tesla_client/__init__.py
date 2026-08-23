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
from tesla_personal_platform.tesla_client.transport import (
    LocalCommandProxyTransport,
    UrllibTransport,
)

COMPONENT = "tesla-client"

__all__ = [
    "COMPONENT",
    "BinaryDocument",
    "CommandResult",
    "FleetStatus",
    "ListResponse",
    "LocalCommandProxyTransport",
    "ObjectResponse",
    "Pagination",
    "PartnerRegistrar",
    "PerUserTeslaClient",
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
    "UrllibTransport",
    "ValueResponse",
    "VehicleData",
]
