"""Gateway-wide ASGI request and access-log boundary tests."""

from __future__ import annotations

import asyncio
import importlib
import json
import logging
from types import SimpleNamespace
from typing import cast

import pytest
from starlette.testclient import TestClient
from starlette.types import ASGIApp, Message, Receive, Scope, Send
from tesla_personal_platform.auth import Authenticator, VerifiedIdentity
from tesla_personal_platform.auth.memory import InMemoryIdentityStore
from tesla_personal_platform.mcp_gateway.app import create_app
from tesla_personal_platform.mcp_gateway.auth_boundary import GatewayAuthBoundary
from tesla_personal_platform.mcp_gateway.gateway_runtime import GatewayRuntime
from tesla_personal_platform.mcp_gateway.http_boundary import RequestBodyBoundaryMiddleware


class RejectingVerifier:
    def verify(self, token: str) -> VerifiedIdentity:
        raise AssertionError(f"unexpected token verification: {token}")


def test_uvicorn_query_bearing_access_logger_is_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gateway_main = importlib.import_module("tesla_personal_platform.mcp_gateway.main")
    settings = SimpleNamespace(server=SimpleNamespace(host="127.0.0.1", port=8080))
    runtime = object()
    app = object()
    invocation: dict[str, object] = {}

    def run(called_app: object, **kwargs: object) -> None:
        invocation["app"] = called_app
        invocation.update(kwargs)

    monkeypatch.setattr(gateway_main, "configure_json_logging", lambda: None)
    monkeypatch.setattr(gateway_main, "GatewaySettings", lambda: settings)
    monkeypatch.setattr(gateway_main, "build_runtime", lambda value: runtime)
    monkeypatch.setattr(gateway_main, "create_app", lambda value: app)
    monkeypatch.setattr(gateway_main.uvicorn, "run", run)

    gateway_main.main()

    assert invocation["app"] is app
    assert invocation["access_log"] is False


def _scope() -> Scope:
    return cast(
        Scope,
        {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "POST",
            "scheme": "https",
            "path": "/mcp",
            "raw_path": b"/mcp",
            "query_string": b"",
            "root_path": "",
            "headers": [],
            "client": ("127.0.0.1", 1234),
            "server": ("woodhouse.example", 443),
        },
    )


def _run_boundary(
    messages: list[Message] | None,
    *,
    max_bytes: int = 8,
    timeout_seconds: float = 1,
    receive_override: Receive | None = None,
) -> tuple[bool, list[Message]]:
    routed = False
    sent: list[Message] = []

    async def downstream(_scope: Scope, receive: Receive, send: Send) -> None:
        nonlocal routed
        routed = True
        request = await receive()
        assert request["type"] == "http.request"
        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    pending = iter(messages or [])

    async def receive() -> Message:
        return next(pending)

    async def send(message: Message) -> None:
        sent.append(message)

    middleware = RequestBodyBoundaryMiddleware(
        cast(ASGIApp, downstream),
        max_bytes=max_bytes,
        timeout_seconds=timeout_seconds,
    )
    asyncio.run(middleware(_scope(), receive_override or receive, send))
    return routed, sent


def _response_document(messages: list[Message]) -> object:
    body = b"".join(
        message.get("body", b"") for message in messages if message["type"] == "http.response.body"
    )
    return json.loads(body)


def test_chunked_body_is_rejected_as_soon_as_cumulative_limit_is_crossed() -> None:
    routed, sent = _run_boundary(
        [
            {"type": "http.request", "body": b"12345", "more_body": True},
            {"type": "http.request", "body": b"6789", "more_body": False},
        ]
    )

    assert routed is False
    assert sent[0]["status"] == 413
    assert _response_document(sent) == {"error": "body_too_large"}


def test_request_body_timeout_is_absolute_across_the_whole_body() -> None:
    async def never_receive() -> Message:
        await asyncio.Event().wait()
        raise AssertionError("unreachable")

    routed, sent = _run_boundary(None, timeout_seconds=0.001, receive_override=never_receive)

    assert routed is False
    assert sent[0]["status"] == 408
    assert _response_document(sent) == {"error": "request_timeout"}


def test_gateway_access_log_omits_oauth_callback_query_values(
    caplog: pytest.LogCaptureFixture,
) -> None:
    boundary = GatewayAuthBoundary(Authenticator(RejectingVerifier(), InMemoryIdentityStore()))
    runtime = GatewayRuntime(boundary, None, None, None)
    with caplog.at_level(logging.INFO):
        with TestClient(create_app(runtime)) as client:
            response = client.get(
                "/oauth/callback?code=sensitive-code&state=sensitive-state",
                follow_redirects=False,
            )

    assert response.status_code == 503
    captured = "\n".join(
        record.getMessage()
        for record in caplog.records
        if record.name == "tesla_personal_platform.mcp_gateway.http_boundary"
    )
    assert '"path":"/oauth/callback"' in captured
    assert "sensitive-code" not in captured
    assert "sensitive-state" not in captured
