"""Narrow Tesla onboarding client boundary.

Phase 4 deliberately implements only authentication, partner registration,
region discovery, vehicle enumeration, and fleet-status inspection. The broad
typed Fleet API surface remains Phase 5 work.
"""

from tesla_personal_platform.tesla_client.errors import (
    TeslaAPIError,
    TeslaAuthenticationError,
    TeslaConfigurationError,
    TeslaReauthorizationRequired,
)
from tesla_personal_platform.tesla_client.fleet import TeslaFleetClient
from tesla_personal_platform.tesla_client.models import (
    FleetStatus,
    TeslaRegion,
    TeslaVehicle,
    TokenSet,
)
from tesla_personal_platform.tesla_client.oauth import (
    TeslaIDTokenVerifier,
    TeslaOAuthClient,
    TeslaOAuthConfig,
)
from tesla_personal_platform.tesla_client.partner import PartnerRegistrar
from tesla_personal_platform.tesla_client.transport import UrllibTransport

COMPONENT = "tesla-client"

__all__ = [
    "COMPONENT",
    "FleetStatus",
    "PartnerRegistrar",
    "TeslaAPIError",
    "TeslaAuthenticationError",
    "TeslaConfigurationError",
    "TeslaFleetClient",
    "TeslaIDTokenVerifier",
    "TeslaOAuthClient",
    "TeslaOAuthConfig",
    "TeslaReauthorizationRequired",
    "TeslaRegion",
    "TeslaVehicle",
    "TokenSet",
    "UrllibTransport",
]
