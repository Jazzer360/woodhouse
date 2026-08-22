"""Authenticated gateway with Phase 4 Tesla onboarding routes."""

import json
import logging
import os
from collections.abc import Callable
from http import HTTPStatus
from http.cookies import CookieError, SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from time import monotonic
from typing import Final, Protocol, cast
from urllib.parse import parse_qs, urlsplit

from google.cloud import firestore
from tesla_personal_platform.auth import (
    AuthenticationError,
    Authenticator,
    CallerIdentityClaimError,
    ConfigurationError,
    CrossUserAccessError,
    OIDCAccessTokenVerifier,
    OIDCIDTokenVerifier,
    UserContext,
    assert_no_caller_identity_claims,
)
from tesla_personal_platform.auth.firestore import FirestoreIdentityStore
from tesla_personal_platform.auth.google_oidc import GoogleOIDCVerifier
from tesla_personal_platform.mcp_gateway import SERVICE_NAME
from tesla_personal_platform.mcp_gateway.auth_boundary import GatewayAuthBoundary
from tesla_personal_platform.mcp_gateway.browser_auth import (
    WEB_SESSION_LIFETIME,
    BrowserAuthenticationError,
    BrowserAuthService,
    BrowserOIDCConfig,
    BrowserSession,
    FirestoreBrowserAuthStore,
)
from tesla_personal_platform.mcp_gateway.mcp_auth import MCP_ACCESS_SCOPE, MCPAuthorizationSettings
from tesla_personal_platform.mcp_gateway.onboarding_web import error_page, onboarding_page
from tesla_personal_platform.mcp_gateway.tesla_onboarding import (
    InvalidOAuthStateError,
    TeslaOnboardingError,
)
from tesla_personal_platform.mcp_gateway.tesla_runtime import TeslaRuntime, build_tesla_runtime
from tesla_personal_platform.tesla_client import (
    TeslaAPIError,
    TeslaAuthenticationError,
    TeslaReauthorizationRequired,
)

DEFAULT_HOST: Final = "127.0.0.1"
DEFAULT_PORT: Final = 8080
HEALTH_PATHS: Final = frozenset({"/health", "/healthz"})
TESLA_PUBLIC_KEY_PATH: Final = "/.well-known/appspecific/com.tesla.3p.public-key.pem"
MAX_REQUEST_BYTES: Final = 1_048_576
REQUEST_IDLE_TIMEOUT_SECONDS: Final = 15.0
REQUEST_BODY_TIMEOUT_SECONDS: Final = 15.0
SESSION_COOKIE_NAME: Final = "__Host-tpp_session"
LOGIN_COOKIE_NAME: Final = "__Host-tpp_login"
_REQUEST_READ_CHUNK_BYTES: Final = 64 * 1024
LOGGER = logging.getLogger(__name__)


class _RequestBodyReader(Protocol):
    def read1(self, size: int = -1, /) -> bytes:
        """Read at most one buffered/raw chunk."""
        ...


class _TimeoutConnection(Protocol):
    def settimeout(self, value: float | None) -> None:
        """Set the timeout for the next socket operation."""
        ...


def health_document() -> dict[str, str]:
    """Return a non-sensitive service health document."""
    return {"phase": "typed-tesla-mcp", "service": SERVICE_NAME, "status": "ok"}


def _decode_json_request(body: bytes) -> object:
    """Decode caller JSON while treating excessive nesting as a bad request."""
    try:
        return json.loads(body.decode("utf-8"))
    except RecursionError as error:
        raise ValueError("JSON request exceeds safe nesting depth") from error


def _read_request_body(
    reader: _RequestBodyReader,
    connection: _TimeoutConnection,
    length: int,
    *,
    timeout_seconds: float = REQUEST_BODY_TIMEOUT_SECONDS,
    clock: Callable[[], float] = monotonic,
) -> bytes:
    """Read exactly ``length`` bytes within one absolute body deadline."""
    deadline = clock() + timeout_seconds
    chunks: list[bytes] = []
    remaining = length

    try:
        while remaining:
            time_left = deadline - clock()
            if time_left <= 0:
                raise TimeoutError("Request body read deadline exceeded")
            connection.settimeout(time_left)
            chunk = reader.read1(min(remaining, _REQUEST_READ_CHUNK_BYTES))
            if not chunk:
                raise ValueError("Request body ended before Content-Length")
            chunks.append(chunk)
            remaining -= len(chunk)
    finally:
        connection.settimeout(REQUEST_IDLE_TIMEOUT_SECONDS)

    return b"".join(chunks)


