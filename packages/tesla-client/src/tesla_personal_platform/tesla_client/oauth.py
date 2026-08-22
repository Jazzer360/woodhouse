"""Tesla third-party OAuth authorization-code and token-rotation support."""

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Protocol
from urllib.parse import urlencode

import jwt
from tesla_personal_platform.tesla_client.errors import (
    TeslaAuthenticationError,
    TeslaConfigurationError,
    TeslaReauthorizationRequired,
)
from tesla_personal_platform.tesla_client.models import TokenSet
from tesla_personal_platform.tesla_client.transport import HttpResponse, HttpTransport

TESLA_AUTHORIZE_URL = "https://auth.tesla.com/oauth2/v3/authorize"
TESLA_TOKEN_URL = "https://fleet-auth.prd.vn.cloud.tesla.com/oauth2/v3/token"  # noqa: S105
TESLA_OIDC_ISSUER = "https://auth.tesla.com/oauth2/v3/nts"
TESLA_JWKS_URL = "https://auth.tesla.com/oauth2/v3/discovery/thirdparty/keys"
DEFAULT_SCOPES = (
    "openid",
    "offline_access",
    "vehicle_device_data",
    "vehicle_location",
    "vehicle_cmds",
    "vehicle_charging_cmds",
    "user_data",
)


@dataclass(frozen=True, slots=True)
class TeslaOAuthConfig:
    """Non-secret OAuth settings plus the runtime-injected client secret."""

    client_id: str
    client_secret: str = field(repr=False)
    redirect_uri: str
    audience: str
    scopes: tuple[str, ...] = DEFAULT_SCOPES

    def __post_init__(self) -> None:
        for name, value in {
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "redirect_uri": self.redirect_uri,
            "audience": self.audience,
        }.items():
            if not value.strip():
                raise TeslaConfigurationError(f"Tesla OAuth {name} is required")


class IDTokenVerifier(Protocol):
    """Validate Tesla's OIDC ID token and return its immutable subject."""

    def verify(self, token: str, *, nonce: str, audience: str) -> str:
        """Verify signature and registered claims including nonce."""
        ...


class TeslaIDTokenVerifier:
    """Verify Tesla RS256 ID tokens against Tesla's current JWKS endpoint."""

    def __init__(self, jwks_url: str = TESLA_JWKS_URL) -> None:
        self._jwks = jwt.PyJWKClient(jwks_url, cache_jwk_set=True, lifespan=300)

    def verify(self, token: str, *, nonce: str, audience: str) -> str:
        try:
            signing_key = self._jwks.get_signing_key_from_jwt(token)
            claims = jwt.decode(
                token,
                signing_key.key,
                algorithms=["RS256"],
                audience=audience,
                issuer=TESLA_OIDC_ISSUER,
                options={"require": ["exp", "iat", "iss", "sub", "aud", "nonce"]},
            )
        except jwt.PyJWTError as error:
            raise TeslaAuthenticationError("Tesla ID token verification failed") from error
        if claims.get("nonce") != nonce:
            raise TeslaAuthenticationError("Tesla ID token nonce mismatch")
        subject = claims.get("sub")
        if not isinstance(subject, str) or not subject:
            raise TeslaAuthenticationError("Tesla ID token subject is missing")
        return subject


class TeslaOAuthClient:
    """Construct authorization URLs and exchange rotating credentials."""

    def __init__(
        self,
        config: TeslaOAuthConfig,
        transport: HttpTransport,
        id_tokens: IDTokenVerifier,
    ) -> None:
        self.config = config
        self._transport = transport
        self._id_tokens = id_tokens

    def authorization_url(self, *, state: str, nonce: str) -> str:
        parameters = {
            "response_type": "code",
            "client_id": self.config.client_id,
            "redirect_uri": self.config.redirect_uri,
            "scope": " ".join(self.config.scopes),
            "state": state,
            "nonce": nonce,
            "prompt_missing_scopes": "true",
            "require_requested_scopes": "true",
            "show_keypair_step": "true",
        }
        return f"{TESLA_AUTHORIZE_URL}?{urlencode(parameters)}"

    def exchange_code(self, code: str, *, nonce: str) -> TokenSet:
        response = self._transport.request(
            "POST",
            TESLA_TOKEN_URL,
            form={
                "grant_type": "authorization_code",
                "client_id": self.config.client_id,
                "client_secret": self.config.client_secret,
                "code": code,
                "audience": self.config.audience,
                "redirect_uri": self.config.redirect_uri,
            },
        )
        document = _successful_token_document(response)
        id_token = _required_string(document, "id_token")
        subject = self._id_tokens.verify(
            id_token,
            nonce=nonce,
            audience=self.config.client_id,
        )
        return _token_set(document, subject=subject, fallback_scopes=self.config.scopes)

    def refresh(self, refresh_token: str, *, tesla_subject: str) -> TokenSet:
        response = self._transport.request(
            "POST",
            TESLA_TOKEN_URL,
            form={
                "grant_type": "refresh_token",
                "client_id": self.config.client_id,
                "refresh_token": refresh_token,
            },
        )
        document = _successful_token_document(response)
        return _token_set(document, subject=tesla_subject, fallback_scopes=self.config.scopes)


def _successful_token_document(response: HttpResponse) -> Mapping[str, object]:
    try:
        document = response.json()
    except (UnicodeDecodeError, ValueError) as error:
        raise TeslaAuthenticationError("Tesla token endpoint returned invalid JSON") from error
    if not isinstance(document, Mapping):
        raise TeslaAuthenticationError("Tesla token endpoint returned an invalid document")
    error_code = document.get("error")
    if response.status == 401 and error_code == "login_required":
        raise TeslaReauthorizationRequired("Tesla authorization must be renewed")
    if response.status < 200 or response.status >= 300:
        raise TeslaAuthenticationError("Tesla token exchange was rejected")
    return document


def _required_string(document: Mapping[str, object], key: str) -> str:
    value = document.get(key)
    if not isinstance(value, str) or not value:
        raise TeslaAuthenticationError(f"Tesla token response is missing {key}")
    return value


def _token_set(
    document: Mapping[str, object],
    *,
    subject: str,
    fallback_scopes: tuple[str, ...],
) -> TokenSet:
    expires_in = document.get("expires_in")
    if not isinstance(expires_in, (int, float)) or isinstance(expires_in, bool) or expires_in <= 0:
        raise TeslaAuthenticationError("Tesla token response has invalid expiry")
    scope = document.get("scope")
    scopes = tuple(scope.split()) if isinstance(scope, str) and scope.strip() else fallback_scopes
    return TokenSet(
        access_token=_required_string(document, "access_token"),
        refresh_token=_required_string(document, "refresh_token"),
        expires_at=datetime.now(UTC) + timedelta(seconds=float(expires_in)),
        scopes=scopes,
        tesla_subject=subject,
    )
