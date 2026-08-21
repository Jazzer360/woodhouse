"""Phase 3 MCP gateway with a real platform-authentication boundary."""

import json
import os
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Final, cast

from google.cloud import firestore
from tesla_personal_platform.auth import (
    AuthenticationError,
    Authenticator,
    CallerIdentityClaimError,
    ConfigurationError,
)
from tesla_personal_platform.auth.firestore import FirestoreIdentityStore
from tesla_personal_platform.auth.google_oidc import GoogleOIDCVerifier
from tesla_personal_platform.mcp_gateway import SERVICE_NAME
from tesla_personal_platform.mcp_gateway.auth_boundary import GatewayAuthBoundary

DEFAULT_HOST: Final = "127.0.0.1"
DEFAULT_PORT: Final = 8080
MAX_REQUEST_BYTES: Final = 1_048_576


def health_document() -> dict[str, str]:
    """Return a non-sensitive service health document."""
    return {"phase": "platform-auth", "service": SERVICE_NAME, "status": "ok"}


def build_auth_boundary() -> GatewayAuthBoundary:
    """Build the production Google OIDC and Firestore adapters from safe config."""
    audience = os.environ.get("OIDC_AUDIENCE", "").strip()
    project_id = os.environ.get("GOOGLE_CLOUD_PROJECT", "").strip()
    if not audience:
        raise RuntimeError("OIDC_AUDIENCE must be configured")
    if not project_id:
        raise RuntimeError("GOOGLE_CLOUD_PROJECT must be configured")

    verifier = GoogleOIDCVerifier(audience)
    identities = FirestoreIdentityStore(firestore.Client(project=project_id))
    return GatewayAuthBoundary(Authenticator(verifier, identities))


class _Handler(BaseHTTPRequestHandler):
    server: "_Server"

    def do_GET(self) -> None:  # noqa: N802 - inherited HTTP handler API
        if self.path != "/healthz":
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        self._send_json(HTTPStatus.OK, health_document())

    def do_POST(self) -> None:  # noqa: N802 - inherited HTTP handler API
        if self.path != "/mcp":
            self.send_error(HTTPStatus.NOT_FOUND)
            return

        try:
            payload = self._read_json()
            self.server.auth_boundary.authorize(self.headers.get("Authorization"), payload)
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

        self._send_json(
            HTTPStatus.NOT_IMPLEMENTED,
            {"error": "mcp_behavior_deferred", "phase": "platform-auth"},
        )

    def _read_json(self) -> object:
        content_length = self.headers.get("Content-Length")
        if content_length is None:
            raise ValueError("Content-Length is required")
        length = int(content_length)
        if length < 0 or length > MAX_REQUEST_BYTES:
            raise ValueError("Request body size is invalid")
        return json.loads(self.rfile.read(length).decode("utf-8"))

    def _send_json(
        self,
        status: HTTPStatus,
        document: dict[str, str],
        *,
        authenticate: bool = False,
    ) -> None:
        body = json.dumps(document, sort_keys=True).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        if authenticate:
            self.send_header("WWW-Authenticate", 'Bearer realm="mcp-gateway"')
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        """Use standard access logging, which never includes authorization headers."""
        super().log_message(format, *args)


class _Server(ThreadingHTTPServer):
    allow_reuse_address = True

    def __init__(
        self,
        server_address: tuple[str, int],
        auth_boundary: GatewayAuthBoundary,
    ) -> None:
        self.auth_boundary = auth_boundary
        super().__init__(server_address, _Handler)


def main() -> None:
    """Run the authenticated gateway boundary."""
    host = os.environ.get("HOST", DEFAULT_HOST)
    port = int(os.environ.get("PORT", str(DEFAULT_PORT)))
    server = _Server((host, port), build_auth_boundary())
    cast(ThreadingHTTPServer, server).serve_forever()


if __name__ == "__main__":
    main()