def build_runtime() -> tuple[
    GatewayAuthBoundary,
    TeslaRuntime | None,
    MCPAuthorizationSettings | None,
    BrowserAuthService | None,
]:
    """Build platform auth and optional Tesla onboarding from runtime configuration."""
    project_id = os.environ.get("GOOGLE_CLOUD_PROJECT", "").strip()
    if not project_id:
        raise RuntimeError("GOOGLE_CLOUD_PROJECT must be configured")

    firestore_client = firestore.Client(project=project_id)
    identities = FirestoreIdentityStore(firestore_client)
    issuer = os.environ.get("PLATFORM_OIDC_ISSUER", "").strip()
    resource_url = os.environ.get("PLATFORM_OIDC_RESOURCE_URL", "").strip()
    browser_auth: BrowserAuthService | None = None
    authorization: MCPAuthorizationSettings | None = None

    if issuer:
        if not resource_url:
            raise RuntimeError("Platform OIDC resource URL must be configured")
        access_tokens = OIDCAccessTokenVerifier(
            issuer=issuer,
            audience=resource_url,
            required_scopes=frozenset({MCP_ACCESS_SCOPE}),
        )
        authenticator = Authenticator(access_tokens, identities)
        authorization = MCPAuthorizationSettings(resource_url, issuer, (MCP_ACCESS_SCOPE,))
        client_id = os.environ.get("PLATFORM_OIDC_CLIENT_ID", "").strip()
        client_secret = os.environ.get("PLATFORM_OIDC_CLIENT_SECRET", "").strip()
        redirect_uri = os.environ.get("PLATFORM_OIDC_REDIRECT_URI", "").strip()
        if client_id or client_secret or redirect_uri:
            if not all((client_id, client_secret, redirect_uri)):
                raise RuntimeError("Platform browser OIDC client configuration is incomplete")
            browser_config = BrowserOIDCConfig(
                issuer=issuer,
                audience=resource_url,
                client_id=client_id,
                client_secret=client_secret,
                redirect_uri=redirect_uri,
                scopes=("openid", "email", "profile", MCP_ACCESS_SCOPE),
            )
            browser_auth = BrowserAuthService(
                config=browser_config,
                store=FirestoreBrowserAuthStore(firestore_client),
                identities=identities,
                access_tokens=access_tokens,
                id_tokens=OIDCIDTokenVerifier(issuer=issuer, audience=client_id),
            )
    else:
        audience = os.environ.get("OIDC_AUDIENCE", "").strip()
        if not audience:
            raise RuntimeError("PLATFORM_OIDC_ISSUER or OIDC_AUDIENCE must be configured")
        authenticator = Authenticator(GoogleOIDCVerifier(audience), identities)

    return (
        GatewayAuthBoundary(authenticator),
        build_tesla_runtime(project_id, oauth_protected=authorization is not None),
        authorization,
        browser_auth,
    )


