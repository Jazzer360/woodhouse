"""Provider-neutral authentication and tenant authorization rules."""

from collections.abc import Mapping, Sequence
from typing import Final, Protocol

from tesla_personal_platform.auth.errors import (
    CallerIdentityClaimError,
    CrossUserAccessError,
    EmailNotVerifiedError,
    InvalidTokenError,
    UserNotAllowedError,
)
from tesla_personal_platform.auth.models import UserContext, VerifiedIdentity

_FORBIDDEN_CALLER_KEYS = frozenset(
    {
        "dataset_id",
        "owner_email",
        "owner_user_id",
        "owned_by",
        "ownership",
        "ownership_claim",
        "user_id",
    }
)
_MAX_CALLER_PAYLOAD_DEPTH: Final = 64


class TokenVerifier(Protocol):
    """Cryptographically verify an external bearer token."""

    def verify(self, token: str) -> VerifiedIdentity:
        """Return trusted claims or raise ``InvalidTokenError``."""
        ...


class IdentityStore(Protocol):
    """Resolve an immutable binding, atomically creating it on first login."""

    def resolve_or_bind(self, identity: VerifiedIdentity) -> UserContext:
        """Return the active internal user context for verified claims."""
        ...


class Authenticator:
    """Resolve a bearer credential into a server-controlled user context."""

    def __init__(self, verifier: TokenVerifier, identities: IdentityStore) -> None:
        self._verifier = verifier
        self._identities = identities

    def authenticate_bearer(self, authorization: str | None) -> UserContext:
        """Verify a strict Bearer header and resolve its immutable binding."""
        if authorization is None:
            raise InvalidTokenError("Missing bearer token")

        scheme, separator, token = authorization.partition(" ")
        if separator != " " or scheme.casefold() != "bearer" or not token.strip():
            raise InvalidTokenError("Malformed bearer token")
        if token != token.strip() or " " in token:
            raise InvalidTokenError("Malformed bearer token")

        return self._identities.resolve_or_bind(self._verifier.verify(token))


def normalize_email(email: str) -> str:
    """Normalize the invitation lookup key without making it an identity key."""
    normalized = email.strip().casefold()
    local, separator, domain = normalized.partition("@")
    if not separator or not local or not domain or "@" in domain:
        raise ValueError("A valid email address is required")
    return normalized


def first_login_invitation_email(identity: VerifiedIdentity) -> str:
    """Return a safe invitation key from verified first-login claims."""
    if not identity.email_verified:
        raise EmailNotVerifiedError("Verified email is required for first login")
    if identity.email is None:
        raise UserNotAllowedError("No invitation email claim")
    try:
        return normalize_email(identity.email)
    except ValueError as error:
        raise InvalidTokenError("OIDC token has an invalid email claim") from error


def assert_no_caller_identity_claims(payload: object) -> None:
    """Reject tenant, dataset, and ownership selection anywhere in caller JSON."""
    pending: list[tuple[object, int]] = [(payload, 0)]

    while pending:
        current, depth = pending.pop()
        if depth > _MAX_CALLER_PAYLOAD_DEPTH:
            raise CallerIdentityClaimError("Caller payload exceeds maximum nesting depth")

        if isinstance(current, Mapping):
            for key, value in current.items():
                if isinstance(key, str) and key.casefold() in _FORBIDDEN_CALLER_KEYS:
                    raise CallerIdentityClaimError(
                        f"Caller-controlled identity field is forbidden: {key}"
                    )
                pending.append((value, depth + 1))
            continue

        if isinstance(current, Sequence) and not isinstance(current, (str, bytes, bytearray)):
            pending.extend((value, depth + 1) for value in current)


def authorize_trusted_owner(context: UserContext, trusted_owner_user_id: str) -> None:
    """Enforce ownership obtained from trusted platform storage."""
    if context.user_id != trusted_owner_user_id:
        raise CrossUserAccessError("Resource is outside the authenticated user boundary")
