"""Small injectable HTTPS transport for the Tesla onboarding API surface."""

import json
import logging
import re
import secrets
import ssl
import time
from dataclasses import dataclass, field
from typing import Any, Protocol
from urllib.parse import parse_qsl, unquote, urlencode, urlsplit

import httpx2
from tesla_personal_platform.tesla_client.coverage import COMMAND_NAMES, IMPLEMENTED_ENDPOINTS
from tesla_personal_platform.tesla_client.observability import current_tesla_api_log_context

LOGGER = logging.getLogger("tesla_personal_platform.tesla_client.api_calls")

_TESLA_API_HOSTS = frozenset(
    {
        "auth.tesla.com",
        "fleet-auth.prd.vn.cloud.tesla.com",
        "fleet-api.prd.na.vn.cloud.tesla.com",
        "fleet-api.prd.eu.vn.cloud.tesla.com",
        "fleet-api.prd.cn.vn.cloud.tesla.cn",
    }
)

_COLLECTION_VEHICLE_ROUTES = frozenset(
    {"fleet_status", "fleet_telemetry_config", "fleet_telemetry_config_jws"}
)
_SENSITIVE_REQUEST_KEYS = frozenset(
    {
        "access_token",
        "authorization",
        "calendar_data",
        "client_secret",
        "code",
        "id_token",
        "lat",
        "latitude",
        "lon",
        "longitude",
        "password",
        "pin",
        "refresh_token",
        "routable_message",
        "token",
        "vin",
        "vins",
    }
)
_EMAIL_PATTERN = re.compile(r"\b[^\s@]+@[^\s@]+\.[^\s@]+\b")
_JWT_PATTERN = re.compile(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b")
_VIN_PATTERN = re.compile(r"\b[A-HJ-NPR-Z0-9]{17}\b", re.IGNORECASE)
_URL_PATTERN = re.compile(r"https?://\S+", re.IGNORECASE)
_PRECISE_COORDINATE_PATTERN = re.compile(r"(?<!\w)-?\d{1,3}\.\d{4,}(?!\w)")
_LABELED_COORDINATE_PATTERN = re.compile(
    r"(?i)\b(?:lat(?:itude)?|lon(?:gitude)?|location|coordinates?)\b"
    r"(?:\s*(?:=|:|at))?\s*-?\d{1,3}(?:\.\d+)?"
)
_COORDINATE_PAIR_PATTERN = re.compile(
    r"(?<![\w.])-?\d{1,3}(?:\.\d+)?\s*[,/]\s*-?\d{1,3}(?:\.\d+)?(?![\w.])"
)
_CREDENTIAL_PATTERN = re.compile(
    r"(?i)(?:"
    r"[\"']?(?:access[_ -]?token|refresh[_ -]?token|id[_ -]?token|client[_ -]?secret|"
    r"api[_ -]?key|session[_ -]?(?:id|token)|token|secret|credential|authorization|"
    r"bearer|password|passcode|pin|code)[\"']?\s*(?::|=)\s*"
    r"[\"']?[^\"'\s,;&}]+[\"']?"
    r"|\b(?:token|secret|credential|authorization|bearer|password|passcode|pin|code)"
    r"\s+[^\s,;&}]+"
    r")"
)
_SAFE_DIAGNOSTIC_KEYS = ("error", "error_description", "message")
_MAX_DIAGNOSTIC_BODY_BYTES = 64 * 1024


@dataclass(frozen=True, slots=True)
class HttpResponse:
    """Transport response whose body is deliberately absent from repr/log output."""

    status: int
    body: bytes = field(repr=False)
    content_type: str | None = None
    headers: dict[str, str] = field(default_factory=dict, repr=False)

    def json(self) -> object:
        return json.loads(self.body.decode("utf-8"))


class HttpTransport(Protocol):
    """Injectable HTTP boundary used by tests and production."""

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        form: dict[str, str] | None = None,
        json_body: object | None = None,
    ) -> HttpResponse:
        """Execute one bounded HTTPS request."""
        ...


