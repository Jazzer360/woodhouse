"""Auto-escaped Jinja rendering for the private onboarding UI."""

from __future__ import annotations

import json
from collections.abc import Mapping
from functools import lru_cache
from urllib.parse import quote

from jinja2 import Environment, PackageLoader, StrictUndefined, select_autoescape


@lru_cache(maxsize=1)
def _templates() -> Environment:
    return Environment(
        loader=PackageLoader("tesla_personal_platform.mcp_gateway", "templates"),
        autoescape=select_autoescape(("html", "xml", "j2"), default=True),
        undefined=StrictUndefined,
        auto_reload=False,
    )


def onboarding_page(
    *,
    vehicles: list[dict[str, object]] | None = None,
    csrf_token: str | None = None,
    message: str | None = None,
) -> bytes:
    """Render the public sign-in page or one authenticated user's vehicles."""
    presented = []
    for vehicle in vehicles or []:
        pairing_url = str(vehicle.get("virtual_key_pairing_url") or "")
        presented.append(
            {
                **vehicle,
                "encoded_id": quote(str(vehicle.get("vehicle_id", "")), safe=""),
                "safe_pairing_url": (
                    pairing_url if pairing_url.startswith("https://www.tesla.com/_ak/") else None
                ),
            }
        )
    return (
        _templates()
        .get_template("onboarding.html.j2")
        .render(
            vehicles=presented,
            csrf_token=csrf_token,
            message=message,
        )
        .encode()
    )


def error_page(title: str, message: str, *, retry_path: str = "/") -> bytes:
    """Render a safe generic interruption page."""
    return (
        _templates()
        .get_template("error.html.j2")
        .render(
            title=title,
            message=message,
            retry_path=retry_path,
        )
        .encode()
    )


def telemetry_configuration_page(
    document: Mapping[str, object], csrf_token: str, *, message: str | None = None
) -> bytes:
    """Render the exact safe desired/current plan and guarded operator actions."""
    persisted = document.get("persisted", {})
    diff = document.get("diff", {})
    return (
        _templates()
        .get_template("telemetry.html.j2")
        .render(
            document_json=json.dumps(document, indent=2, sort_keys=True),
            persisted_json=json.dumps(persisted, sort_keys=True),
            vehicle_id=str(document.get("vehicle_id", "")),
            encoded_id=quote(str(document.get("vehicle_id", "")), safe=""),
            display_name=str(document.get("display_name") or "Tesla vehicle"),
            desired_hash=str(document.get("desired_config_hash", "")),
            status=diff.get("status") if isinstance(diff, dict) else "unknown",
            transport_opt_in=(
                bool(persisted.get("transport_maintenance_opt_in", False))
                if isinstance(persisted, dict)
                else False
            ),
            csrf_token=csrf_token,
            message=message,
        )
        .encode()
    )
