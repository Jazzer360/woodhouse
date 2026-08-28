"""Generate the complete MCP tool reference from the live typed registry."""

# ruff: noqa: E501 - long literal descriptions are rendered directly into Markdown.

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from tesla_personal_platform.mcp_gateway.mcp_tools import (
    MCP_TOOL_SPECS,
    ToolSpec,
    analytics_tool_documents,
)
from tesla_personal_platform.tesla_client.coverage import COMMAND_NAMES

ROOT = Path(__file__).resolve().parents[2]
OUTPUT = ROOT / "docs" / "mcp-tool-reference.md"
ANALYTICS_TOOLS = analytics_tool_documents()

_ENDPOINTS = {
    "tesla_charging_history": ("GET", "/api/1/dx/charging/history"),
    "tesla_charging_invoice": ("GET", "/api/1/dx/charging/invoice/{invoice_id}"),
    "tesla_drivers": ("GET", "/api/1/vehicles/{vin}/drivers"),
    "tesla_feature_config": ("GET", "/api/1/users/feature_config"),
    "tesla_fleet_status": ("POST", "/api/1/vehicles/fleet_status"),
    "tesla_fleet_telemetry_config_get": (
        "GET",
        "/api/1/vehicles/{vin}/fleet_telemetry_config",
    ),
    "tesla_fleet_telemetry_errors": (
        "GET",
        "/api/1/vehicles/{vin}/fleet_telemetry_errors",
    ),
    "tesla_list_vehicles": ("GET", "/api/1/vehicles"),
    "tesla_me": ("GET", "/api/1/users/me"),
    "tesla_mobile_enabled": ("GET", "/api/1/vehicles/{vin}/mobile_enabled"),
    "tesla_nearby_charging_sites": (
        "GET",
        "/api/1/vehicles/{vin}/nearby_charging_sites",
    ),
    "tesla_orders": ("GET", "/api/1/users/orders"),
    "tesla_recent_alerts": ("GET", "/api/1/vehicles/{vin}/recent_alerts"),
    "tesla_release_notes": ("GET", "/api/1/vehicles/{vin}/release_notes"),
    "tesla_service_data": ("GET", "/api/1/vehicles/{vin}/service_data"),
    "tesla_vehicle": ("GET", "/api/1/vehicles/{vin}"),
    "tesla_vehicle_data": ("GET", "/api/1/vehicles/{vin}/vehicle_data"),
    "tesla_wake_up": ("POST", "/api/1/vehicles/{vin}/wake_up"),
}