class HttpxTransport:
    """Pooled HTTPX2 transport restricted to approved Tesla HTTPS hosts."""

    def __init__(
        self,
        timeout_seconds: float = 20.0,
        *,
        client: httpx2.Client | None = None,
    ) -> None:
        self._owns_client = client is None
        self._client = client or httpx2.Client(
            timeout=timeout_seconds,
            follow_redirects=False,
            trust_env=False,
        )

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        form: dict[str, str] | None = None,
        json_body: object | None = None,
    ) -> HttpResponse:
        if form is not None and json_body is not None:
            raise ValueError("form and json_body are mutually exclusive")
        request_headers = dict(headers or {})
        body_size = 0
        if form is not None:
            body_size = len(urlencode(form).encode("utf-8"))
        elif json_body is not None:
            body_size = len(json.dumps(json_body, separators=(",", ":")).encode("utf-8"))

        parsed = urlsplit(url)
        if parsed.scheme != "https" or parsed.hostname not in _TESLA_API_HOSTS:
            raise ValueError("Tesla HTTP transport requires an approved Tesla HTTPS host")
        request_log = _TeslaRequestLog.create(
            method=method,
            parsed_url=parsed,
            destination=_destination(parsed.hostname),
            request_headers=request_headers,
            form=form,
            json_body=json_body,
            body_size=body_size,
        )
        request_log.started()
        try:
            response = self._client.request(
                method,
                url,
                headers=request_headers,
                data=form,
                json=json_body,
            )
            result = _http_response(response)
            request_log.completed(result)
            return result
        except httpx2.RequestError:
            # Never include the exception text: it may contain a credential-bearing URL.
            from tesla_personal_platform.tesla_client.errors import TeslaTransportError

            request_log.failed("transport_error")
            raise TeslaTransportError("Tesla Fleet API transport failed") from None
        except Exception:
            request_log.failed("unexpected_transport_error")
            raise

    def close(self) -> None:
        """Close the owned connection pool."""
        if self._owns_client:
            self._client.close()


class LocalCommandProxyTransport:
    """Send only typed vehicle-command requests to a pinned local TLS proxy."""

    def __init__(
        self,
        *,
        proxy_origin: str = "https://localhost:4443",
        ca_file: str,
        timeout_seconds: float = 20.0,
        client: httpx2.Client | None = None,
    ) -> None:
        parsed = urlsplit(proxy_origin)
        if (
            parsed.scheme != "https"
            or parsed.hostname not in {"localhost", "127.0.0.1", "::1"}
            or parsed.query
            or parsed.fragment
            or parsed.path not in {"", "/"}
        ):
            raise ValueError("Vehicle Command Proxy must use a loopback HTTPS origin")
        context = ssl.create_default_context(cafile=ca_file)
        self._origin = proxy_origin.rstrip("/")
        self._owns_client = client is None
        self._client = client or httpx2.Client(
            timeout=timeout_seconds,
            follow_redirects=False,
            trust_env=False,
            verify=context,
        )

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        form: dict[str, str] | None = None,
        json_body: object | None = None,
    ) -> HttpResponse:
        if form is not None:
            raise ValueError("Vehicle Command Proxy does not accept form requests")
        parsed = urlsplit(url)
        if parsed.scheme != "https" or parsed.hostname not in _TESLA_API_HOSTS:
            raise ValueError("Command source URL requires an approved Tesla HTTPS host")
        path_parts = parsed.path.split("/")
        typed_vehicle_command = (
            len(path_parts) == 7
            and path_parts[1:4] == ["api", "1", "vehicles"]
            and path_parts[5] == "command"
            and path_parts[6] in COMMAND_NAMES
        )
        signed_telemetry_config = parsed.path == "/api/1/vehicles/fleet_telemetry_config"
        if (
            method != "POST"
            or parsed.query
            or not (typed_vehicle_command or signed_telemetry_config)
        ):
            raise ValueError(
                "Vehicle Command Proxy transport accepts typed commands and signed telemetry "
                "configuration only"
            )

        request_headers = dict(headers or {})
        data = json.dumps(json_body or {}, separators=(",", ":")).encode("utf-8")
        request_log = _TeslaRequestLog.create(
            method="POST",
            parsed_url=parsed,
            destination="vehicle_command_proxy",
            request_headers=request_headers,
            form=None,
            json_body=json_body,
            body_size=len(data),
        )
        request_log.started()
        try:
            response = self._client.request(
                "POST",
                f"{self._origin}{parsed.path}",
                headers=request_headers,
                json=json_body or {},
            )
            result = _http_response(response)
            request_log.completed(result)
            return result
        except httpx2.RequestError:
            from tesla_personal_platform.tesla_client.errors import TeslaTransportError

            request_log.failed("transport_error")
            raise TeslaTransportError("Local Vehicle Command Proxy transport failed") from None
        except Exception:
            request_log.failed("unexpected_transport_error")
            raise

    def close(self) -> None:
        """Close the owned loopback connection pool."""
        if self._owns_client:
            self._client.close()


