"""Official MCP SDK integration for Woodhouse's semantic capability surface."""

from __future__ import annotations

from typing import Any

from mcp.server import MCPServer
from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.auth.provider import AccessToken
from mcp.server.auth.settings import AuthSettings
from mcp.server.mcpserver.exceptions import ToolError
from mcp_types import ToolAnnotations
from pydantic import AnyHttpUrl
from tesla_personal_platform.auth import AuthenticationError, UserContext
from tesla_personal_platform.mcp_gateway import SERVICE_NAME
from tesla_personal_platform.mcp_gateway.auth_boundary import GatewayAuthBoundary
from tesla_personal_platform.mcp_gateway.mcp_auth import MCPAuthorizationSettings
from tesla_personal_platform.mcp_gateway.mcp_models import (
    AccountRead,
    AnalyticsQuery,
    ChargingControl,
    ChargingRecordRead,
    ClimateControl,
    MediaControl,
    NavigationControl,
    SecurityControl,
    ToolInput,
    VehicleAccessControl,
    VehicleRead,
    VehicleSettingsControl,
    WakeVehicle,
)
from tesla_personal_platform.mcp_gateway.mcp_policy import MCPToolError
from tesla_personal_platform.mcp_gateway.mcp_tools import TeslaMCPService
from tesla_personal_platform.tesla_client import TeslaAPIError

Document = dict[str, Any]

READ_ONLY = ToolAnnotations(
    read_only_hint=True,
    destructive_hint=False,
    idempotent_hint=True,
    open_world_hint=True,
)
CONTROL = ToolAnnotations(
    read_only_hint=False,
    destructive_hint=False,
    idempotent_hint=False,
    open_world_hint=True,
)
SECURITY_CONTROL = ToolAnnotations(
    read_only_hint=False,
    destructive_hint=True,
    idempotent_hint=False,
    open_world_hint=True,
)


class WoodhouseTokenVerifier:
    """Adapt Woodhouse JWT verification and allowlist binding to the MCP SDK."""

    def __init__(self, boundary: GatewayAuthBoundary) -> None:
        self._boundary = boundary

    async def verify_token(self, token: str) -> AccessToken | None:
        try:
            context = self._boundary.authorize(f"Bearer {token}", {})
        except AuthenticationError:
            return None
        return AccessToken(
            token=token,
            client_id="woodhouse-mcp-client",
            scopes=["mcp:access"],
            subject=context.oidc_subject,
            claims={
                "user_id": context.user_id,
                "dataset_id": context.dataset_id,
                "oidc_issuer": context.oidc_issuer,
                "oidc_subject": context.oidc_subject,
            },
        )


