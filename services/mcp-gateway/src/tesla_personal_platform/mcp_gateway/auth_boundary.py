"""Authenticated request boundary shared by all future MCP operations."""

from tesla_personal_platform.auth import (
    Authenticator,
    UserContext,
    assert_no_caller_identity_claims,
)


class GatewayAuthBoundary:
    """Derive tenant context and reject caller-selected tenant metadata."""

    def __init__(self, authenticator: Authenticator) -> None:
        self._authenticator = authenticator

    def authorize(self, authorization: str | None, payload: object) -> UserContext:
        """Return context derived only from a verified bearer token and trusted storage."""
        context = self._authenticator.authenticate_bearer(authorization)
        assert_no_caller_identity_claims(payload)
        return context