class _Handler(BaseHTTPRequestHandler):
    def setup(self) -> None:
        """Bound idle socket operations, including request-header parsing."""
        super().setup()
        self.connection.settimeout(REQUEST_IDLE_TIMEOUT_SECONDS)

    def do_GET(self) -> None:  # noqa: N802 - inherited HTTP handler API
        parsed = urlsplit(self.path)
        if parsed.path in HEALTH_PATHS:
            self._send_json(HTTPStatus.OK, health_document())
            return
        if parsed.path in {"/", "/onboarding"}:
            self._serve_onboarding(parse_qs(parsed.query))
            return
        if parsed.path in {
            "/.well-known/oauth-protected-resource",
            "/.well-known/oauth-protected-resource/mcp",
        }:
            self._serve_oauth_resource_metadata()
            return
        if parsed.path == "/auth/login":
            self._start_platform_login()
            return
        if parsed.path == "/auth/callback":
            self._complete_platform_login(parse_qs(parsed.query))
            return
        if parsed.path == TESLA_PUBLIC_KEY_PATH:
            self._serve_tesla_public_key()
            return
        if parsed.path == "/tesla/oauth/start":
            self._start_tesla_oauth()
            return
        if parsed.path == "/oauth/callback":
            self._complete_tesla_oauth(parse_qs(parsed.query))
            return
        if parsed.path == "/tesla/vehicles":
            self._list_tesla_vehicles()
            return
        self.send_error(HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:  # noqa: N802 - inherited HTTP handler API
        parsed = urlsplit(self.path)
        if parsed.path == "/auth/logout":
            self._logout_browser_session()
            return
        if parsed.path == "/onboarding/tesla/start":
            self._start_browser_tesla_oauth()
            return
        if parsed.path.startswith("/onboarding/vehicles/") and parsed.path.endswith("/refresh"):
            self._refresh_browser_pairing_status(parsed.path)
            return
        if parsed.path == "/tesla/oauth/refresh":
            self._rotate_tesla_refresh_token()
            return
        if parsed.path.startswith("/tesla/vehicles/") and parsed.path.endswith("/fleet-status"):
            self._refresh_tesla_fleet_status(parsed.path)
            return
        if parsed.path != "/mcp":
            self.send_error(HTTPStatus.NOT_FOUND)
            return

        try:
            payload = self._read_json()
            assert_no_caller_identity_claims(payload)
            server = cast(_Server, self.server)
            authorization_header = self.headers.get("Authorization")
            context: UserContext | None = None
            if authorization_header is not None or _mcp_requires_auth(payload):
                context = server.auth_boundary.authorize(authorization_header, payload)
        except TimeoutError:
            self._send_json(HTTPStatus.REQUEST_TIMEOUT, {"error": "request_timeout"})
            return
        except CallerIdentityClaimError:
            self._send_json(
                HTTPStatus.BAD_REQUEST,
                {"error": "caller_identity_fields_forbidden"},
            )
            return
        except ConfigurationError:
            self._send_json(HTTPStatus.SERVICE_UNAVAILABLE, {"error": "auth_unavailable"})
            return
        except AuthenticationError:
            server = cast(_Server, self.server)
            if server.mcp_authorization is not None and _is_mcp_tool_call(payload):
                runtime = server.tesla_runtime
                if runtime is not None and runtime.mcp is not None:
                    challenge = server.mcp_authorization.challenge(
                        error="invalid_token",
                        description="Sign in to use Woodhouse Tesla tools",
                    )
                    self._send_json_document(
                        HTTPStatus.OK,
                        runtime.mcp.authentication_required(payload, challenge),
                        authenticate=True,
                    )
                    return
            self._send_json(
                HTTPStatus.UNAUTHORIZED,
                {"error": "unauthorized"},
                authenticate=True,
            )
            return
        except (json.JSONDecodeError, UnicodeDecodeError, ValueError):
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": "invalid_json_request"})
            return

        runtime = cast(_Server, self.server).tesla_runtime
        if runtime is None or runtime.mcp is None:
            self._send_json(
                HTTPStatus.SERVICE_UNAVAILABLE,
                {"error": "tesla_mcp_not_configured"},
            )
            return
        response = runtime.mcp.handle(context, payload)
        if response is None:
            self.send_response(HTTPStatus.ACCEPTED)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        self._send_json_document(HTTPStatus.OK, response)

    def _serve_oauth_resource_metadata(self) -> None:
        authorization = cast(_Server, self.server).mcp_authorization
        if authorization is None:
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "oauth_not_configured"})
            return
        self._send_json_document(HTTPStatus.OK, authorization.metadata_document())

    def _serve_onboarding(self, query: dict[str, list[str]]) -> None:
        server = cast(_Server, self.server)
        if server.browser_auth is None:
            self._send_html(
                HTTPStatus.SERVICE_UNAVAILABLE,
                error_page(
                    "Sign-in is not configured", "The operator has not enabled browser onboarding."
                ),
            )
            return
        session = self._optional_browser_session()
        if session is None:
            self._send_html(HTTPStatus.OK, onboarding_page())
            return
        context, browser_session, _ = session
        runtime = server.tesla_runtime
        if runtime is None:
            self._send_html(
                HTTPStatus.SERVICE_UNAVAILABLE,
                error_page(
                    "Tesla setup is unavailable", "The Tesla integration is not configured."
                ),
            )
            return
        vehicles = runtime.onboarding.list_vehicles(context)
        message = None
        if _single_optional_query_value(query, "connected") == "1":
            message = "Tesla authorization succeeded. Pair and verify each vehicle below."
        elif _single_optional_query_value(query, "refreshed") == "1":
            message = "The selected vehicle's Virtual Key status was refreshed."
        elif _single_optional_query_value(query, "tesla") == "denied":
            message = "Tesla authorization was cancelled; no connection changes were made."
        self._send_html(
            HTTPStatus.OK,
            onboarding_page(
                vehicles=vehicles,
                csrf_token=browser_session.csrf_token,
                message=message,
            ),
        )

    def _start_platform_login(self) -> None:
        browser_auth = cast(_Server, self.server).browser_auth
        if browser_auth is None:
            self._send_html(
                HTTPStatus.SERVICE_UNAVAILABLE,
                error_page(
                    "Sign-in is not configured", "The operator has not enabled browser onboarding."
                ),
            )
            return
        login = browser_auth.start()
        self._redirect(
            login.authorization_url,
            cookie=_login_cookie(login.browser_binding_token),
        )

    def _complete_platform_login(self, query: dict[str, list[str]]) -> None:
        browser_auth = cast(_Server, self.server).browser_auth
        if browser_auth is None:
            self._send_html(
                HTTPStatus.SERVICE_UNAVAILABLE,
                error_page(
                    "Sign-in is not configured", "The operator has not enabled browser onboarding."
                ),
            )
            return
        browser_binding_token = _cookie_token(
            self.headers.get("Cookie"),
            LOGIN_COOKIE_NAME,
        )
        try:
            if _single_optional_query_value(query, "error") is not None:
                state = _single_optional_query_value(query, "state")
                if state is not None:
                    browser_auth.cancel(
                        state,
                        browser_binding_token=browser_binding_token or "",
                    )
                raise BrowserAuthenticationError("The identity provider denied sign-in")
            state = _single_optional_query_value(query, "state")
            code = _single_optional_query_value(query, "code")
            if state is None or code is None:
                raise BrowserAuthenticationError("The sign-in callback is incomplete")
            result = browser_auth.complete(
                state=state,
                code=code,
                browser_binding_token=browser_binding_token or "",
            )
        except AuthenticationError:
            self._send_html(
                HTTPStatus.FORBIDDEN,
                error_page(
                    "Account not approved",
                    "Ask the operator to add this verified email before signing in.",
                ),
                cookie=_expired_login_cookie(),
            )
            return
        except BrowserAuthenticationError:
            self._send_html(
                HTTPStatus.BAD_REQUEST,
                error_page("Sign-in failed", "Start a fresh sign-in and try again."),
                cookie=_expired_login_cookie(),
            )
            return
        self._redirect(
            "/onboarding",
            cookie=(
                _session_cookie(result.session_token),
                _expired_login_cookie(),
            ),
        )

    def _start_browser_tesla_oauth(self) -> None:
        session = self._require_browser_form_session()
        if session is None:
            return
        context, _, session_token = session
        runtime = cast(_Server, self.server).tesla_runtime
        if runtime is None:
            self._send_html(
                HTTPStatus.SERVICE_UNAVAILABLE,
                error_page(
                    "Tesla setup is unavailable", "The Tesla integration is not configured."
                ),
            )
            return
        browser_auth = cast(_Server, self.server).browser_auth
        if browser_auth is None:
            self._send_html(
                HTTPStatus.SERVICE_UNAVAILABLE,
                error_page(
                    "Sign-in is not configured", "The operator has not enabled browser onboarding."
                ),
            )
            return
        self._redirect(
            runtime.onboarding.start(
                context,
                completion_mode="browser",
                browser_session_binding=browser_auth.session_binding(session_token),
            )
        )

    def _refresh_browser_pairing_status(self, path: str) -> None:
        session = self._require_browser_form_session()
        if session is None:
            return
        context, _, _ = session
        vehicle_id = path.removeprefix("/onboarding/vehicles/").removesuffix("/refresh")
        if not vehicle_id or "/" in vehicle_id:
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        runtime = cast(_Server, self.server).tesla_runtime
        if runtime is None:
            self._send_html(
                HTTPStatus.SERVICE_UNAVAILABLE,
                error_page(
                    "Tesla setup is unavailable", "The Tesla integration is not configured."
                ),
            )
            return
        try:
            runtime.onboarding.refresh_fleet_status(context, vehicle_id)
        except CrossUserAccessError:
            self._send_html(
                HTTPStatus.FORBIDDEN,
                error_page("Vehicle unavailable", "That vehicle is not available to this account."),
            )
            return
        except TeslaReauthorizationRequired:
            self._send_html(
                HTTPStatus.UNAUTHORIZED,
                error_page(
                    "Tesla reconnection required",
                    "Authorize Tesla again before refreshing vehicle status.",
                    retry_path="/onboarding",
                ),
            )
            return
        except (TeslaAPIError, TeslaOnboardingError):
            self._send_html(
                HTTPStatus.BAD_GATEWAY,
                error_page(
                    "Status refresh failed",
                    "Tesla did not return a usable status. Wait briefly and try again.",
                    retry_path="/onboarding",
                ),
            )
            return
        self._redirect("/onboarding?refreshed=1", status=HTTPStatus.SEE_OTHER)

    def _logout_browser_session(self) -> None:
        session = self._require_browser_form_session()
        if session is None:
            return
        _, _, token = session
        browser_auth = cast(_Server, self.server).browser_auth
        if browser_auth is not None:
            browser_auth.logout(token)
        self._redirect("/", status=HTTPStatus.SEE_OTHER, cookie=_expired_session_cookie())

    def _optional_browser_session(self) -> tuple[UserContext, BrowserSession, str] | None:
        browser_auth = cast(_Server, self.server).browser_auth
        if browser_auth is None:
            return None
        token = _session_token(self.headers.get("Cookie"))
        if token is None:
            return None
        try:
            context, session = browser_auth.authenticate_session(token)
        except (AuthenticationError, BrowserAuthenticationError):
            return None
        return context, session, token

    def _require_browser_form_session(self) -> tuple[UserContext, BrowserSession, str] | None:
        session = self._optional_browser_session()
        if session is None:
            self._send_html(
                HTTPStatus.UNAUTHORIZED,
                error_page("Sign-in required", "Sign in before continuing."),
                cookie=_expired_session_cookie(),
            )
            return None
        try:
            form = self._read_form()
        except (TimeoutError, UnicodeDecodeError, ValueError):
            self._send_html(
                HTTPStatus.BAD_REQUEST,
                error_page("Invalid request", "Return to onboarding and try again."),
            )
            return None
        if form.get("csrf_token") != session[1].csrf_token:
            self._send_html(
                HTTPStatus.FORBIDDEN,
                error_page("Request expired", "Return to onboarding and try again."),
            )
            return None
        return session

    def _serve_tesla_public_key(self) -> None:
        runtime = cast(_Server, self.server).tesla_runtime
        if runtime is None:
            self._send_json(HTTPStatus.SERVICE_UNAVAILABLE, {"error": "tesla_not_configured"})
            return
        body = runtime.public_key_pem
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "application/x-pem-file")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "public, max-age=300")
        self.end_headers()
        self.wfile.write(body)

    def _start_tesla_oauth(self) -> None:
        runtime = cast(_Server, self.server).tesla_runtime
        if runtime is None:
            self._send_json(HTTPStatus.SERVICE_UNAVAILABLE, {"error": "tesla_not_configured"})
            return
        try:
            server = cast(_Server, self.server)
            context = server.auth_boundary.authorize(self.headers.get("Authorization"), {})
            location = runtime.onboarding.start(context)
        except AuthenticationError:
            self._send_json(HTTPStatus.UNAUTHORIZED, {"error": "unauthorized"}, authenticate=True)
            return
        self.send_response(HTTPStatus.FOUND)
        self.send_header("Location", location)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _complete_tesla_oauth(self, query: dict[str, list[str]]) -> None:
        runtime = cast(_Server, self.server).tesla_runtime
        if runtime is None:
            self._send_json(HTTPStatus.SERVICE_UNAVAILABLE, {"error": "tesla_not_configured"})
            return
        browser_session = self._optional_browser_session()
        browser_auth = cast(_Server, self.server).browser_auth
        browser_session_binding = (
            browser_auth.session_binding(browser_session[2])
            if browser_auth is not None and browser_session is not None
            else None
        )
        try:
            state = _single_query_value(query, "state")
            if _single_query_value(query, "error") is not None:
                pending = runtime.onboarding.decline(
                    state=state or "",
                    expected_browser_session_binding=browser_session_binding,
                )
                if pending.completion_mode == "browser":
                    self._redirect("/onboarding?tesla=denied", status=HTTPStatus.SEE_OTHER)
                    return
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": "tesla_authorization_denied"})
                return
            code = _single_query_value(query, "code")
            if state is None or code is None:
                raise InvalidOAuthStateError("Tesla OAuth callback is incomplete")
            result = runtime.onboarding.callback(
                state=state,
                code=code,
                expected_browser_session_binding=browser_session_binding,
            )
        except InvalidOAuthStateError:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": "invalid_oauth_state"})
            return
        except TeslaAuthenticationError:
            self._send_json(HTTPStatus.BAD_GATEWAY, {"error": "tesla_authorization_failed"})
            return
        except (TeslaAPIError, TeslaOnboardingError):
            self._send_json(HTTPStatus.BAD_GATEWAY, {"error": "tesla_onboarding_failed"})
            return
        if result.completion_mode == "browser":
            if browser_session is None or browser_session[0].user_id != result.owner_user_id:
                self._send_html(
                    HTTPStatus.FORBIDDEN,
                    error_page(
                        "Sign-in required",
                        "Sign in again before viewing the connected vehicles.",
                    ),
                )
                return
            self._redirect("/onboarding?connected=1", status=HTTPStatus.SEE_OTHER)
            return
        self._send_json_document(
            HTTPStatus.OK,
            {
                "status": "connected",
                "connection_id": result.connection_id,
                "region": result.region,
                "fleet_api_base_url": result.base_url,
                "vehicles": runtime.onboarding.vehicle_documents(result.vehicles),
            },
        )

    def _list_tesla_vehicles(self) -> None:
        runtime = cast(_Server, self.server).tesla_runtime
        if runtime is None:
            self._send_json(HTTPStatus.SERVICE_UNAVAILABLE, {"error": "tesla_not_configured"})
            return
        try:
            server = cast(_Server, self.server)
            context = server.auth_boundary.authorize(self.headers.get("Authorization"), {})
            vehicles = runtime.onboarding.list_vehicles(context)
        except AuthenticationError:
            self._send_json(HTTPStatus.UNAUTHORIZED, {"error": "unauthorized"}, authenticate=True)
            return
        self._send_json_document(HTTPStatus.OK, {"vehicles": vehicles})

    def _refresh_tesla_fleet_status(self, path: str) -> None:
        runtime = cast(_Server, self.server).tesla_runtime
        if runtime is None:
            self._send_json(HTTPStatus.SERVICE_UNAVAILABLE, {"error": "tesla_not_configured"})
            return
        vehicle_id = path.removeprefix("/tesla/vehicles/").removesuffix("/fleet-status")
        if not vehicle_id or "/" in vehicle_id:
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        try:
            payload = self._read_json()
            server = cast(_Server, self.server)
            context = server.auth_boundary.authorize(self.headers.get("Authorization"), payload)
            vehicle = runtime.onboarding.refresh_fleet_status(context, vehicle_id)
        except TimeoutError:
            self._send_json(HTTPStatus.REQUEST_TIMEOUT, {"error": "request_timeout"})
            return
        except CallerIdentityClaimError:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": "caller_identity_fields_forbidden"})
            return
        except AuthenticationError:
            self._send_json(HTTPStatus.UNAUTHORIZED, {"error": "unauthorized"}, authenticate=True)
            return
        except TeslaReauthorizationRequired:
            self._send_json(HTTPStatus.UNAUTHORIZED, {"error": "tesla_reauthorization_required"})
            return
        except CrossUserAccessError:
            self._send_json(HTTPStatus.FORBIDDEN, {"error": "vehicle_forbidden"})
            return
        except TeslaAPIError as error:
            _log_tesla_failure("tesla_fleet_status_failed", error)
            self._send_json(HTTPStatus.BAD_GATEWAY, {"error": "tesla_fleet_status_failed"})
            return
        except TeslaOnboardingError:
            LOGGER.warning(
                "tesla_fleet_status_failed category=onboarding_error upstream_status=none"
            )
            self._send_json(HTTPStatus.BAD_GATEWAY, {"error": "tesla_fleet_status_failed"})
            return
        except (json.JSONDecodeError, UnicodeDecodeError, ValueError):
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": "invalid_json_request"})
            return
        self._send_json_document(HTTPStatus.OK, {"vehicle": vehicle})

    def _rotate_tesla_refresh_token(self) -> None:
        runtime = cast(_Server, self.server).tesla_runtime
        if runtime is None:
            self._send_json(HTTPStatus.SERVICE_UNAVAILABLE, {"error": "tesla_not_configured"})
            return
        try:
            payload = self._read_json()
            server = cast(_Server, self.server)
            context = server.auth_boundary.authorize(self.headers.get("Authorization"), payload)
            result = runtime.onboarding.rotate_refresh_token(context)
        except TimeoutError:
            self._send_json(HTTPStatus.REQUEST_TIMEOUT, {"error": "request_timeout"})
            return
        except CallerIdentityClaimError:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": "caller_identity_fields_forbidden"})
            return
        except AuthenticationError:
            self._send_json(HTTPStatus.UNAUTHORIZED, {"error": "unauthorized"}, authenticate=True)
            return
        except TeslaReauthorizationRequired:
            self._send_json(HTTPStatus.UNAUTHORIZED, {"error": "tesla_reauthorization_required"})
            return
        except (TeslaAPIError, TeslaOnboardingError):
            self._send_json(HTTPStatus.BAD_GATEWAY, {"error": "tesla_refresh_failed"})
            return
        except (json.JSONDecodeError, UnicodeDecodeError, ValueError):
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": "invalid_json_request"})
            return
        self._send_json_document(HTTPStatus.OK, result)

    def _read_json(self) -> object:
        content_length = self.headers.get("Content-Length")
        if content_length is None:
            raise ValueError("Content-Length is required")
        length = int(content_length)
        if length < 0 or length > MAX_REQUEST_BYTES:
            raise ValueError("Request body size is invalid")
        body = _read_request_body(
            cast(_RequestBodyReader, self.rfile),
            cast(_TimeoutConnection, self.connection),
            length,
        )
        return _decode_json_request(body)

    def _read_form(self) -> dict[str, str]:
        content_type = self.headers.get("Content-Type", "").partition(";")[0].strip().casefold()
        if content_type != "application/x-www-form-urlencoded":
            raise ValueError("Form content type is required")
        content_length = self.headers.get("Content-Length")
        if content_length is None:
            raise ValueError("Content-Length is required")
        length = int(content_length)
        if length < 0 or length > 16_384:
            raise ValueError("Form body size is invalid")
        body = _read_request_body(
            cast(_RequestBodyReader, self.rfile),
            cast(_TimeoutConnection, self.connection),
            length,
        ).decode("utf-8")
        parsed = parse_qs(body, strict_parsing=True, max_num_fields=8)
        return {key: values[0] for key, values in parsed.items() if len(values) == 1}

    def _redirect(
        self,
        location: str,
        *,
        status: HTTPStatus = HTTPStatus.FOUND,
        cookie: str | tuple[str, ...] | None = None,
    ) -> None:
        self.send_response(status)
        self.send_header("Location", location)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Referrer-Policy", "no-referrer")
        for value in _cookie_headers(cookie):
            self.send_header("Set-Cookie", value)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _send_html(
        self,
        status: HTTPStatus,
        body: bytes,
        *,
        cookie: str | tuple[str, ...] | None = None,
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; "
            "base-uri 'none'; frame-ancestors 'none'",
        )
        for value in _cookie_headers(cookie):
            self.send_header("Set-Cookie", value)
        self.end_headers()
        self.wfile.write(body)

    def _send_json(
        self,
        status: HTTPStatus,
        document: dict[str, str],
        *,
        authenticate: bool = False,
    ) -> None:
        self._send_json_document(status, document, authenticate=authenticate)

    def _send_json_document(
        self,
        status: HTTPStatus,
        document: object,
        *,
        authenticate: bool = False,
    ) -> None:
        body = json.dumps(document, sort_keys=True).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Content-Type-Options", "nosniff")
        if authenticate:
            authorization = cast(_Server, self.server).mcp_authorization
            challenge = (
                authorization.challenge(
                    error="invalid_token", description="Authentication required"
                )
                if authorization is not None
                else 'Bearer realm="mcp-gateway"'
            )
            self.send_header("WWW-Authenticate", challenge)
        self.end_headers()
        self.wfile.write(body)

    def log_request(self, code: int | str = "-", size: int | str = "-") -> None:
        """Log the request path without OAuth codes, state, or other query values."""
        self.log_message(
            '"%s %s %s" %s %s',
            self.command,
            urlsplit(self.path).path,
            self.request_version,
            str(code),
            str(size),
        )

    def log_message(self, format: str, *args: object) -> None:
        """Use standard access logging after ``log_request`` removes query values."""
        super().log_message(format, *args)


