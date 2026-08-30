"""Server-side OIDC login and opaque browser sessions for onboarding."""

from __future__ import annotations

import secrets
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from hmac import compare_digest
from typing import Any, Protocol, cast
from urllib.parse import urlencode, urlsplit

import httpx2
from authlib.oauth2 import OAuth2Client
from authlib.oauth2.auth import ClientAuth
from authlib.oauth2.rfc6749.errors import OAuth2Error
from google.cloud import firestore
from google.cloud.firestore_v1 import Client
from google.cloud.firestore_v1.transaction import Transaction
from tesla_personal_platform.auth import (
    InvalidTokenError,
    OIDCAccessTokenVerifier,
    OIDCIDTokenVerifier,
    UserContext,
    VerifiedIdentity,
)
from tesla_personal_platform.auth.core import IdentityStore

LOGIN_STATE_LIFETIME = timedelta(minutes=10)
WEB_SESSION_LIFETIME = timedelta(hours=12)
LOGIN_STATES_COLLECTION = "platform_login_states"
WEB_SESSIONS_COLLECTION = "platform_web_sessions"


class BrowserAuthenticationError(Exception):
    """Safe browser authentication failure."""


@dataclass(frozen=True, slots=True)
class BrowserOIDCConfig:
    """Trusted browser OAuth client configuration."""

    issuer: str
    audience: str
    client_id: str
    client_secret: str = field(repr=False)
    redirect_uri: str
    scopes: tuple[str, ...] = ("openid", "email", "profile", "mcp:access")

    def __post_init__(self) -> None:
        issuer = self.issuer.rstrip("/") + "/"
        object.__setattr__(self, "issuer", issuer)
        for value, label in ((issuer, "issuer"), (self.redirect_uri, "redirect URI")):
            parsed = urlsplit(value)
            if parsed.scheme != "https" or not parsed.netloc:
                raise ValueError(f"OIDC {label} must be an HTTPS URL")
        if not self.audience or not self.client_id or not self.client_secret:
            raise ValueError("OIDC browser client configuration is incomplete")

    @property
    def authorization_endpoint(self) -> str:
        return f"{self.issuer}authorize"

    @property
    def token_endpoint(self) -> str:
        return f"{self.issuer}oauth/token"


@dataclass(frozen=True, slots=True)
class PendingBrowserLogin:
    code_verifier: str = field(repr=False)
    nonce: str = field(repr=False)
    browser_binding_hash: str = field(repr=False)
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class BrowserSession:
    issuer: str
    subject: str
    csrf_token: str = field(repr=False)
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class BrowserLoginStart:
    authorization_url: str = field(repr=False)
    browser_binding_token: str = field(repr=False)


@dataclass(frozen=True, slots=True)
class BrowserLoginResult:
    session_token: str = field(repr=False)
    csrf_token: str = field(repr=False)
    context: UserContext


class BrowserAuthStore(Protocol):
    def create_login(self, state: str, login: PendingBrowserLogin) -> None: ...

    def consume_login(self, state: str, *, now: datetime) -> PendingBrowserLogin: ...

    def create_session(self, token: str, session: BrowserSession) -> None: ...

    def get_session(self, token: str, *, now: datetime) -> BrowserSession: ...

    def delete_session(self, token: str) -> None: ...


class BrowserTokenClient(Protocol):
    def exchange_code(self, code: str, *, code_verifier: str) -> Mapping[str, Any]: ...


class OIDCBrowserTokenClient:
    """Use Authlib protocol handling over a pooled, no-redirect HTTPX2 client."""

    def __init__(
        self,
        config: BrowserOIDCConfig,
        *,
        timeout_seconds: float = 10.0,
        client: httpx2.Client | None = None,
    ) -> None:
        self._config = config
        self._owns_client = client is None
        self._session = _Httpx2OAuthSession(
            client
            or httpx2.Client(
                timeout=timeout_seconds,
                follow_redirects=False,
                trust_env=False,
            )
        )

    def close(self) -> None:
        if self._owns_client:
            self._session.close()

    def authorization_url(
        self,
        *,
        state: str,
        nonce: str,
        code_verifier: str,
    ) -> str:
        client = self._oauth_client()
        url, _ = client.create_authorization_url(
            self._config.authorization_endpoint,
            state=state,
            nonce=nonce,
            audience=self._config.audience,
            code_verifier=code_verifier,
        )
        return url

    def exchange_code(self, code: str, *, code_verifier: str) -> Mapping[str, Any]:
        try:
            payload = self._oauth_client().fetch_token(
                self._config.token_endpoint,
                grant_type="authorization_code",
                code=code,
                code_verifier=code_verifier,
                redirect_uri=self._config.redirect_uri,
            )
        except (OAuth2Error, httpx2.RequestError, httpx2.HTTPStatusError, ValueError) as error:
            raise BrowserAuthenticationError("Platform sign-in could not be completed") from error
        if not isinstance(payload, dict):
            raise BrowserAuthenticationError("Platform token response is invalid")
        return cast(Mapping[str, Any], payload)

    def _oauth_client(self) -> OAuth2Client:
        return OAuth2Client(
            self._session,
            client_id=self._config.client_id,
            client_secret=self._config.client_secret,
            token_endpoint_auth_method="client_secret_post",  # noqa: S106 - Authlib method ID
            scope=self._config.scopes,
            redirect_uri=self._config.redirect_uri,
            code_challenge_method="S256",
        )