def _http_response(response: httpx2.Response) -> HttpResponse:
    return HttpResponse(
        status=response.status_code,
        body=response.content,
        content_type=response.headers.get("Content-Type"),
        headers={key.casefold(): value for key, value in response.headers.items()},
    )


@dataclass(slots=True)
class _TeslaRequestLog:
    call_id: str
    started_at: float
    fields: dict[str, object]
    secret_values: tuple[str, ...]

    @classmethod
    def create(
        cls,
        *,
        method: str,
        parsed_url: Any,
        destination: str,
        request_headers: dict[str, str],
        form: dict[str, str] | None,
        json_body: object | None,
        body_size: int,
    ) -> "_TeslaRequestLog":
        route = _route_template(parsed_url.path)
        context = current_tesla_api_log_context()
        endpoint = IMPLEMENTED_ENDPOINTS.get((method, route))
        fields: dict[str, object] = {
            "call_id": f"api_{secrets.token_hex(16)}",
            "method": method,
            "route": route,
            "operation": endpoint.method if endpoint is not None else _operation(route),
            "destination": destination,
            "region": _region(parsed_url.hostname),
            "attempt": context.attempt or 1,
            "request_body_bytes": body_size,
            "request_fields": _request_fields(form, json_body),
            "query_fields": sorted({key for key, _value in parse_qsl(parsed_url.query)}),
        }
        for name in (
            "correlation_id",
            "vehicle_id",
            "source",
            "flow_phase",
            "flow_iteration",
        ):
            value = getattr(context, name)
            if value is not None:
                fields[name] = value
        return cls(
            call_id=str(fields["call_id"]),
            started_at=time.monotonic(),
            fields=fields,
            secret_values=_request_secret_values(
                parsed_url.path,
                parsed_url.query,
                request_headers,
                form,
                json_body,
            ),
        )

    def started(self) -> None:
        _log_event({"event": "tesla_api_call", "phase": "started", **self.fields})

    def completed(self, response: HttpResponse) -> None:
        fields = {
            "event": "tesla_api_call",
            "phase": "completed",
            **self.fields,
            "status_code": response.status,
            "outcome": _http_outcome(response.status),
            "duration_ms": _duration_ms(self.started_at),
            "response_body_bytes": len(response.body),
        }
        content_type = _safe_content_type(response.content_type)
        if content_type is not None:
            fields["response_content_type"] = content_type
        if _should_summarize_response(response, self.fields):
            summary = _diagnostic_summary(response, self.secret_values)
            if summary:
                fields["response_summary"] = summary
        _log_event(fields)

    def failed(self, category: str) -> None:
        _log_event(
            {
                "event": "tesla_api_call",
                "phase": "failed",
                **self.fields,
                "outcome": "transport_error",
                "error_category": category,
                "duration_ms": _duration_ms(self.started_at),
            }
        )


