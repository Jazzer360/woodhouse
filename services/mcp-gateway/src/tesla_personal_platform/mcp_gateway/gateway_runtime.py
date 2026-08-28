"""Composition root for gateway authentication and Tesla services."""

from __future__ import annotations

from dataclasses import dataclass

from google.cloud import firestore
from tesla_personal_platform.auth import (
    Authenticator,
    OIDCAccessTokenVerifier,
    OIDCIDTokenVerifier,
)
from tesla_personal_platform.auth.firestore import FirestoreIdentityStore
from tesla_personal_platform.auth.google_oidc import GoogleOIDCVerifier
from tesla_personal_platform.mcp_gateway.auth_boundary import GatewayAuthBoundary
from tesla_personal_platform.mcp_gateway.browser_auth import (
    BrowserAuthService,
    BrowserOIDCConfig,
    FirestoreBrowserAuthStore,
)
from tesla_personal_platform.mcp_gateway.mcp_auth import (
    MCP_ACCESS_SCOPE,
    MCPAuthorizationSettings,
)
from tesla_personal_platform.mcp_gateway.settings import GatewaySettings, require_setting
from tesla_personal_platform.mcp_gateway.tesla_runtime import TeslaRuntime, build_tesla_runtime


@dataclass(frozen=True, slots=True)
class GatewayRuntime:
    """Fully constructed dependencies shared by the ASGI routes."""

    auth_boundary: GatewayAuthBoundary
    tesla: TeslaRuntime | None
    authorization: MCPAuthorizationSettings | None
    browser_auth: BrowserAuthService | None


def build_runtime(settings: GatewaySettings | None = None) -> GatewayRuntime:
    """Build platform auth and optional Tesla onboarding from typed settings."""
    settings = settings or GatewaySettings()
    project_id = settings.gcp.project_id
    firestore_client = firestore.Client(project=project_id)
    identities = FirestoreIdentityStore(firestore_client)
    issuer = str(settings.oidc.issuer) if settings.oidc.issuer is not None else ""
    resource_url = str(settings.oidc.resource_url) if settings.oidc.resource_url is not None else ""
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
        if settings.oidc.browser_enabled:
            client_id = require_setting(settings.oidc.client_id, "PLATFORM_OIDC_CLIENT_ID")
            client_secret = require_setting(
                settings.oidc.client_secret, "PLATFORM_OIDC_CLIENT_SECRET"
            )
            redirect_uri = require_setting(settings.oidc.redirect_uri, "PLATFORM_OIDC_REDIRECT_URI")
            browser_auth = BrowserAuthService(
                config=BrowserOIDCConfig(
                    issuer=issuer,
                    audience=resource_url,
                    client_id=client_id,
                    client_secret=client_secret.get_secret_value(),
                    redirect_uri=str(redirect_uri),
                    scopes=("openid", "email", "profile", MCP_ACCESS_SCOPE),
                ),
                store=FirestoreBrowserAuthStore(firestore_client),
                identities=identities,
                access_tokens=access_tokens,
                id_tokens=OIDCIDTokenVerifier(issuer=issuer, audience=client_id),
            )
    else:
        audience = require_setting(settings.oidc.legacy_google_audience, "OIDC_AUDIENCE")
        authenticator = Authenticator(GoogleOIDCVerifier(audience), identities)

    return GatewayRuntime(
        auth_boundary=GatewayAuthBoundary(authenticator),
        tesla=build_tesla_runtime(
            project_id,
            settings=settings.tesla,
            command_proxy_settings=settings.command_proxy,
            telemetry_settings=settings.telemetry,
            analytics_location=settings.gcp.analytics_location,
        ),
        authorization=authorization,
        browser_auth=browser_auth,
    )