class _Server(ThreadingHTTPServer):
    allow_reuse_address = True

    def __init__(
        self,
        server_address: tuple[str, int],
        auth_boundary: GatewayAuthBoundary,
        tesla_runtime: TeslaRuntime | None = None,
        mcp_authorization: MCPAuthorizationSettings | None = None,
        browser_auth: BrowserAuthService | None = None,
    ) -> None:
        self.auth_boundary = auth_boundary
        self.tesla_runtime = tesla_runtime
        self.mcp_authorization = mcp_authorization
        self.browser_auth = browser_auth
        super().__init__(server_address, _Handler)


def main() -> None:
    """Run the authenticated gateway boundary."""
    host = os.environ.get("HOST", DEFAULT_HOST)
    port = int(os.environ.get("PORT", str(DEFAULT_PORT)))
    auth_boundary, tesla_runtime, mcp_authorization, browser_auth = build_runtime()
    server = _Server(
        (host, port),
        auth_boundary,
        tesla_runtime,
        mcp_authorization,
        browser_auth,
    )
    cast(ThreadingHTTPServer, server).serve_forever()


def _single_query_value(query: dict[str, list[str]], name: str) -> str | None:
    values = query.get(name)
    if values is None:
        return None
    if len(values) != 1 or not values[0]:
        raise InvalidOAuthStateError("Tesla OAuth callback parameter is invalid")
    return values[0]


