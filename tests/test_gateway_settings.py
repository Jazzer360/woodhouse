"""Fail-fast typed gateway settings tests."""

import pytest
from pydantic import ValidationError
from tesla_personal_platform.mcp_gateway.settings import (
    CommandProxySettings,
    PlatformOIDCSettings,
    ServerSettings,
    TeslaSettings,
)


def test_server_settings_reject_invalid_port(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PORT", "70000")

    with pytest.raises(ValidationError):
        ServerSettings()


def test_platform_oidc_requires_complete_browser_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PLATFORM_OIDC_ISSUER", "https://tenant.example/")
    monkeypatch.setenv("PLATFORM_OIDC_RESOURCE_URL", "https://woodhouse.example/mcp")
    monkeypatch.setenv("PLATFORM_OIDC_CLIENT_ID", "browser-client")

    with pytest.raises(ValidationError, match="browser OIDC client configuration"):
        PlatformOIDCSettings()


def test_enabled_tesla_requires_all_secret_backed_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TESLA_ONBOARDING_ENABLED", "true")

    with pytest.raises(ValidationError, match="Tesla onboarding configuration is incomplete"):
        TeslaSettings()


def test_secrets_are_redacted_from_settings_repr(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TESLA_CLIENT_SECRET", "must-not-appear")
    settings = TeslaSettings()

    assert "must-not-appear" not in repr(settings)
    assert "**********" in repr(settings)


def test_enabled_command_proxy_requires_ca_file(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TESLA_COMMAND_PROXY_ENABLED", "true")

    with pytest.raises(ValidationError, match="TESLA_COMMAND_PROXY_CA_FILE"):
        CommandProxySettings()
