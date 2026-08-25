"""Minimal server-rendered onboarding pages with no client-side secrets."""

from __future__ import annotations

import json
from collections.abc import Mapping
from html import escape
from urllib.parse import quote


def onboarding_page(
    *,
    vehicles: list[dict[str, object]] | None = None,
    csrf_token: str | None = None,
    message: str | None = None,
) -> bytes:
    """Render the public sign-in page or one authenticated user's vehicles."""
    if csrf_token is None:
        content = """
        <section class="card">
          <p class="eyebrow">Private access</p>
          <h1>Woodhouse vehicle setup</h1>
          <p>Your email must be approved by the operator before you sign in.</p>
          <a class="button" href="/auth/login">Sign in with Google</a>
        </section>
        """
        return _document(content)

    cards = "".join(_vehicle_card(vehicle, csrf_token) for vehicle in vehicles or [])
    if not cards:
        cards = (
            '<div class="empty">No Tesla vehicles are connected yet. '
            "Authorize Tesla to discover every vehicle on your account.</div>"
        )
    notice = f'<p class="notice">{escape(message)}</p>' if message else ""
    content = f"""
      <header class="topbar">
        <div><span class="mark">W</span><span>Woodhouse</span></div>
        <form method="post" action="/auth/logout">
          <input type="hidden" name="csrf_token" value="{escape(csrf_token, quote=True)}">
          <button class="link-button" type="submit">Sign out</button>
        </form>
      </header>
      <main>
        <section class="hero">
          <p class="eyebrow">Approved account</p>
          <h1>Connect and pair your Tesla vehicles</h1>
          <p>Authorization discovers every vehicle. Virtual Key pairing is completed separately
          in the Tesla app for each vehicle that requires it.</p>
          {notice}
          <form method="post" action="/onboarding/tesla/start">
            <input type="hidden" name="csrf_token" value="{escape(csrf_token, quote=True)}">
            <button class="button" type="submit">Authorize or reconnect Tesla</button>
          </form>
        </section>
        <section class="vehicles" aria-label="Tesla vehicles">{cards}</section>
      </main>
    """
    return _document(content)


def error_page(title: str, message: str, *, retry_path: str = "/") -> bytes:
    content = f"""
      <section class="card">
        <p class="eyebrow">Setup interrupted</p>
        <h1>{escape(title)}</h1>
        <p>{escape(message)}</p>
        <a class="button" href="{escape(retry_path, quote=True)}">Continue</a>
      </section>
    """
    return _document(content)


