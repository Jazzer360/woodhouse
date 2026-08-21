"""Thread-safe in-memory identity store for unit tests and local fakes."""

from threading import RLock

from tesla_personal_platform.auth.core import normalize_email
from tesla_personal_platform.auth.errors import (
    ConfigurationError,
    EmailNotVerifiedError,
    IdentityMismatchError,
    UserDisabledError,
    UserNotAllowedError,
)
from tesla_personal_platform.auth.models import (
    AllowedUser,
    UserContext,
    UserStatus,
    VerifiedIdentity,
)


class InMemoryIdentityStore:
    """Faithful fake of first-login binding behavior."""

    def __init__(self, users: list[AllowedUser] | None = None) -> None:
        self._users = {normalize_email(user.invitation_email): user for user in (users or [])}
        self._bindings: dict[tuple[str, str], str] = {}
        self._lock = RLock()
        for email, user in self._users.items():
            if user.oidc_issuer is not None and user.oidc_subject is not None:
                self._bindings[(user.oidc_issuer, user.oidc_subject)] = email

    def resolve_or_bind(self, identity: VerifiedIdentity) -> UserContext:
        """Resolve by immutable identity first, or bind an active invitation once."""
        with self._lock:
            binding_key = (identity.issuer, identity.subject)
            bound_email = self._bindings.get(binding_key)
            if bound_email is not None:
                return self._context(self._users[bound_email], identity)

            if not identity.email_verified:
                raise EmailNotVerifiedError("Verified email is required for first login")
            if identity.email is None:
                raise UserNotAllowedError("No invitation email claim")

            email = normalize_email(identity.email)
            user = self._users.get(email)
            if user is None:
                raise UserNotAllowedError("No active invitation")
            self._require_active(user)

            if user.oidc_issuer is not None or user.oidc_subject is not None:
                if (user.oidc_issuer, user.oidc_subject) != binding_key:
                    raise IdentityMismatchError("Invitation is bound to another identity")
            else:
                user = AllowedUser(
                    invitation_email=user.invitation_email,
                    user_id=user.user_id,
                    dataset_id=user.dataset_id,
                    status=user.status,
                    oidc_issuer=identity.issuer,
                    oidc_subject=identity.subject,
                )
                self._users[email] = user
                self._bindings[binding_key] = email

            return self._context(user, identity)

    def replace_user(self, user: AllowedUser) -> None:
        """Replace trusted user state for an administrative test action."""
        with self._lock:
            email = normalize_email(user.invitation_email)
            old_user = self._users.get(email)
            if old_user is not None and old_user.oidc_issuer and old_user.oidc_subject:
                self._bindings.pop((old_user.oidc_issuer, old_user.oidc_subject), None)
            self._users[email] = user
            if user.oidc_issuer and user.oidc_subject:
                self._bindings[(user.oidc_issuer, user.oidc_subject)] = email

    def get_user(self, email: str) -> AllowedUser | None:
        """Return a trusted record for assertions."""
        with self._lock:
            return self._users.get(normalize_email(email))

    @staticmethod
    def _require_active(user: AllowedUser) -> None:
        if user.status is not UserStatus.ACTIVE:
            raise UserDisabledError("User is disabled")

    @classmethod
    def _context(cls, user: AllowedUser, identity: VerifiedIdentity) -> UserContext:
        cls._require_active(user)
        if not user.user_id or not user.dataset_id:
            raise ConfigurationError("Allowlist record is missing tenant configuration")
        if (user.oidc_issuer, user.oidc_subject) != (identity.issuer, identity.subject):
            raise IdentityMismatchError("Immutable identity binding is inconsistent")
        return UserContext(
            user_id=user.user_id,
            dataset_id=user.dataset_id,
            oidc_issuer=identity.issuer,
            oidc_subject=identity.subject,
        )