_PURPOSES = {
    "actuate_trunk": "Open the front trunk or actuate the rear trunk/liftgate selected by `which_trunk`.",
    "add_charge_schedule": "Create or replace a location-bound recurring or one-time charging schedule.",
    "add_precondition_schedule": "Create or replace a location-bound recurring or one-time preconditioning schedule.",
    "adjust_volume": "Set the in-cabin media playback volume; Tesla may require an occupant and mobile access.",
    "auto_conditioning_start": "Start cabin climate preconditioning.",
    "auto_conditioning_stop": "Stop cabin climate preconditioning.",
    "cancel_software_update": "Cancel an update-install countdown before installation has begun.",
    "charge_max_range": "Select Tesla's max-range charging mode for exceptional long-trip use.",
    "charge_port_door_close": "Close a motorized charge-port door when no cable or vehicle state blocks it.",
    "charge_port_door_open": "Open the charge-port door while the vehicle is parked and eligible.",
    "charge_standard": "Return charging to the vehicle's standard/default charge-limit mode.",
    "charge_start": "Start charging when a powered cable is connected and charging is eligible.",
    "charge_stop": "Stop an active charging session.",
    "door_lock": "Lock the vehicle.",
    "door_unlock": "Unlock the vehicle; this is a security-sensitive physical-access operation.",
    "flash_lights": "Briefly flash the exterior lights while the vehicle is parked.",
    "guest_mode": "Enable or disable Tesla Guest Mode and its restricted-access behavior.",
    "honk_horn": "Sound the horn while the vehicle is parked.",
    "media_next_fav": "Move to the next favorite in the active media source.",
    "media_next_track": "Move to the next media track.",
    "media_prev_fav": "Move to the previous favorite in the active media source.",
    "media_prev_track": "Move to the previous media track.",
    "media_toggle_playback": "Toggle the active media source between playing and paused.",
    "media_volume_down": "Lower media volume by one vehicle-defined step.",
    "media_volume_up": "Raise media volume by one vehicle-defined step.",
    "navigation_gps_request": "Start navigation to coordinates, optionally at a specified stop order.",
    "navigation_request": "Send Tesla's structured destination object to in-vehicle navigation.",
    "navigation_sc_request": "Start navigation to a Tesla Supercharger identified by Tesla's numeric ID.",
    "navigation_waypoints_request": "Send an encoded waypoint list to in-vehicle navigation.",
    "parental_controls_activate": "Activate configured Parental Controls with the existing four-digit PIN.",
    "parental_controls_deactivate": "Deactivate Parental Controls using the current four-digit PIN.",
    "parental_controls_enable_setting": "Enable or disable one parental setting before Parental Controls is activated.",
    "parental_controls_set_speed_limit": "Set the Parental Controls maximum speed in miles per hour before activation.",
    "remote_auto_seat_climate_request": "Configure automatic heating/cooling for a selected seat while climate is running.",
    "remote_auto_steering_wheel_heat_climate_request": "Enable or disable automatic steering-wheel heating while climate is running.",
    "remote_boombox": "Play a supported external-speaker sound: `0` random fart or `2000` locate ping.",
    "remote_seat_cooler_request": "Set cooling level for a selected seat while climate is running.",
    "remote_seat_heater_request": "Set heating level for a selected seat while climate is running.",
    "remote_start_drive": "Enable keyless remote driving; this is not climate start and is security-sensitive.",
    "remote_steering_wheel_heat_level_request": "Set the steering-wheel heat level while climate is running.",
    "remote_steering_wheel_heater_request": "Enable or disable non-automatic steering-wheel heat while climate is running.",
    "remove_charge_schedule": "Remove a charging schedule by its Tesla schedule ID.",
    "remove_precondition_schedule": "Remove a preconditioning schedule by its Tesla schedule ID.",
    "schedule_software_update": "Schedule the available vehicle software update after the requested delay.",
    "set_bioweapon_mode": "Enable or disable Bioweapon Defense Mode, with an explicit manual-override flag.",
    "set_cabin_overheat_protection": "Enable or disable Cabin Overheat Protection and choose fan-only behavior.",
    "set_charge_limit": "Set the vehicle's requested charge-limit percentage.",
    "set_charging_amps": "Set the requested charging-current limit in amperes.",
    "set_climate_keeper_mode": "Set climate keeper mode: 0 off, 1 keep, 2 dog, or 3 camp.",
    "set_cop_temp": "Set Cabin Overheat Protection threshold: 0 low, 1 medium, or 2 high.",
    "set_pin_to_drive": "Enable or disable PIN to Drive using a four-digit password; security-sensitive.",
    "set_preconditioning_max": "Enable or disable maximum preconditioning with an explicit manual override.",
    "set_sentry_mode": "Enable or disable Sentry Mode.",
    "set_temps": "Set driver and passenger cabin-temperature targets.",
    "set_valet_mode": "Enable or disable Valet Mode using its four-digit password.",
    "set_vehicle_name": "Change the vehicle name when Guest Mode does not block it.",
    "speed_limit_activate": "Activate Speed Limit Mode with its four-digit PIN.",
    "speed_limit_clear_pin": "Deactivate Speed Limit Mode and clear its PIN using the current PIN.",
    "speed_limit_deactivate": "Deactivate Speed Limit Mode using the current PIN.",
    "speed_limit_set_limit": "Set the Speed Limit Mode maximum in miles per hour.",
    "sun_roof_control": "Stop, close, or vent a supported sunroof.",
    "trigger_homelink": "Trigger a paired HomeLink device near the supplied user coordinates.",
    "upcoming_calendar_entries": "Send serialized upcoming calendar entries to the vehicle.",
    "window_control": "Vent or close windows on a parked vehicle; closing may require nearby user coordinates.",
}

_ARGUMENT_NOTES = {
    "auto_climate_on": "Whether automatic seat climate should be active.",
    "auto_seat_position": "Tesla seat-position integer.",
    "calendar_data": "Tesla-compatible serialized calendar payload; treated as sensitive and never audited.",
    "charging_amps": "Requested current in amperes; the vehicle may clamp or reject unsupported values.",
    "command": "Window action.",
    "days_of_week": "Tesla schedule weekday encoding.",
    "driver_temp": "Driver-zone target temperature in Tesla's API units.",
    "end_time": "RFC 3339 timestamp for history reads, or minutes after local midnight for schedules.",
    "endpoints": "One or more explicit `vehicle_data` sections; duplicates are rejected.",
    "id": "Tesla schedule or Supercharger identifier, depending on the tool.",
    "lat": "Latitude; sensitive and excluded from command audit parameters.",
    "limit_mph": "Maximum speed in miles per hour.",
    "lon": "Longitude; sensitive and excluded from command audit parameters.",
    "offset_sec": "Delay before update installation, in seconds.",
    "order": "Optional navigation stop order.",
    "page_size": "Requested page size, at most 100.",
    "passenger_temp": "Passenger-zone target temperature in Tesla's API units.",
    "password": "Exactly four digits; never logged or stored in command audit.",
    "percent": "Requested whole-number charge-limit percentage.",
    "pin": "Exactly four digits; never logged or stored in command audit.",
    "precondition_time": "Preconditioning time in minutes after local midnight.",
    "seat_cooler_level": "Tesla seat-cooling level integer.",
    "seat_position": "Tesla seat-position integer.",
    "sound": "External-speaker sound ID: 0 random fart; 2000 locate ping.",
    "start_time": "RFC 3339 timestamp for history reads, or minutes after local midnight for schedules.",
    "timestamp_ms": "Navigation request timestamp represented as milliseconds.",
    "token": "Tesla HomeLink token; sensitive and never written to command audit.",
    "vehicle_id": "Opaque Woodhouse vehicle ID. Omit only when exactly one eligible vehicle exists.",
    "value": "Tesla structured navigation destination object; treated as sensitive.",
    "waypoints": "Tesla-compatible encoded waypoint list; treated as sensitive.",
}