def _log_event(fields: dict[str, object]) -> None:
    LOGGER.info(json.dumps(fields, sort_keys=True, separators=(",", ":")))


def _route_template(path: str) -> str:
    parts = [unquote(part) for part in path.split("/") if part]
    if parts[:3] == ["api", "1", "vehicles"]:
        if len(parts) >= 4 and parts[3] not in _COLLECTION_VEHICLE_ROUTES:
            parts[3] = "{vin}"
        if len(parts) >= 6 and parts[4] == "invitations":
            parts[5] = "{id}"
        return "/" + "/".join(parts)
    if parts[:5] == ["api", "1", "dx", "charging", "invoice"] and len(parts) >= 6:
        parts[5] = "{id}"
        return "/" + "/".join(parts)
    known = {
        "/api/1/dx/charging/history",
        "/api/1/invitations/redeem",
        "/api/1/partner_accounts",
        "/api/1/partner_accounts/fleet_telemetry_error_vins",
        "/api/1/partner_accounts/fleet_telemetry_errors",
        "/api/1/partner_accounts/public_key",
        "/api/1/users/feature_config",
        "/api/1/users/me",
        "/api/1/users/orders",
        "/api/1/users/region",
        "/oauth2/v3/discovery/thirdparty/keys",
        "/oauth2/v3/token",
    }
    normalized = "/" + "/".join(parts)
    return normalized if normalized in known else "/{unrecognized_tesla_route}"


def _operation(route: str) -> str:
    if route == "/oauth2/v3/token":
        return "oauth_token"
    if route == "/oauth2/v3/discovery/thirdparty/keys":
        return "oauth_jwks"
    if "/command/" in route:
        return route.rsplit("/", 1)[-1]
    if route == "/{unrecognized_tesla_route}":
        return "unknown"
    return route.rsplit("/", 1)[-1].replace("{vin}", "vehicle").replace("{id}", "item")


def _region(hostname: str | None) -> str:
    return {
        "fleet-api.prd.na.vn.cloud.tesla.com": "na",
        "fleet-api.prd.eu.vn.cloud.tesla.com": "eu",
        "fleet-api.prd.cn.vn.cloud.tesla.cn": "cn",
        "fleet-auth.prd.vn.cloud.tesla.com": "global",
        "auth.tesla.com": "global",
    }.get(hostname or "", "unknown")


def _destination(hostname: str | None) -> str:
    if hostname in {"auth.tesla.com", "fleet-auth.prd.vn.cloud.tesla.com"}:
        return "tesla_oauth"
    return "tesla_fleet_api"


def _request_fields(form: dict[str, str] | None, json_body: object | None) -> list[str]:
    if form is not None:
        return sorted(str(key) for key in form)
    if isinstance(json_body, dict):
        return sorted(str(key) for key in json_body)
    return []


def _request_secret_values(
    path: str,
    query: str,
    headers: dict[str, str],
    form: dict[str, str] | None,
    json_body: object | None,
) -> tuple[str, ...]:
    values: set[str] = set()
    for value in headers.values():
        if len(value) >= 4:
            values.add(value)
        if value.casefold().startswith("bearer ") and len(value) > 11:
            values.add(value[7:])
    values.update(_path_secret_values(path))
    for _key, value in parse_qsl(query):
        if len(value) >= 4:
            values.add(value)
    if form is not None:
        values.update(value for value in form.values() if len(value) >= 4)
    _collect_sensitive_values(json_body, values)
    return tuple(sorted(values, key=len, reverse=True))


