"""Authenticated gateway with Phase 4 Tesla onboarding routes."""

import json
import logging
import os
from collections.abc import Callable
from http import HTTPStatus
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
)
from tesla_personal_platform.auth.firestore import FirestoreIdentityStore
from tesla_personal_platform.auth.google_oidc import GoogleOIDCVerifier
from tesla_personal_platform.mcp_gateway import SERVICE_NAME
from tesla_personal_platform.mcp_gateway.auth_boundary import GatewayAuthBoundary
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


def build_runtime() -> tuple[GatewayAuthBoundary, TeslaRuntime | None]:
    """Build platform auth and optional Tesla onboarding from runtime configuration."""
    audience = os.environ.get("OIDC_AUDIENCE", "").strip()
    project_id = os.environ.get("GOOGLE_CLOUD_PROJECT", "").strip()
    if not audience:
        raise RuntimeError("OIDC_AUDIENCE must be configured")
    if not project_id:
        raise RuntimeError("GOOGLE_CLOUD_PROJECT must be configured")

    verifier = GoogleOIDCVerifier(audience)
    identities = FirestoreIdentityStore(firestore.Client(project=project_id))
    return (
        GatewayAuthBoundary(Authenticator(verifier, identities)),
        build_tesla_runtime(project_id),
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
            server = cast(_Server, self.server)
            context = server.auth_boundary.authorize(self.headers.get("Authorization"), payload)
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
        try:
            state = _single_query_value(query, "state")
            if _single_query_value(query, "error") is not None:
                runtime.onboarding.decline(state=state or "")
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": "tesla_authorization_denied"})
                return
            code = _single_query_value(query, "code")
            if state is None or code is None:
                raise InvalidOAuthStateError("Tesla OAuth callback is incomplete")
            result = runtime.onboarding.callback(state=state, code=code)
        except InvalidOAuthStateError:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": "invalid_oauth_state"})
            return
        except TeslaAuthenticationError:
            self._send_json(HTTPStatus.BAD_GATEWAY, {"error": "tesla_authorization_failed"})
            return
        except (TeslaAPIError, TeslaOnboardingError):
            self._send_json(HTTPStatus.BAD_GATEWAY, {"error": "tesla_onboarding_failed"})
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
            self.send_header("WWW-Authenticate", 'Bearer realm="mcp-gateway"')
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
    ) -> None:
        self.auth_boundary = auth_boundary
        self.tesla_runtime = tesla_runtime
        super().__init__(server_address, _Handler)


def main() -> None:
    """Run the authenticated gateway boundary."""
    host = os.environ.get("HOST", DEFAULT_HOST)
    port = int(os.environ.get("PORT", str(DEFAULT_PORT)))
    auth_boundary, tesla_runtime = build_runtime()
    server = _Server((host, port), auth_boundary, tesla_runtime)
    cast(ThreadingHTTPServer, server).serve_forever()


def _single_query_value(query: dict[str, list[str]], name: str) -> str | None:
    values = query.get(name)
    if values is None:
        return None
    if len(values) != 1 or not values[0]:
        raise InvalidOAuthStateError("Tesla OAuth callback parameter is invalid")
    return values[0]


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
