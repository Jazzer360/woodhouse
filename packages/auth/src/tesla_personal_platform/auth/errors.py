"""Safe authentication and authorization failure categories."""


class AuthenticationError(Exception):
    """Base class for failures that must not expose credentials or user records."""


class InvalidTokenError(AuthenticationError):
    """The bearer token is missing, malformed, expired, or unverifiable."""


class EmailNotVerifiedError(AuthenticationError):
    """A first-login invitation cannot bind an unverified email address."""


class UserNotAllowedError(AuthenticationError):
    """No invitation or immutable identity binding authorizes the caller."""


class UserDisabledError(AuthenticationError):
    """The bound or invited user is disabled."""


class IdentityMismatchError(AuthenticationError):
    """An invitation is already bound to a different immutable identity."""


class ConfigurationError(AuthenticationError):
    """Trusted platform state is incomplete or inconsistent."""


class CallerIdentityClaimError(AuthenticationError):
    """A request tried to select its own tenant or ownership boundary."""


class CrossUserAccessError(AuthenticationError):
    """A trusted resource belongs to a different internal user."""
