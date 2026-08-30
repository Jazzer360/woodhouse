"""OAuth protected-resource metadata and MCP authentication challenges."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final
from urllib.parse import urlsplit

MCP_ACCESS_SCOPE: Final = "mcp:access"


@dataclass(frozen=True, slots=True)
class MCPAuthorizationSettings:
    """Public OAuth metadata for the protected MCP resource."""

    resource_url: str
    authorization_server: str
    scopes: tuple[str, ...]

    def __post_init__(self) -> None:
        for value, label in (
            (self.resource_url, "MCP resource URL"),
            (self.authorization_server, "OAuth authorization server"),
        ):
            parsed = urlsplit(value)
            if (
                parsed.scheme != "https"
                or not parsed.netloc
                or parsed.username
                or parsed.password
                or "\r" in value
                or "\n" in value
            ):
                raise ValueError(f"{label} must be a safe HTTPS URL")
        if not self.scopes or any(
            not scope or any(char.isspace() for char in scope) for scope in self.scopes
        ):
            raise ValueError("OAuth scopes must be non-empty tokens")

    @property
    def metadata_url(self) -> str:
        parsed = urlsplit(self.resource_url)
        return f"{parsed.scheme}://{parsed.netloc}/.well-known/oauth-protected-resource"

    @property
    def public_authority(self) -> str:
        """Return the validated Host header authority for the MCP transport."""
        return urlsplit(self.resource_url).netloc

    @property
    def public_origin(self) -> str:
        """Return the validated same-origin browser origin for the MCP transport."""
        parsed = urlsplit(self.resource_url)
        return f"{parsed.scheme}://{parsed.netloc}"

    def metadata_document(self) -> dict[str, object]:
        return {
            "resource": self.resource_url,
            "authorization_servers": [self.authorization_server.rstrip("/") + "/"],
            "scopes_supported": list(self.scopes),
            "resource_documentation": f"{self.resource_url.removesuffix('/mcp')}/onboarding",
        }

    def challenge(
        self,
        *,
        error: str | None = None,
        description: str | None = None,
    ) -> str:
        parts = [f'resource_metadata="{self.metadata_url}"', f'scope="{" ".join(self.scopes)}"']
        if error is not None:
            parts.append(f'error="{_quoted(error)}"')
        if description is not None:
            parts.append(f'error_description="{_quoted(description)}"')
        return "Bearer " + ", ".join(parts)


def _quoted(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\r", " ").replace("\n", " ")
