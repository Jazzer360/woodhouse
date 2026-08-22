"""Provider-neutral platform identity models."""

from dataclasses import dataclass
from enum import StrEnum


class UserStatus(StrEnum):
    """Administrative access state for an allowlisted user."""

    ACTIVE = "active"
    DISABLED = "disabled"


@dataclass(frozen=True, slots=True)
class VerifiedIdentity:
    """Claims returned only after cryptographic OIDC verification."""

    issuer: str
    subject: str
    email: str | None = None
    email_verified: bool = False

    def __post_init__(self) -> None:
        if not self.issuer or not self.subject:
            raise ValueError("Verified OIDC identity requires issuer and subject")


@dataclass(frozen=True, slots=True)
class AllowedUser:
    """Trusted allowlist state persisted by the platform."""

    invitation_email: str
    user_id: str
    dataset_id: str
    status: UserStatus
    oidc_issuer: str | None = None
    oidc_subject: str | None = None


@dataclass(frozen=True, slots=True)
class UserContext:
    """Server-derived tenant context for one authenticated request."""

    user_id: str
    dataset_id: str
    oidc_issuer: str
    oidc_subject: str
