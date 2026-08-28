"""Uvicorn entrypoint for the authenticated Woodhouse ASGI gateway."""

from __future__ import annotations

import uvicorn
from tesla_personal_platform.mcp_gateway.app import create_app, health_document
from tesla_personal_platform.mcp_gateway.gateway_runtime import build_runtime
from tesla_personal_platform.mcp_gateway.settings import GatewaySettings
from tesla_personal_platform.tesla_client import configure_json_logging

__all__ = ["build_runtime", "create_app", "health_document", "main"]


def main() -> None:
    """Validate settings, construct dependencies, and serve the ASGI application."""
    configure_json_logging()
    settings = GatewaySettings()
    uvicorn.run(
        create_app(build_runtime(settings)),
        host=settings.server.host,
        port=settings.server.port,
        timeout_keep_alive=15,
        server_header=False,
    )


if __name__ == "__main__":
    main()