class _Httpx2OAuthSession:
    """Minimal Authlib session adapter that retains HTTPX2 transport guarantees."""

    def __init__(self, client: httpx2.Client) -> None:
        self._client = client

    def post(
        self,
        url: str,
        *,
        data: Mapping[str, object],
        headers: Mapping[str, str] | None = None,
        auth: ClientAuth | None = None,
        **kwargs: object,
    ) -> httpx2.Response:
        del kwargs
        request_headers = dict(headers or {})
        body = urlencode(data)
        method = "POST"
        if auth is not None:
            url, request_headers, body = auth.prepare(  # type: ignore[no-untyped-call]
                method,
                url,
                request_headers,
                body,
            )
        response = self._client.request(
            method,
            url,
            content=body.encode(),
            headers=request_headers,
        )
        if response.status_code >= 500:
            response.raise_for_status()
        return response

    def close(self) -> None:
        self._client.close()


class BrowserAuthService:
    """Bind Auth0 login to the allowlist and maintain opaque server-side sessions."""

    def __init__(
        self,
        *,
        config: BrowserOIDCConfig,
        store: BrowserAuthStore,
        identities: IdentityStore,
        access_tokens: OIDCAccessTokenVerifier,
        id_tokens: OIDCIDTokenVerifier,
        token_client: BrowserTokenClient | None = None,
    ) -> None:
        self.config = config
        self._store = store
        self._identities = identities
        self._access_tokens = access_tokens
        self._id_tokens = id_tokens
        authlib_client = OIDCBrowserTokenClient(config)
        self._authorization = authlib_client
        self._tokens = token_client or authlib_client

    def start(self, *, now: datetime | None = None) -> BrowserLoginStart:
        current = now or datetime.now(UTC)
        state = secrets.token_urlsafe(32)
        nonce = secrets.token_urlsafe(32)
        verifier = secrets.token_urlsafe(64)
        browser_binding_token = secrets.token_urlsafe(32)
        self._store.create_login(
            state,
            PendingBrowserLogin(
                verifier,
                nonce,
                _opaque_id(browser_binding_token),
                current + LOGIN_STATE_LIFETIME,
            ),
        )
        return BrowserLoginStart(
            self._authorization.authorization_url(
                state=state,
                nonce=nonce,
                code_verifier=verifier,
            ),
            browser_binding_token,
        )

    def complete(
        self,
        *,
        state: str,
        code: str,
        browser_binding_token: str,
        now: datetime | None = None,
    ) -> BrowserLoginResult:
        current = now or datetime.now(UTC)
        pending = self._store.consume_login(state, now=current)
        _require_browser_binding(pending, browser_binding_token)
        tokens = self._tokens.exchange_code(code, code_verifier=pending.code_verifier)
        access_token = _required_token(tokens, "access_token")
        id_token = _required_token(tokens, "id_token")
        access_identity = self._access_tokens.verify(access_token)
        identity = self._id_tokens.verify(id_token, nonce=pending.nonce)
        if (access_identity.issuer, access_identity.subject) != (
            identity.issuer,
            identity.subject,
        ):
            raise BrowserAuthenticationError("Platform token identities do not match")
        context = self._identities.resolve_or_bind(identity)
        session_token = secrets.token_urlsafe(48)
        csrf_token = secrets.token_urlsafe(32)
        self._store.create_session(
            session_token,
            BrowserSession(
                identity.issuer, identity.subject, csrf_token, current + WEB_SESSION_LIFETIME
            ),
        )
        return BrowserLoginResult(session_token, csrf_token, context)

    def cancel(
        self,
        state: str,
        *,
        browser_binding_token: str,
        now: datetime | None = None,
    ) -> None:
        """Consume a denied login state so it cannot be replayed."""
        pending = self._store.consume_login(state, now=now or datetime.now(UTC))
        _require_browser_binding(pending, browser_binding_token)

    def authenticate_session(
        self,
        token: str,
        *,
        now: datetime | None = None,
    ) -> tuple[UserContext, BrowserSession]:
        session = self._store.get_session(token, now=now or datetime.now(UTC))
        context = self._identities.resolve_or_bind(
            VerifiedIdentity(session.issuer, session.subject)
        )
        return context, session

    def logout(self, token: str) -> None:
        self._store.delete_session(token)

    def close(self) -> None:
        self._authorization.close()

    @staticmethod
    def session_binding(token: str) -> str:
        """Return the non-reversible binding stored with browser-started Tesla OAuth."""
        return _opaque_id(token)


