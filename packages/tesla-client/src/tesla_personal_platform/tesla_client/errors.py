"""Safe Tesla onboarding failure categories."""


class TeslaAPIError(Exception):
    """A Tesla endpoint returned an invalid or unsuccessful response."""


class TeslaAuthenticationError(TeslaAPIError):
    """A Tesla OAuth exchange or identity-token validation failed."""


class TeslaReauthorizationRequired(TeslaAuthenticationError):
    """The Tesla user must complete the authorization flow again."""


class TeslaConfigurationError(TeslaAPIError):
    """Local Tesla application configuration is missing or unsafe."""