def endpoint(spec: ToolSpec) -> tuple[str, str]:
    known = _ENDPOINTS.get(spec.name)
    if known is not None:
        return known
    if not spec.write or spec.client_method not in COMMAND_NAMES:
        raise ValueError(f"MCP endpoint mapping is missing for {spec.name}")
    return "POST", f"/api/1/vehicles/{{vin}}/command/{spec.client_method}"


def purpose(spec: ToolSpec) -> str:
    return _PURPOSES.get(spec.client_method, spec.description)


def schema_summary(schema: dict[str, Any]) -> str:
    pieces: list[str] = []
    value_type = schema.get("type", "any")
    if isinstance(value_type, list):
        pieces.append(" or ".join(str(item) for item in value_type))
    else:
        pieces.append(str(value_type))
    if "enum" in schema:
        pieces.append("one of " + ", ".join(f"`{item}`" for item in schema["enum"]))
    if "const" in schema:
        pieces.append(f"must be `{json.dumps(schema['const'])}`")
    bounds = []
    if "minimum" in schema:
        bounds.append(f">= {schema['minimum']}")
    if "maximum" in schema:
        bounds.append(f"<= {schema['maximum']}")
    if bounds:
        pieces.append(" and ".join(bounds))
    if schema.get("format"):
        pieces.append(str(schema["format"]))
    if schema.get("items"):
        pieces.append(f"items: {schema_summary(schema['items'])}")
    if "minItems" in schema:
        pieces.append(f"at least {schema['minItems']} item(s)")
    if schema.get("uniqueItems") is True:
        pieces.append("unique items")
    return "; ".join(pieces)


def tool_section(spec: ToolSpec) -> list[str]:
    method, path = endpoint(spec)
    schema = spec.input_schema()
    required = set(schema.get("required", []))
    lines = [
        f"### `{spec.name}`",
        "",
        purpose(spec),
        "",
        f"- Tesla operation: `{method} {path}`",
        f"- Tesla scope: `{spec.required_scope}`",
        f"- Vehicle wake: `{spec.wake_behavior}`",
        f"- Risk: `{spec.risk}`",
        f"- Retry: `{spec.retry_policy}`",
        f"- Audit: `{spec.audit_behavior}`",
        "",
        "Arguments:",
        "",
        "| Name | Required | Type/constraints | Meaning |",
        "|---|---:|---|---|",
    ]
    properties = schema.get("properties", {})
    if not properties:
        lines.append("| _none_ | — | — | The authenticated account is derived from OAuth. |")
    for name, value in properties.items():
        note = _ARGUMENT_NOTES.get(name, value.get("description", "Tesla request field."))
        lines.append(
            f"| `{name}` | {'yes' if name in required else 'no'} | "
            f"{schema_summary(value)} | {note} |"
        )
    lines.extend(
        [
            "",
            (
                "Result: a sanitized structured result with `correlation_id`. "
                + (
                    "The explicit wake returns the vehicle identity, display name, and live state."
                    if spec.client_method == "wake_up"
                    else (
                        "Commands also return Tesla's success/reason outcome and, when an "
                        "automatic wake was needed, `wake_correlation_id`."
                        if spec.write
                        else "The Tesla response remains live Fleet API data, not BigQuery history."
                    )
                )
            ),
            "",
        ]
    )
    return lines