class FirestoreBrowserAuthStore:
    """Persist one-time login state and hashed opaque sessions in Firestore."""

    def __init__(self, client: Client) -> None:
        self.client = client

    def create_login(self, state: str, login: PendingBrowserLogin) -> None:
        self.client.collection(LOGIN_STATES_COLLECTION).document(_opaque_id(state)).create(
            {
                "code_verifier": login.code_verifier,
                "nonce": login.nonce,
                "browser_binding_hash": login.browser_binding_hash,
                "expires_at": login.expires_at,
                "created_at": firestore.SERVER_TIMESTAMP,
            }
        )

    def consume_login(self, state: str, *, now: datetime) -> PendingBrowserLogin:
        transaction = self.client.transaction()
        return cast(PendingBrowserLogin, _consume_login(transaction, self, state, now))

    def create_session(self, token: str, session: BrowserSession) -> None:
        self.client.collection(WEB_SESSIONS_COLLECTION).document(_opaque_id(token)).create(
            {
                "issuer": session.issuer,
                "subject": session.subject,
                "csrf_token": session.csrf_token,
                "expires_at": session.expires_at,
                "created_at": firestore.SERVER_TIMESTAMP,
            }
        )

    def get_session(self, token: str, *, now: datetime) -> BrowserSession:
        reference = self.client.collection(WEB_SESSIONS_COLLECTION).document(_opaque_id(token))
        snapshot = reference.get()
        if not snapshot.exists:
            raise InvalidTokenError("Browser session is not valid")
        session = _session_from_data(snapshot.to_dict())
        if session.expires_at <= now:
            reference.delete()
            raise InvalidTokenError("Browser session has expired")
        return session

    def delete_session(self, token: str) -> None:
        self.client.collection(WEB_SESSIONS_COLLECTION).document(_opaque_id(token)).delete()


@firestore.transactional
def _consume_login(
    transaction: Transaction,
    store: FirestoreBrowserAuthStore,
    state: str,
    now: datetime,
) -> PendingBrowserLogin:
    reference = store.client.collection(LOGIN_STATES_COLLECTION).document(_opaque_id(state))
    snapshot = reference.get(transaction=transaction)
    if not snapshot.exists:
        raise BrowserAuthenticationError("Platform login state is invalid")
    transaction.delete(reference)
    data = snapshot.to_dict()
    if not isinstance(data, dict):
        raise BrowserAuthenticationError("Platform login state is invalid")
    verifier = data.get("code_verifier")
    nonce = data.get("nonce")
    browser_binding_hash = data.get("browser_binding_hash")
    expires_at = data.get("expires_at")
    if (
        not isinstance(verifier, str)
        or not isinstance(nonce, str)
        or not isinstance(browser_binding_hash, str)
        or not isinstance(expires_at, datetime)
    ):
        raise BrowserAuthenticationError("Platform login state is invalid")
    if expires_at <= now:
        raise BrowserAuthenticationError("Platform login state has expired")
    return PendingBrowserLogin(verifier, nonce, browser_binding_hash, expires_at)


def _session_from_data(data: Mapping[str, Any] | None) -> BrowserSession:
    if data is None:
        raise BrowserAuthenticationError("Browser session is invalid")
    issuer = data.get("issuer")
    subject = data.get("subject")
    csrf_token = data.get("csrf_token")
    expires_at = data.get("expires_at")
    if not isinstance(issuer, str) or not issuer:
        raise BrowserAuthenticationError("Browser session is invalid")
    if not isinstance(subject, str) or not subject:
        raise BrowserAuthenticationError("Browser session is invalid")
    if not isinstance(csrf_token, str) or not csrf_token:
        raise BrowserAuthenticationError("Browser session is invalid")
    if not isinstance(expires_at, datetime):
        raise BrowserAuthenticationError("Browser session is invalid")
    return BrowserSession(issuer, subject, csrf_token, expires_at)


def _required_token(tokens: Mapping[str, Any], name: str) -> str:
    value = tokens.get(name)
    if not isinstance(value, str) or not value:
        raise BrowserAuthenticationError("Platform token response is incomplete")
    return value


def _require_browser_binding(login: PendingBrowserLogin, token: str) -> None:
    if not token or not compare_digest(login.browser_binding_hash, _opaque_id(token)):
        raise BrowserAuthenticationError("Platform login browser binding is invalid")


def _opaque_id(value: str) -> str:
    return sha256(value.encode()).hexdigest()