def _path_secret_values(path: str) -> set[str]:
    parts = [unquote(part) for part in path.split("/") if part]
    values: set[str] = set()
    if parts[:3] == ["api", "1", "vehicles"]:
        if len(parts) >= 4 and parts[3] not in _COLLECTION_VEHICLE_ROUTES:
            values.add(parts[3])
        if len(parts) >= 6 and parts[4] == "invitations":
            values.add(parts[5])
    if parts[:5] == ["api", "1", "dx", "charging", "invoice"] and len(parts) >= 6:
        values.add(parts[5])
    return {value for value in values if len(value) >= 4}


def _collect_sensitive_values(value: object, output: set[str], *, key: str | None = None) -> None:
    if isinstance(value, dict):
        for nested_key, nested in value.items():
            _collect_sensitive_values(nested, output, key=str(nested_key).casefold())
        return
    if isinstance(value, (list, tuple)):
        for nested in value:
            _collect_sensitive_values(nested, output, key=key)
        return
    if isinstance(value, str) and (len(value) >= 4 or key in _SENSITIVE_REQUEST_KEYS):
        output.add(value)
        return
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        encoded = str(value)
        if len(encoded) >= 4 or key in _SENSITIVE_REQUEST_KEYS:
            output.add(encoded)


def _should_summarize_response(response: HttpResponse, fields: dict[str, object]) -> bool:
    if len(response.body) > _MAX_DIAGNOSTIC_BODY_BYTES:
        return False
    return not 200 <= response.status < 300 or fields.get("operation") in COMMAND_NAMES


def _diagnostic_summary(
    response: HttpResponse, secret_values: tuple[str, ...]
) -> dict[str, object]:
    try:
        document = response.json()
    except (UnicodeDecodeError, ValueError):
        return {}
    if not isinstance(document, dict):
        return {}
    summary: dict[str, object] = {}
    for key in _SAFE_DIAGNOSTIC_KEYS:
        value = _safe_diagnostic_value(document.get(key), secret_values)
        if value is not None:
            summary[key] = value
    payload = document.get("response")
    if isinstance(payload, dict):
        result = payload.get("result")
        if isinstance(result, bool):
            summary["result"] = result
        reason = _safe_diagnostic_value(payload.get("reason"), secret_values)
        if reason is not None:
            summary["reason"] = reason
    return summary


def _safe_diagnostic_value(value: object, secret_values: tuple[str, ...]) -> object | None:
    if isinstance(value, bool) or isinstance(value, (int, float)):
        return value
    if not isinstance(value, str) or not value:
        return None
    safe = " ".join(value.split())
    for secret in secret_values:
        safe = safe.replace(secret, "[REDACTED]")
    safe = _EMAIL_PATTERN.sub("[REDACTED_EMAIL]", safe)
    safe = _JWT_PATTERN.sub("[REDACTED_TOKEN]", safe)
    safe = _VIN_PATTERN.sub("[REDACTED_VIN]", safe)
    safe = _URL_PATTERN.sub("[REDACTED_URL]", safe)
    safe = _COORDINATE_PAIR_PATTERN.sub("[REDACTED_COORDINATE]", safe)
    safe = _LABELED_COORDINATE_PATTERN.sub("[REDACTED_COORDINATE]", safe)
    safe = _PRECISE_COORDINATE_PATTERN.sub("[REDACTED_COORDINATE]", safe)
    safe = _CREDENTIAL_PATTERN.sub("[REDACTED_CREDENTIAL]", safe)
    return safe[:256]


def _http_outcome(status: int) -> str:
    if 200 <= status < 300:
        return "success"
    if 300 <= status < 400:
        return "redirect_rejected"
    if 400 <= status < 500:
        return "client_error"
    return "server_error"


def _duration_ms(started_at: float) -> int:
    return max(0, round((time.monotonic() - started_at) * 1000))


def _safe_content_type(value: str | None) -> str | None:
    if value is None:
        return None
    media_type = value.split(";", 1)[0].strip().casefold()
    return media_type if re.fullmatch(r"[a-z0-9.+-]+/[a-z0-9.+-]+", media_type) else "other"
