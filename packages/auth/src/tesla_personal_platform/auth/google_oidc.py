"""Google OIDC token verification behind the provider-neutral verifier boundary."""

from google.auth.exceptions import GoogleAuthError
from google.auth.transport.requests import Request
from google.oauth2 import id_token
from tesla_personal_platform.auth.errors import InvalidTokenError
from tesla_personal_platform.auth.models import VerifiedIdentity

GOOGLE_CANONICAL_ISSUER = "https://accounts.google.com"
GOOGLE_ISSUERS = frozenset({"accounts.google.com", GOOGLE_CANONICAL_ISSUER})


class GoogleOIDCVerifier:
    """Verify Google-signed ID tokens for one configured OAuth client audience."""

    def __init__(self, audience: str, request: Request | None = None) -> None:
        if not audience.strip():
            raise ValueError("OIDC audience is required")
        self._audience = audience
        self._request = request or Request()

    def verify(self, token: str) -> VerifiedIdentity:
        """Validate signature, expiry, audience, issuer, and required identity claims."""
        try:
            claims = id_token.verify_oauth2_token(  # type: ignore[no-untyped-call]
                token,
                self._request,
                self._audience,
            )
        except (GoogleAuthError, ValueError) as error:
            raise InvalidTokenError("OIDC token verification failed") from error

        issuer = claims.get("iss")
        subject = claims.get("sub")
        if not isinstance(issuer, str) or issuer not in GOOGLE_ISSUERS:
            raise InvalidTokenError("OIDC issuer is not trusted")
        if not isinstance(subject, str) or not subject:
            raise InvalidTokenError("OIDC subject is missing")

        email_claim = claims.get("email")
        email = email_claim if isinstance(email_claim, str) else None
        return VerifiedIdentity(
            issuer=GOOGLE_CANONICAL_ISSUER,
            subject=subject,
            email=email,
            email_verified=claims.get("email_verified") is True,
        )