def create_mcp_server(
    service: TeslaMCPService,
    *,
    auth_boundary: GatewayAuthBoundary | None = None,
    authorization: MCPAuthorizationSettings | None = None,
    test_user_context: UserContext | None = None,
) -> MCPServer[None]:
    """Create the production MCP server, or an explicitly context-bound test server."""
    if (auth_boundary is None) != (authorization is None):
        raise ValueError(
            "MCP authorization settings and token verifier must be configured together"
        )
    auth = None
    verifier = None
    if auth_boundary is not None and authorization is not None:
        auth = AuthSettings(
            issuer_url=AnyHttpUrl(authorization.authorization_server),
            resource_server_url=AnyHttpUrl(authorization.resource_url),
            required_scopes=list(authorization.scopes),
        )
        verifier = WoodhouseTokenVerifier(auth_boundary)
    server: MCPServer[None] = MCPServer(
        SERVICE_NAME,
        title="Woodhouse Tesla Personal Platform",
        description=("Authenticated semantic Tesla controls and isolated historical analytics."),
        version="2.0.0",
        auth=auth,
        token_verifier=verifier,
    )

    def current_user() -> UserContext:
        if test_user_context is not None:
            return test_user_context
        token = get_access_token()
        claims = token.claims if token is not None else None
        if not isinstance(claims, dict):
            raise RuntimeError("Authenticated Woodhouse user context is unavailable")
        try:
            return UserContext(
                user_id=str(claims["user_id"]),
                dataset_id=str(claims["dataset_id"]),
                oidc_issuer=str(claims["oidc_issuer"]),
                oidc_subject=str(claims["oidc_subject"]),
            )
        except KeyError as error:
            raise RuntimeError("Authenticated Woodhouse user context is incomplete") from error

    def call(name: str, arguments: object) -> Document:
        try:
            return service.call(current_user(), name, arguments)
        except MCPToolError as error:
            correlation_id = error.correlation_id
            suffix = f"; correlation_id={correlation_id}" if correlation_id else ""
            raise ToolError(f"{error.category}: {error}{suffix}") from None
        except TeslaAPIError as error:
            suffix = f"; correlation_id={error.correlation_id}" if error.correlation_id else ""
            raise ToolError(f"{error.category}: Tesla Fleet API request failed{suffix}") from None

    def call_family(request: ToolInput) -> Document:
        return call(f"tesla_{request.action}", request.legacy_arguments())

    @server.tool(annotations=READ_ONLY)
    def get_tesla_account(request: AccountRead) -> Document:
        """Read account metadata or list vehicles for the authenticated Tesla connection."""
        return call_family(request)

    @server.tool(annotations=READ_ONLY)
    def get_vehicle_status(request: VehicleRead) -> Document:
        """Read one owned vehicle's live status without an implicit wake."""
        return call_family(request)

    @server.tool(annotations=READ_ONLY)
    def get_charging_records(request: ChargingRecordRead) -> Document:
        """Read Tesla charging history or one invoice for the authenticated account."""
        return call_family(request)

    @server.tool(annotations=SECURITY_CONTROL)
    def control_vehicle_access(request: VehicleAccessControl) -> Document:
        """Control locks, openings, HomeLink, remote drive, lights, or horn."""
        return call_family(request)

    @server.tool(annotations=CONTROL)
    def control_vehicle_climate(request: ClimateControl) -> Document:
        """Control cabin climate, seats, steering wheel heat, and protection modes."""
        return call_family(request)

    @server.tool(annotations=CONTROL)
    def control_vehicle_charging(request: ChargingControl) -> Document:
        """Control charging, charge limits/current, ports, and supported schedules."""
        return call_family(request)

    @server.tool(annotations=CONTROL)
    def control_vehicle_media(request: MediaControl) -> Document:
        """Control playback, favorites, volume, and boombox on one owned vehicle."""
        return call_family(request)

    @server.tool(annotations=CONTROL)
    def control_vehicle_navigation(request: NavigationControl) -> Document:
        """Send typed destinations, coordinates, Superchargers, or waypoints."""
        return call_family(request)

    @server.tool(annotations=SECURITY_CONTROL)
    def control_vehicle_security(request: SecurityControl) -> Document:
        """Control Sentry, PIN, valet, parental, and speed-limit settings."""
        return call_family(request)

    @server.tool(annotations=SECURITY_CONTROL)
    def control_vehicle_settings(request: VehicleSettingsControl) -> Document:
        """Control software update, vehicle naming, sunroof, and calendar integration."""
        return call_family(request)

    @server.tool(annotations=CONTROL)
    def wake_vehicle(request: WakeVehicle) -> Document:
        """Explicitly wake one owned vehicle; live reads never wake implicitly."""
        return call("tesla_wake_up", request.model_dump(mode="json", exclude_none=True))

    @server.tool(annotations=READ_ONLY)
    def get_analytics_schema() -> Document:
        """Describe the authenticated user's private historical analytics namespace."""
        return call("get_analytics_schema", {})

    @server.tool(annotations=READ_ONLY)
    def run_analytics_query(request: AnalyticsQuery) -> Document:
        """Run one bounded read-only query in the server-derived private dataset."""
        return call("run_analytics_query", request.model_dump(mode="json"))

    return server
