"""Typed, fail-fast runtime configuration for the gateway service."""

from __future__ import annotations

from pathlib import Path

from pydantic import AnyHttpUrl, Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class _EnvironmentSettings(BaseSettings):
    model_config = SettingsConfigDict(
        extra="ignore",
        case_sensitive=True,
        validate_default=True,
    )


class ServerSettings(_EnvironmentSettings):
    host: str = Field(default="127.0.0.1", validation_alias="HOST")
    port: int = Field(default=8080, ge=1, le=65535, validation_alias="PORT")


class GCPSettings(_EnvironmentSettings):
    project_id: str = Field(default="", min_length=1, validation_alias="GOOGLE_CLOUD_PROJECT")
    analytics_location: str = Field(default="us-central1", validation_alias="ANALYTICS_LOCATION")


class PlatformOIDCSettings(_EnvironmentSettings):
    issuer: AnyHttpUrl | None = Field(default=None, validation_alias="PLATFORM_OIDC_ISSUER")
    resource_url: AnyHttpUrl | None = Field(
        default=None, validation_alias="PLATFORM_OIDC_RESOURCE_URL"
    )
    client_id: str | None = Field(default=None, validation_alias="PLATFORM_OIDC_CLIENT_ID")
    client_secret: SecretStr | None = Field(
        default=None, validation_alias="PLATFORM_OIDC_CLIENT_SECRET"
    )
    redirect_uri: AnyHttpUrl | None = Field(
        default=None, validation_alias="PLATFORM_OIDC_REDIRECT_URI"
    )
    legacy_google_audience: str | None = Field(default=None, validation_alias="OIDC_AUDIENCE")

    @model_validator(mode="after")
    def validate_platform_oidc(self) -> PlatformOIDCSettings:
        if self.issuer is None:
            if not self.legacy_google_audience:
                raise ValueError("PLATFORM_OIDC_ISSUER or OIDC_AUDIENCE must be configured")
            return self
        if self.resource_url is None:
            raise ValueError("PLATFORM_OIDC_RESOURCE_URL is required with PLATFORM_OIDC_ISSUER")
        browser_values = (self.client_id, self.client_secret, self.redirect_uri)
        if any(value is not None for value in browser_values) and not all(
            value is not None for value in browser_values
        ):
            raise ValueError("Platform browser OIDC client configuration is incomplete")
        return self

    @property
    def browser_enabled(self) -> bool:
        return self.client_id is not None


class TeslaSettings(_EnvironmentSettings):
    enabled: bool = Field(default=False, validation_alias="TESLA_ONBOARDING_ENABLED")
    client_id: str | None = Field(default=None, validation_alias="TESLA_CLIENT_ID")
    client_secret: SecretStr | None = Field(default=None, validation_alias="TESLA_CLIENT_SECRET")
    oauth_redirect_uri: AnyHttpUrl | None = Field(
        default=None, validation_alias="TESLA_OAUTH_REDIRECT_URI"
    )
    initial_audience: AnyHttpUrl | None = Field(
        default=None, validation_alias="TESLA_INITIAL_AUDIENCE"
    )
    app_domain: str | None = Field(default=None, validation_alias="TESLA_APP_DOMAIN")
    public_key_pem: SecretStr | None = Field(default=None, validation_alias="TESLA_PUBLIC_KEY_PEM")
    token_encryption_key: SecretStr | None = Field(
        default=None, validation_alias="TESLA_TOKEN_ENCRYPTION_KEY"
    )

    @model_validator(mode="after")
    def validate_enabled_tesla(self) -> TeslaSettings:
        if self.enabled:
            required = {
                "TESLA_CLIENT_ID": self.client_id,
                "TESLA_CLIENT_SECRET": self.client_secret,
                "TESLA_OAUTH_REDIRECT_URI": self.oauth_redirect_uri,
                "TESLA_INITIAL_AUDIENCE": self.initial_audience,
                "TESLA_APP_DOMAIN": self.app_domain,
                "TESLA_PUBLIC_KEY_PEM": self.public_key_pem,
                "TESLA_TOKEN_ENCRYPTION_KEY": self.token_encryption_key,
            }
            missing = sorted(name for name, value in required.items() if value is None)
            if missing:
                raise ValueError(
                    "Tesla onboarding configuration is incomplete: " + ", ".join(missing)
                )
        return self


class CommandProxySettings(_EnvironmentSettings):
    enabled: bool = Field(default=False, validation_alias="TESLA_COMMAND_PROXY_ENABLED")
    origin: AnyHttpUrl = Field(
        default=AnyHttpUrl("https://localhost:4443"),
        validation_alias="TESLA_COMMAND_PROXY_ORIGIN",
    )
    ca_file: Path | None = Field(default=None, validation_alias="TESLA_COMMAND_PROXY_CA_FILE")

    @model_validator(mode="after")
    def validate_enabled_proxy(self) -> CommandProxySettings:
        if self.enabled and self.ca_file is None:
            raise ValueError("TESLA_COMMAND_PROXY_CA_FILE is required when the proxy is enabled")
        return self


class TelemetryControlSettings(_EnvironmentSettings):
    enabled: bool = Field(default=False, validation_alias="FLEET_TELEMETRY_CONTROL_ENABLED")
    server_ca_pem: SecretStr | None = Field(
        default=None, validation_alias="TELEMETRY_SERVER_CA_PEM"
    )
    trust_profile_id: str | None = Field(
        default=None, validation_alias="TELEMETRY_TRUST_PROFILE_ID"
    )
    hostname: str | None = Field(default=None, validation_alias="TELEMETRY_HOSTNAME")
    port: int = Field(default=443, ge=1, le=65535, validation_alias="TELEMETRY_PORT")
    receiver_version: str = Field(default="unknown", validation_alias="TELEMETRY_RECEIVER_VERSION")

    @model_validator(mode="after")
    def validate_enabled_control(self) -> TelemetryControlSettings:
        if self.enabled and not all((self.server_ca_pem, self.trust_profile_id, self.hostname)):
            raise ValueError("Fleet Telemetry trust profile configuration is incomplete")
        return self


class GatewaySettings:
    """Small composition root; each group reads its established environment names."""

    def __init__(self) -> None:
        self.server = ServerSettings()
        self.gcp = GCPSettings()
        self.oidc = PlatformOIDCSettings()
        self.tesla = TeslaSettings()
        self.command_proxy = CommandProxySettings()
        self.telemetry = TelemetryControlSettings()


def require_setting[SettingT](value: SettingT | None, name: str) -> SettingT:
    """Narrow an optional feature setting after its model-level validation."""
    if value is None:
        raise RuntimeError(f"{name} is required by the enabled feature")
    return value
