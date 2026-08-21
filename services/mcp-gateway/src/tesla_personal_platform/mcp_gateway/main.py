"""Health-only process for the Phase 1 MCP gateway container."""

import json
import os
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Final

from tesla_personal_platform.mcp_gateway import SERVICE_NAME

DEFAULT_HOST: Final = "127.0.0.1"
DEFAULT_PORT: Final = 8080


def health_document() -> dict[str, str]:
    """Return the non-production scaffold health document."""
    return {"phase": "scaffold", "service": SERVICE_NAME, "status": "ok"}


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802 - inherited HTTP handler API
        if self.path != "/healthz":
            self.send_error(HTTPStatus.NOT_FOUND)
            return

        body = json.dumps(health_document(), sort_keys=True).encode()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        """Retain standard access logging without adding request data."""
        super().log_message(format, *args)


class _Server(ThreadingHTTPServer):
    allow_reuse_address = True


def main() -> None:
    """Run the scaffold health server."""
    host = os.environ.get("HOST", DEFAULT_HOST)
    port = int(os.environ.get("PORT", str(DEFAULT_PORT)))
    _Server((host, port), _Handler).serve_forever()


if __name__ == "__main__":
    main()
