"""OIDC JWT verification for MCP resource access and browser sign-in."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any, Protocol, cast
from urllib.parse import urlsplit

import jwt
from jwt import PyJWKClient
from jwt.exceptions import PyJWTError
from tesla_personal_platform.auth.errors import InvalidTokenError
from tesla_personal_platform.auth.models import VerifiedIdentity


class ClaimsDecoder(Protocol):
    """Decode and cryptographically verify one JWT."""

    def __call__(self, token: str, audience: str) -> Mapping[str, Any]: ...


class OIDCJWTDecoder:
    """Validate RS256 tokens against one exact issuer and its JWKS."""

    def __init__(
        self,
        issuer: str,
        jwks_url: str | None = None,
        *,
        key_resolver: Callable[[str], Any] | None = None,
    ) -> None:
        self.issuer = _https_issuer(issuer)
        resolved_jwks = jwks_url or f"{self.issuer}.well-known/jwks.json"
        _require_https_url(resolved_jwks, "OIDC JWKS URL")
        if key_resolver is None:
            keys = PyJWKClient(resolved_jwks, cache_keys=True, lifespan=300, timeout=5)
            self._resolve_key: Callable[[str], Any] = lambda token: (
                keys.get_signing_key_from_jwt(token).key
            )
        else:
            self._resolve_key = key_resolver

    def __call__(self, token: str, audience: str) -> Mapping[str, Any]:
        try:
            key = self._resolve_key(token)
            claims = jwt.decode(
                token,
                key=key,
                algorithms=["RS256"],
                audience=audience,
                issuer=self.issuer,
                options={"require": ["aud", "exp", "iat", "iss", "sub"]},
            )
        except (PyJWTError, ValueError) as error:
            raise InvalidTokenError("OIDC token verification failed") from error
        return cast(Mapping[str, Any], claims)


class OIDCAccessTokenVerifier:
    """Verify an OAuth access token for the MCP resource and required scopes."""

    def __init__(
        self,
        *,
        issuer: str,
        audience: str,
        required_scopes: frozenset[str],
        decoder: ClaimsDecoder | None = None,
    ) -> None:
        if not audience.strip():
            raise ValueError("OIDC audience is required")
        self._issuer = _https_issuer(issuer)
        self._audience = audience
        self._required_scopes = required_scopes
        self._decoder = decoder or OIDCJWTDecoder(self._issuer)

    def verify(self, token: str) -> VerifiedIdentity:
        claims = self._decoder(token, self._audience)
        granted = _scopes(claims)
        if not self._required_scopes.issubset(granted):
            raise InvalidTokenError("OIDC token is missing required scopes")

        # Existing authorization resolves solely from the immutable issuer/subject
        # binding. Email is required only by the identity store when creating the
        # first binding, which the browser flow performs from its verified ID token.
        return _identity(claims, expected_issuer=self._issuer)


class OIDCIDTokenVerifier:
    """Verify a browser-flow ID token, including its per-login nonce."""

    def __init__(
        self,
        *,
        issuer: str,
        audience: str,
        decoder: ClaimsDecoder | None = None,
    ) -> None:
        if not audience.strip():
            raise ValueError("OIDC client audience is required")
        self._issuer = _https_issuer(issuer)
        self._audience = audience
        self._decoder = decoder or OIDCJWTDecoder(self._issuer)

    def verify(self, token: str, *, nonce: str) -> VerifiedIdentity:
        claims = self._decoder(token, self._audience)
        if claims.get("nonce") != nonce:
            raise InvalidTokenError("OIDC ID token nonce does not match")
        return _identity(claims, expected_issuer=self._issuer)


def _identity(
    claims: Mapping[str, Any],
    *,
    expected_issuer: str,
) -> VerifiedIdentity:
    issuer = claims.get("iss")
    if issuer != expected_issuer:
        raise InvalidTokenError("OIDC issuer is not trusted")
    subject = claims.get("sub")
    if not isinstance(subject, str) or not subject:
        raise InvalidTokenError("OIDC subject is missing")
    email = claims.get("email")
    if email is not None and not isinstance(email, str):
        raise InvalidTokenError("OIDC email claim is invalid")
    email_verified = claims.get("email_verified", False)
    if not isinstance(email_verified, bool):
        raise InvalidTokenError("OIDC email verification claim is invalid")
    return VerifiedIdentity(issuer, subject, email, email_verified)


def _scopes(claims: Mapping[str, Any]) -> frozenset[str]:
    scope = claims.get("scope", "")
    if not isinstance(scope, str):
        raise InvalidTokenError("OIDC scope claim is invalid")
    permissions = claims.get("permissions", [])
    if not isinstance(permissions, list) or not all(
        isinstance(value, str) for value in permissions
    ):
        raise InvalidTokenError("OIDC permissions claim is invalid")
    return frozenset(scope.split()) | frozenset(permissions)


def _https_issuer(value: str) -> str:
    issuer = value.strip()
    _require_https_url(issuer, "OIDC issuer")
    return issuer.rstrip("/") + "/"


def _require_https_url(value: str, label: str) -> None:
    parsed = urlsplit(value)
    if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password:
        raise ValueError(f"{label} must be an HTTPS URL")
    if "\r" in value or "\n" in value:
        raise ValueError(f"{label} is invalid")
