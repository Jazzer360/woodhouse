"""Safe, normalized Tesla API failure categories."""


class TeslaAPIError(Exception):
    """A Tesla endpoint returned an invalid or unsuccessful response."""

    def __init__(
        self,
        message: str,
        *,
        category: str = "unspecified",
        status_code: int | None = None,
        correlation_id: str | None = None,
    ) -> None:
        super().__init__(message)
        self.category = category
        self.status_code = status_code
        self.correlation_id = correlation_id


class TeslaAuthenticationError(TeslaAPIError):
    """A Tesla OAuth exchange or identity-token validation failed."""


class TeslaReauthorizationRequired(TeslaAuthenticationError):
    """The Tesla user must complete the authorization flow again."""


class TeslaConfigurationError(TeslaAPIError):
    """Local Tesla application configuration is missing or unsafe."""


class TeslaTransportError(TeslaAPIError):
    """The Tesla request failed before a valid HTTP response was received."""

    def __init__(self, message: str = "Tesla Fleet API transport failed") -> None:
        super().__init__(message, category="transport_error")
