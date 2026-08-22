"""Small injectable HTTPS transport for the Tesla onboarding API surface."""

import json
import ssl
from dataclasses import dataclass, field
from typing import Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, HTTPSHandler, Request, build_opener

from tesla_personal_platform.tesla_client.coverage import COMMAND_NAMES

_TESLA_API_HOSTS = frozenset(
    {
        "auth.tesla.com",
        "fleet-auth.prd.vn.cloud.tesla.com",
        "fleet-api.prd.na.vn.cloud.tesla.com",
        "fleet-api.prd.eu.vn.cloud.tesla.com",
        "fleet-api.prd.cn.vn.cloud.tesla.cn",
    }
)


class _RejectRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        """Never forward a Tesla bearer credential across an HTTP redirect."""
        return None


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


class UrllibTransport:
    """Standard-library HTTPS transport with bounded request duration."""

    def __init__(self, timeout_seconds: float = 20.0) -> None:
        self._timeout_seconds = timeout_seconds
        self._opener = build_opener(_RejectRedirects)

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
        data: bytes | None = None
        if form is not None:
            data = urlencode(form).encode("utf-8")
            request_headers["Content-Type"] = "application/x-www-form-urlencoded"
        elif json_body is not None:
            data = json.dumps(json_body, separators=(",", ":")).encode("utf-8")
            request_headers["Content-Type"] = "application/json"

        parsed = urlsplit(url)
        if parsed.scheme != "https" or parsed.hostname not in _TESLA_API_HOSTS:
            raise ValueError("Tesla HTTP transport requires an approved Tesla HTTPS host")
        request = Request(url, data=data, headers=request_headers, method=method)  # noqa: S310
        try:
            with self._opener.open(request, timeout=self._timeout_seconds) as response:
                return HttpResponse(
                    status=response.status,
                    body=response.read(),
                    content_type=response.headers.get("Content-Type"),
                    headers={key.casefold(): value for key, value in response.headers.items()},
                )
        except HTTPError as error:
            return HttpResponse(
                status=error.code,
                body=error.read(),
                content_type=error.headers.get("Content-Type"),
                headers={key.casefold(): value for key, value in error.headers.items()},
            )
        except (TimeoutError, URLError):
            # Never include urllib's exception text: it may contain a credential-bearing URL.
            from tesla_personal_platform.tesla_client.errors import TeslaTransportError

            raise TeslaTransportError("Tesla Fleet API transport failed") from None


class LocalCommandProxyTransport:
    """Send only typed vehicle-command requests to a pinned local TLS proxy."""

    def __init__(
        self,
        *,
        proxy_origin: str = "https://localhost:4443",
        ca_file: str,
        timeout_seconds: float = 20.0,
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
        self._timeout_seconds = timeout_seconds
        self._opener = build_opener(_RejectRedirects, HTTPSHandler(context=context))

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
        if (
            method != "POST"
            or parsed.query
            or len(path_parts) != 7
            or path_parts[1:4] != ["api", "1", "vehicles"]
            or path_parts[5] != "command"
            or path_parts[6] not in COMMAND_NAMES
        ):
            raise ValueError("Vehicle Command Proxy transport accepts typed commands only")

        request_headers = dict(headers or {})
        data = json.dumps(json_body or {}, separators=(",", ":")).encode("utf-8")
        request_headers["Content-Type"] = "application/json"
        request = Request(  # noqa: S310 - fixed loopback origin validated above
            f"{self._origin}{parsed.path}",
            data=data,
            headers=request_headers,
            method="POST",
        )
        try:
            with self._opener.open(request, timeout=self._timeout_seconds) as response:
                return HttpResponse(
                    status=response.status,
                    body=response.read(),
                    content_type=response.headers.get("Content-Type"),
                    headers={key.casefold(): value for key, value in response.headers.items()},
                )
        except HTTPError as error:
            return HttpResponse(
                status=error.code,
                body=error.read(),
                content_type=error.headers.get("Content-Type"),
                headers={key.casefold(): value for key, value in error.headers.items()},
            )
        except (TimeoutError, URLError):
            from tesla_personal_platform.tesla_client.errors import TeslaTransportError

            raise TeslaTransportError("Local Vehicle Command Proxy transport failed") from None