def telemetry_configuration_page(
    document: Mapping[str, object], csrf_token: str, *, message: str | None = None
) -> bytes:
    """Render the exact safe desired/current plan and guarded operator actions."""
    vehicle_id = str(document.get("vehicle_id", ""))
    display_name = str(document.get("display_name") or "Tesla vehicle")
    desired_hash = str(document.get("desired_config_hash", ""))
    diff = document.get("diff", {})
    persisted = document.get("persisted", {})
    transport_opt_in = bool(
        persisted.get("transport_maintenance_opt_in", False)
        if isinstance(persisted, dict)
        else False
    )
    transport_checked = " checked" if transport_opt_in else ""
    status = diff.get("status") if isinstance(diff, dict) else "unknown"
    encoded_id = quote(vehicle_id, safe="")
    notice = f'<p class="notice">{escape(message)}</p>' if message else ""
    safe_json = escape(json.dumps(document, indent=2, sort_keys=True))
    content = f"""
      <header class="topbar">
        <div><span class="mark">W</span><span>Woodhouse</span></div>
        <a class="link-button" href="/onboarding">Back to vehicles</a>
      </header>
      <main>
        <section class="hero telemetry-hero">
          <p class="eyebrow">Operator checkpoint</p>
          <h1>{escape(display_name)} telemetry</h1>
          <p>Review the complete redacted desired-versus-current configuration below. Applying,
          repairing, and removing are per-vehicle operations and require explicit approval.</p>
          {notice}
          <p><span class="badge {"ok" if status == "in_sync" else "pending"}">
            {escape(str(status))}</span></p>
        </section>
        <section class="vehicle telemetry-plan">
          <h2>Exact configuration plan</h2>
          <pre>{safe_json}</pre>
          <div class="actions telemetry-actions">
            <form method="post" action="/onboarding/vehicles/{encoded_id}/telemetry/verify">
              <input type="hidden" name="csrf_token" value="{escape(csrf_token, quote=True)}">
              <p>Read Tesla's current configuration and errors. If it exactly matches this
                plan and is synchronized, record the trusted profile version and hash without
                changing or waking the vehicle.</p>
              <button class="button ghost" type="submit">Verify and record provenance</button>
            </form>
            <form method="post" action="/onboarding/vehicles/{encoded_id}/telemetry/apply">
              <input type="hidden" name="csrf_token" value="{escape(csrf_token, quote=True)}">
              <input type="hidden" name="expected_config_hash"
                value="{escape(desired_hash, quote=True)}">
              <label><input required type="checkbox" name="confirm" value="yes">
                I approve applying this exact configuration to this vehicle.</label>
              <label><input type="checkbox" name="transport_maintenance_opt_in"
                value="yes"{transport_checked}>
                Permit future automatic transport-only trust maintenance for this vehicle.</label>
              <button class="button" type="submit">Apply or repair configuration</button>
            </form>
            <form method="post" action="/onboarding/vehicles/{encoded_id}/telemetry/remove">
              <input type="hidden" name="csrf_token" value="{escape(csrf_token, quote=True)}">
              <label><input required type="checkbox" name="confirm" value="yes">
                I approve removing telemetry from this vehicle.</label>
              <button class="button danger" type="submit">Remove configuration</button>
            </form>
            <form method="post" action="/onboarding/vehicles/{encoded_id}/telemetry/reconcile">
              <input type="hidden" name="csrf_token" value="{escape(csrf_token, quote=True)}">
              <label><input required type="checkbox" name="confirm" value="yes">
                Use this vehicle as the canary for an opted-in transport-only migration.</label>
              <button class="button ghost" type="submit">Reconcile transport trust</button>
            </form>
          </div>
          <p>Persisted state: <code>{escape(json.dumps(persisted, sort_keys=True))}</code></p>
        </section>
      </main>
    """
    return _document(content)


def _vehicle_card(vehicle: dict[str, object], csrf_token: str) -> str:
    vehicle_id = str(vehicle.get("vehicle_id", ""))
    name = str(vehicle.get("display_name") or "Tesla vehicle")
    state = str(vehicle.get("state") or "unknown")
    pairing = str(vehicle.get("virtual_key_status") or "unknown")
    pairing_url = str(vehicle.get("virtual_key_pairing_url") or "")
    status_class = "ok" if pairing == "paired" else "pending"
    encoded_id = quote(vehicle_id, safe="")
    pair_action = ""
    if pairing != "paired" and pairing_url.startswith("https://www.tesla.com/_ak/"):
        pair_action = (
            f'<a class="button secondary" rel="noopener noreferrer" href="'
            f'{escape(pairing_url, quote=True)}">Pair Virtual Key in Tesla</a>'
        )
    telemetry_action = ""
    if pairing == "paired":
        telemetry_action = (
            f'<a class="button ghost" href="/onboarding/vehicles/{encoded_id}'
            f'/telemetry">Inspect telemetry configuration</a>'
        )
    return f"""
      <article class="vehicle">
        <div class="vehicle-heading"><h2>{escape(name)}</h2>
          <span class="badge {status_class}">{escape(pairing)}</span></div>
        <dl><div><dt>Vehicle state</dt><dd>{escape(state)}</dd></div>
            <div><dt>Internal vehicle ID</dt><dd><code>{escape(vehicle_id)}</code></dd></div></dl>
        <div class="actions">{pair_action}{telemetry_action}
          <form method="post" action="/onboarding/vehicles/{encoded_id}/refresh">
            <input type="hidden" name="csrf_token" value="{escape(csrf_token, quote=True)}">
            <button class="button ghost" type="submit">Refresh pairing status</button>
          </form>
        </div>
      </article>
    """


