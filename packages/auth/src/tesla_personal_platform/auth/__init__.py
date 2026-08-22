"""Platform identity and tenant-authorization boundary."""

from tesla_personal_platform.auth.core import (
    Authenticator,
    IdentityStore,
    TokenVerifier,
    assert_no_caller_identity_claims,
    authorize_trusted_owner,
    normalize_email,
)
from tesla_personal_platform.auth.errors import (
    AuthenticationError,
    CallerIdentityClaimError,
    ConfigurationError,
    CrossUserAccessError,
    EmailNotVerifiedError,
    IdentityMismatchError,
    InvalidTokenError,
    UserDisabledError,
    UserNotAllowedError,
)
from tesla_personal_platform.auth.models import (
    AllowedUser,
    UserContext,
    UserStatus,
    VerifiedIdentity,
)

COMPONENT = "auth"

__all__ = [
    "COMPONENT",
    "AllowedUser",
    "AuthenticationError",
    "Authenticator",
    "CallerIdentityClaimError",
    "ConfigurationError",
    "CrossUserAccessError",
    "EmailNotVerifiedError",
    "IdentityMismatchError",
    "IdentityStore",
    "InvalidTokenError",
    "TokenVerifier",
    "UserContext",
    "UserDisabledError",
    "UserNotAllowedError",
    "UserStatus",
    "VerifiedIdentity",
    "assert_no_caller_identity_claims",
    "authorize_trusted_owner",
    "normalize_email",
]
