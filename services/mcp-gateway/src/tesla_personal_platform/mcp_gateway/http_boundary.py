"""Shared HTTP safety boundary and response helpers for the gateway ASGI app."""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from http import HTTPStatus
from http.cookies import CookieError, SimpleCookie
from time import perf_counter
from typing import Final

from anyio import fail_after
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

MAX_REQUEST_BYTES: Final = 1_048_576
MAX_FORM_BYTES: Final = 16_384
REQUEST_BODY_TIMEOUT_SECONDS: Final = 15.0
LOGGER = logging.getLogger(__name__)

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


class RequestBodyBoundaryMiddleware:
    """Bound and replay HTTP bodies before routing, including chunked requests."""

    def __init__(
        self,
        app: ASGIApp,
        *,
        max_bytes: int = MAX_REQUEST_BYTES,
        timeout_seconds: float = REQUEST_BODY_TIMEOUT_SECONDS,
    ) -> None:
        self._app = app
        self._max_bytes = max_bytes
        self._timeout_seconds = timeout_seconds

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return

        content_length = _content_length(scope)
        if content_length is None:
            await _send_json(
                scope,
                receive,
                send,
                HTTPStatus.BAD_REQUEST,
                {"error": "invalid_content_length"},
            )
            return
        if content_length > self._max_bytes:
            await _send_json(
                scope,
                receive,
                send,
                HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                {"error": "body_too_large"},
            )
            return

        body = bytearray()
        disconnected = False
        too_large = False
        try:
            with fail_after(self._timeout_seconds):
                while True:
                    message = await receive()
                    if message["type"] == "http.disconnect":
                        disconnected = True
                        break
                    if message["type"] != "http.request":
                        continue
                    body.extend(message.get("body", b""))
                    if len(body) > self._max_bytes:
                        too_large = True
                        break
                    if not message.get("more_body", False):
                        break
        except TimeoutError:
            await _send_json(
                scope,
                receive,
                send,
                HTTPStatus.REQUEST_TIMEOUT,
                {"error": "request_timeout"},
            )
            return
        if too_large:
            await _send_json(
                scope,
                receive,
                send,
                HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                {"error": "body_too_large"},
            )
            return

        replayed = False

        async def replay_body() -> Message:
            nonlocal replayed
            if not replayed:
                replayed = True
                if disconnected:
                    return {"type": "http.disconnect"}
                return {"type": "http.request", "body": bytes(body), "more_body": False}
            return await receive()

        await self._app(scope, replay_body, send)


class SafeAccessLogMiddleware:
    """Emit structured access events containing the path but never its query string."""

    def __init__(self, app: ASGIApp) -> None:
        self._app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return
        started_at = perf_counter()
        status = int(HTTPStatus.INTERNAL_SERVER_ERROR)

        async def capture_status(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = int(message["status"])
            await send(message)

        try:
            await self._app(scope, receive, capture_status)
        finally:
            LOGGER.info(
                json.dumps(
                    {
                        "duration_ms": round((perf_counter() - started_at) * 1000, 3),
                        "event": "gateway_http_request",
                        "method": scope["method"],
                        "path": scope["path"],
                        "status": status,
                    },
                    separators=(",", ":"),
                    sort_keys=True,
                )
            )


def _content_length(scope: Scope) -> int | None:
    values = [value for name, value in scope["headers"] if name.lower() == b"content-length"]
    if not values:
        return 0
    if len(values) != 1:
        return None
    try:
        result = int(values[0])
    except ValueError:
        return None
    return result if result >= 0 else None


async def _send_json(
    scope: Scope,
    receive: Receive,
    send: Send,
    status: HTTPStatus,
    document: object,
) -> None:
    await json_response(status, document)(scope, receive, send)


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