def _document(content: str) -> bytes:
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Woodhouse vehicle setup</title>
  <style>
    :root {{ color-scheme: dark; font-family: Inter, ui-sans-serif, system-ui, sans-serif; }}
    * {{ box-sizing: border-box; }}
    body {{ margin: 0; min-height: 100vh; color: #f7f7f2; background: #0d1110;
      background-image: radial-gradient(circle at 10% 0%, #24372e 0, transparent 38%); }}
    main, .card {{ width: min(920px, calc(100% - 32px)); margin: 0 auto; }}
    .card {{ margin-top: 12vh; max-width: 620px; padding: 48px; border: 1px solid #33483e;
      border-radius: 24px; background: rgba(19, 27, 23, .94); box-shadow: 0 24px 80px #0008; }}
    .topbar {{ display: flex; justify-content: space-between; align-items: center;
      padding: 20px 5vw;
      border-bottom: 1px solid #25332c; }}
    .topbar > div {{ display: flex; align-items: center; gap: 12px; font-weight: 700; }}
    .mark {{ display: grid; place-items: center; width: 32px; height: 32px; border-radius: 10px;
      color: #0d1110; background: #9fe3bd; }}
    .hero {{ padding: 72px 0 36px; max-width: 720px; }}
    h1 {{ margin: 8px 0 16px; font-size: clamp(2.2rem, 7vw, 4.7rem); line-height: .98; }}
    h2 {{ margin: 0; }} p {{ color: #b8c7bf; line-height: 1.65; }}
    .eyebrow {{ color: #9fe3bd; text-transform: uppercase; letter-spacing: .16em;
      font-size: .75rem; }}
    .button {{ display: inline-block; margin-top: 14px; padding: 12px 18px; border: 0;
      border-radius: 999px; font: inherit; font-weight: 700; text-decoration: none; cursor: pointer;
      color: #0d1110; background: #9fe3bd; }}
    .secondary {{ color: #e8fff2; background: #2e6b4b; }}
    .ghost {{ color: #e8fff2; background: transparent; border: 1px solid #466354; }}
    .danger {{ color: #fff; background: #8a2f35; }}
    .link-button {{ border: 0; color: #b8c7bf; background: transparent; cursor: pointer; }}
    .vehicles {{ display: grid; gap: 18px; padding-bottom: 72px; }}
    .vehicle {{ padding: 24px; border: 1px solid #33483e; border-radius: 18px;
      background: #151d19; }}
    .vehicle-heading, .actions, dl div {{ display: flex; align-items: center; gap: 12px; }}
    .vehicle-heading {{ justify-content: space-between; }} .actions {{ flex-wrap: wrap; }}
    .badge {{ padding: 5px 10px; border-radius: 999px; font-size: .8rem; }}
    .badge.ok {{ color: #9fe3bd; background: #1e4934; }}
    .badge.pending {{ color: #ffd999; background: #5b431d; }}
    dl {{ color: #b8c7bf; }} dl div {{ justify-content: space-between; padding: 9px 0;
      border-bottom: 1px solid #25332c; }} dd {{ margin: 0; text-align: right; }}
    code {{ font-size: .82em; word-break: break-all; }}
    .notice, .empty {{ padding: 14px 16px; border-radius: 12px; background: #1e4934;
      color: #dff9e9; }}
    .telemetry-hero {{ padding-bottom: 24px; }}
    .telemetry-plan {{ margin-bottom: 72px; }}
    pre {{ max-height: 70vh; overflow: auto; padding: 18px; border-radius: 12px;
      color: #dff9e9; background: #0a0e0c; white-space: pre-wrap; }}
    .telemetry-actions {{ align-items: stretch; }}
    .telemetry-actions form {{ display: grid; gap: 12px; flex: 1 1 360px; padding: 16px;
      border: 1px solid #33483e; border-radius: 12px; }}
    .telemetry-actions label {{ color: #b8c7bf; line-height: 1.45; }}
    @media (max-width: 600px) {{ .card {{ padding: 28px; }} dl div {{ align-items: start;
      flex-direction: column; }} dd {{ text-align: left; }} }}
  </style>
</head>
<body>{content}</body>
</html>""".encode()
