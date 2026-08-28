"""Shared HTTP safety boundary and response helpers for the gateway ASGI app."""

from __future__ import annotations

from collections.abc import Mapping
from http import HTTPStatus
from http.cookies import CookieError, SimpleCookie
from typing import Final

from anyio import fail_after
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse, Response

MAX_REQUEST_BYTES: Final = 1_048_576
MAX_FORM_BYTES: Final = 16_384
REQUEST_BODY_TIMEOUT_SECONDS: Final = 15.0

HTML_SECURITY_HEADERS: Final = {
    "Cache-Control": "no-store",
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Content-Security-Policy": (
        "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; "
        "base-uri 'none'; frame-ancestors 'none'"
    ),
}
NO_STORE_HEADERS: Final = {
    "Cache-Control": "no-store",
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
}


class RequestBodyBoundaryMiddleware(BaseHTTPMiddleware):
    """Bound request bodies before routing, including the mounted MCP application."""

    async def dispatch(
        self,
        request: Request,
        call_next: RequestResponseEndpoint,
    ) -> Response:
        content_length = request.headers.get("content-length")
        if content_length is not None:
            try:
                if int(content_length) > MAX_REQUEST_BYTES:
                    return json_response(
                        HTTPStatus.REQUEST_ENTITY_TOO_LARGE, {"error": "body_too_large"}
                    )
            except ValueError:
                return json_response(HTTPStatus.BAD_REQUEST, {"error": "invalid_content_length"})
        try:
            with fail_after(REQUEST_BODY_TIMEOUT_SECONDS):
                body = await request.body()
        except TimeoutError:
            return json_response(HTTPStatus.REQUEST_TIMEOUT, {"error": "request_timeout"})
        if len(body) > MAX_REQUEST_BYTES:
            return json_response(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, {"error": "body_too_large"})
        return await call_next(request)


def json_response(
    status: HTTPStatus,
    document: object,
    *,
    headers: Mapping[str, str] | None = None,
) -> JSONResponse:
    return JSONResponse(
        document,
        status_code=int(status),
        headers={**NO_STORE_HEADERS, **dict(headers or {})},
    )


def html_response(
    status: HTTPStatus,
    body: bytes,
    *,
    cookies: tuple[str, ...] = (),
) -> HTMLResponse:
    response = HTMLResponse(body, status_code=int(status), headers=HTML_SECURITY_HEADERS)
    for cookie in cookies:
        response.headers.append("Set-Cookie", cookie)
    return response


def redirect_response(
    location: str,
    *,
    status: HTTPStatus = HTTPStatus.FOUND,
    cookies: tuple[str, ...] = (),
) -> RedirectResponse:
    response = RedirectResponse(
        location,
        status_code=int(status),
        headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"},
    )
    for cookie in cookies:
        response.headers.append("Set-Cookie", cookie)
    return response


def cookie_token(cookie_header: str | None, name: str) -> str | None:
    if not cookie_header:
        return None
    cookie = SimpleCookie()
    try:
        cookie.load(cookie_header)
    except (CookieError, ValueError):
        return None
    morsel = cookie.get(name)
    return morsel.value if morsel is not None and morsel.value else None