def analytics_tool_section(document: dict[str, Any]) -> list[str]:
    name = str(document["name"])
    schema = document["inputSchema"]
    required = set(schema.get("required", []))
    lines = [
        f"### `{name}`",
        "",
        str(document["description"]),
        "",
        "- Data source: authenticated user's server-derived BigQuery default dataset",
        "- Platform scope: `mcp:access`",
        "- Vehicle wake: `never`",
        "- Risk: `read_only`",
        "- Retry: no automatic retry after execution begins",
        "- Audit/logging: query job metadata only; SQL and result rows are excluded",
        "",
        "Arguments:",
        "",
        "| Name | Required | Type/constraints | Meaning |",
        "|---|---:|---|---|",
    ]
    properties = schema.get("properties", {})
    if not properties:
        lines.append("| _none_ | — | — | Identity and dataset are derived from OAuth. |")
    for argument, value in properties.items():
        lines.append(
            f"| `{argument}` | {'yes' if argument in required else 'no'} | "
            f"{schema_summary(value)} | {value.get('description', '')} |"
        )
    if name == "get_analytics_schema":
        result = (
            "Result: user-safe object/field descriptions, join keys, partition hints, "
            "examples, and active query limits; the physical dataset ID is not returned."
        )
    else:
        result = (
            "Result: bounded columns and rows plus truncation, job ID, duration, referenced "
            "in-scope objects, and processed/billed bytes. SQL is AST-validated, canonicalized, "
            "dry-run first, capped at 1 GiB billed, 30 seconds, 1,000 returned rows, "
            "and 1 MiB of serialized result data. BigQuery can aggregate or correlate "
            "more source rows; the row and result-size limits bound only the response "
            "returned through MCP. Errors retain error/message/correlation_id and add the "
            "validation, dry-run, or execution phase. BigQuery reason and line/column "
            "diagnostics are included when available after private infrastructure identifiers "
            "are sanitized; failed jobs may also include safe job/byte metadata."
        )
    lines.extend(["", result, ""])
    return lines


def render_reference() -> str:
    undocumented_commands = {
        spec.client_method
        for spec in MCP_TOOL_SPECS
        if spec.write and spec.client_method != "wake_up" and spec.client_method not in _PURPOSES
    }
    if undocumented_commands:
        raise ValueError(
            "MCP command purposes are missing: " + ", ".join(sorted(undocumented_commands))
        )
    lines = [
        "# MCP Tool Reference",
        "",
        "**Generated from the live typed registry. Do not edit by hand.** Run",
        "`uv run python scripts/dev/generate-mcp-tool-reference.py` after changing a tool.",
        "",
        "This is the argument-level reference for ChatGPT-facing Woodhouse tools. Tesla",
        "paths are shown for traceability, but callers cannot supply VINs, paths, methods,",
        "tokens, user IDs, or dataset IDs. Woodhouse derives identity and ownership.",
        "The endpoint mapping and behavior were re-audited against Tesla's official Fleet",
        "API documentation on 2026-08-24.",
        "",
        "## Common behavior",
        "",
        "- OAuth requires `mcp:access`; the server separately enforces the Tesla scope shown.",
        "- `vehicle_id` is an opaque internal ID. Omission works only for one eligible vehicle.",
        "- Read tools never wake implicitly. Command tools perform one state check and at most",
        "  one automatic wake, then dispatch the command exactly once.",
        "- A missing command response is indeterminate and is never retried automatically.",
        "- Security-sensitive tools require `explicit_current_turn_intent=true`.",
        "- Expected safe errors include `vehicle_ambiguous`, `vehicle_unavailable`,",
        "  `vehicle_not_owned`, `reauthorization_required`, validation errors, Tesla rejection,",
        "  and indeterminate transport failure. Use `correlation_id` for redacted logs/audit.",
        "",
        f"## Tool index ({len(MCP_TOOL_SPECS) + len(ANALYTICS_TOOLS)})",
        "",
        "| Tool | Tesla operation | Scope | Risk | Wake |",
        "|---|---|---|---|---|",
    ]
    for spec in MCP_TOOL_SPECS:
        method, path = endpoint(spec)
        lines.append(
            f"| [`{spec.name}`](#{spec.name.replace('_', '-')}) | `{method} {path}` | "
            f"`{spec.required_scope}` | `{spec.risk}` | `{spec.wake_behavior}` |"
        )
    for document in ANALYTICS_TOOLS:
        name = str(document["name"])
        lines.append(
            f"| [`{name}`](#{name.replace('_', '-')}) | `BigQuery read-only` | "
            "`mcp:access` | `read_only` | `never` |"
        )
    lines.extend(["", "## Detailed tools", ""])
    for spec in MCP_TOOL_SPECS:
        lines.extend(tool_section(spec))
    for document in ANALYTICS_TOOLS:
        lines.extend(analytics_tool_section(document))
    return "\n".join(lines).rstrip() + "\n"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--check",
        action="store_true",
        help="Fail when the checked-in reference differs from generated output.",
    )
    args = parser.parse_args()
    rendered = render_reference()
    if args.check:
        if not OUTPUT.is_file() or OUTPUT.read_text(encoding="utf-8") != rendered:
            raise SystemExit("docs/mcp-tool-reference.md is stale; regenerate it with this script")
        return
    OUTPUT.write_text(rendered, encoding="utf-8")


if __name__ == "__main__":
    main()