def _single_optional_query_value(query: dict[str, list[str]], name: str) -> str | None:
    try:
        return _single_query_value(query, name)
    except InvalidOAuthStateError:
        return None


def _session_token(cookie_header: str | None) -> str | None:
    return _cookie_token(cookie_header, SESSION_COOKIE_NAME)


def _cookie_token(cookie_header: str | None, name: str) -> str | None:
    if not cookie_header:
        return None
    cookie = SimpleCookie()
    try:
        cookie.load(cookie_header)
    except (CookieError, ValueError):
        return None
    morsel = cookie.get(name)
    return morsel.value if morsel is not None and morsel.value else None


def _session_cookie(token: str) -> str:
    max_age = int(WEB_SESSION_LIFETIME.total_seconds())
    return (
        f"{SESSION_COOKIE_NAME}={token}; Path=/; Max-Age={max_age}; Secure; HttpOnly; SameSite=Lax"
    )


def _login_cookie(token: str) -> str:
    max_age = 10 * 60
    return f"{LOGIN_COOKIE_NAME}={token}; Path=/; Max-Age={max_age}; Secure; HttpOnly; SameSite=Lax"


def _expired_session_cookie() -> str:
    return f"{SESSION_COOKIE_NAME}=; Path=/; Max-Age=0; Secure; HttpOnly; SameSite=Lax"


def _expired_login_cookie() -> str:
    return f"{LOGIN_COOKIE_NAME}=; Path=/; Max-Age=0; Secure; HttpOnly; SameSite=Lax"


def _cookie_headers(cookie: str | tuple[str, ...] | None) -> tuple[str, ...]:
    if cookie is None:
        return ()
    if isinstance(cookie, str):
        return (cookie,)
    return cookie


def _is_mcp_tool_call(payload: object) -> bool:
    return isinstance(payload, dict) and payload.get("method") == "tools/call"


def _mcp_requires_auth(payload: object) -> bool:
    return _is_mcp_tool_call(payload)


def _log_tesla_failure(event: str, error: TeslaAPIError) -> None:
    """Log only credential-free Tesla failure metadata."""
    upstream_status = str(error.status_code) if error.status_code is not None else "none"
    LOGGER.warning(
        "%s category=%s upstream_status=%s",
        event,
        error.category,
        upstream_status,
    )


if __name__ == "__main__":
    main()
