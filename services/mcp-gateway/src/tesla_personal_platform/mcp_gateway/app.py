"""Starlette application for Woodhouse onboarding, administration, and MCP."""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from http import HTTPStatus
from typing import Final, cast
from urllib.parse import parse_qs

from starlette.applications import Starlette
from starlette.datastructures import QueryParams
from starlette.middleware import Middleware
from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import BaseRoute, Mount, Route
from tesla_personal_platform.auth import (
    AuthenticationError,
    CallerIdentityClaimError,
    CrossUserAccessError,
    UserContext,
    assert_no_caller_identity_claims,
)
from tesla_personal_platform.mcp_gateway import SERVICE_NAME
from tesla_personal_platform.mcp_gateway.browser_auth import (
    WEB_SESSION_LIFETIME,
    BrowserAuthenticationError,
    BrowserSession,
)
from tesla_personal_platform.mcp_gateway.gateway_runtime import GatewayRuntime
from tesla_personal_platform.mcp_gateway.http_boundary import (
    MAX_FORM_BYTES,
    MAX_REQUEST_BYTES,
    RequestBodyBoundaryMiddleware,
    SafeAccessLogMiddleware,
)
from tesla_personal_platform.mcp_gateway.http_boundary import (
    cookie_token as _cookie_token,
)
from tesla_personal_platform.mcp_gateway.http_boundary import (
    html_response as _html,
)
from tesla_personal_platform.mcp_gateway.http_boundary import (
    json_response as _json,
)
from tesla_personal_platform.mcp_gateway.http_boundary import (
    redirect_response as _redirect,
)
from tesla_personal_platform.mcp_gateway.mcp_server import create_mcp_server
from tesla_personal_platform.mcp_gateway.onboarding_web import (
    error_page,
    onboarding_page,
    telemetry_configuration_page,
)
from tesla_personal_platform.mcp_gateway.telemetry_control import TelemetryConfigurationError
from tesla_personal_platform.mcp_gateway.tesla_onboarding import (
    InvalidOAuthStateError,
    TeslaOnboardingError,
)
from tesla_personal_platform.tesla_client import (
    TeslaAPIError,
    TeslaAuthenticationError,
    TeslaReauthorizationRequired,
)

TESLA_PUBLIC_KEY_PATH: Final = "/.well-known/appspecific/com.tesla.3p.public-key.pem"
SESSION_COOKIE_NAME: Final = "__Host-tpp_session"
LOGIN_COOKIE_NAME: Final = "__Host-tpp_login"
LOGGER = logging.getLogger(__name__)


def health_document() -> dict[str, str]:
    """Return a non-sensitive service health document."""
    return {"phase": "official-mcp-asgi", "service": SERVICE_NAME, "status": "ok"}


def create_app(runtime: GatewayRuntime) -> Starlette:
    """Create the production ASGI application from already-validated dependencies."""
    mcp_app: Starlette | None = None
    if (
        runtime.tesla is not None
        and runtime.tesla.mcp_service is not None
        and runtime.authorization is not None
    ):
        mcp = create_mcp_server(
            runtime.tesla.mcp_service,
            auth_boundary=runtime.auth_boundary,
            authorization=runtime.authorization,
        )
        mcp_app = mcp.streamable_http_app(
            streamable_http_path="/mcp",
            max_request_body_size=MAX_REQUEST_BYTES,
        )

    @asynccontextmanager
    async def lifespan(_app: Starlette) -> AsyncIterator[None]:
        try:
            if mcp_app is None:
                yield
                return
            async with mcp_app.router.lifespan_context(mcp_app):
                yield
        finally:
            if runtime.browser_auth is not None:
                runtime.browser_auth.close()
            if runtime.tesla is not None:
                runtime.tesla.close()

    async def health(_request: Request) -> Response:
        return _json(HTTPStatus.OK, health_document())

    async def oauth_resource_metadata(_request: Request) -> Response:
        if runtime.authorization is None:
            return _json(HTTPStatus.NOT_FOUND, {"error": "oauth_not_configured"})
        return _json(HTTPStatus.OK, runtime.authorization.metadata_document())

    async def onboarding(request: Request) -> Response:
        if runtime.browser_auth is None:
            return _html(
                HTTPStatus.SERVICE_UNAVAILABLE,
                error_page(
                    "Sign-in is not configured",
                    "The operator has not enabled browser onboarding.",
                ),
            )
        session = _optional_browser_session(runtime, request)
        if session is None:
            return _html(HTTPStatus.OK, onboarding_page())
        context, browser_session, _ = session
        if runtime.tesla is None:
            return _html(
                HTTPStatus.SERVICE_UNAVAILABLE,
                error_page(
                    "Tesla setup is unavailable", "The Tesla integration is not configured."
                ),
            )
        vehicles = runtime.tesla.onboarding.list_vehicles(context)
        message = None
        if _optional_query_value(request.query_params, "connected") == "1":
            message = "Tesla authorization succeeded. Pair and verify each vehicle below."
        elif _optional_query_value(request.query_params, "refreshed") == "1":
            message = "The selected vehicle's Virtual Key status was refreshed."
        elif _optional_query_value(request.query_params, "tesla") == "denied":
            message = "Tesla authorization was cancelled; no connection changes were made."
        return _html(
            HTTPStatus.OK,
            onboarding_page(
                vehicles=vehicles,
                csrf_token=browser_session.csrf_token,
                message=message,
            ),
        )

    async def start_platform_login(_request: Request) -> Response:
        if runtime.browser_auth is None:
            return _html(
                HTTPStatus.SERVICE_UNAVAILABLE,
                error_page(
                    "Sign-in is not configured",
                    "The operator has not enabled browser onboarding.",
                ),
            )
        login = runtime.browser_auth.start()
        return _redirect(
            login.authorization_url,
            cookies=(_login_cookie(login.browser_binding_token),),
        )

    async def complete_platform_login(request: Request) -> Response:
        if runtime.browser_auth is None:
            return _html(
                HTTPStatus.SERVICE_UNAVAILABLE,
                error_page(
                    "Sign-in is not configured",
                    "The operator has not enabled browser onboarding.",
                ),
            )
        binding = _cookie_token(request.headers.get("cookie"), LOGIN_COOKIE_NAME)
        try:
            if _optional_query_value(request.query_params, "error") is not None:
                state = _optional_query_value(request.query_params, "state")
                if state is not None:
                    runtime.browser_auth.cancel(state, browser_binding_token=binding or "")
                raise BrowserAuthenticationError("The identity provider denied sign-in")
            state = _required_query_value(request.query_params, "state")
            code = _required_query_value(request.query_params, "code")
            result = runtime.browser_auth.complete(
                state=state,
                code=code,
                browser_binding_token=binding or "",
            )
        except AuthenticationError:
            return _html(
                HTTPStatus.FORBIDDEN,
                error_page(
                    "Account not approved",
                    "Ask the operator to add this verified email before signing in.",
                ),
                cookies=(_expired_login_cookie(),),
            )
        except BrowserAuthenticationError:
            return _html(
                HTTPStatus.BAD_REQUEST,
                error_page("Sign-in failed", "Start a fresh sign-in and try again."),
                cookies=(_expired_login_cookie(),),
            )
        return _redirect(
            "/onboarding",
            cookies=(_session_cookie(result.session_token), _expired_login_cookie()),
        )

    async def logout(request: Request) -> Response:
        form_session = await _require_form_session(runtime, request)
        if isinstance(form_session, Response):
            return form_session
        _, _, token, _ = form_session
        if runtime.browser_auth is not None:
            runtime.browser_auth.logout(token)
        return _redirect(
            "/",
            status=HTTPStatus.SEE_OTHER,
            cookies=(_expired_session_cookie(),),
        )

    async def start_browser_tesla_oauth(request: Request) -> Response:
        form_session = await _require_form_session(runtime, request)
        if isinstance(form_session, Response):
            return form_session
        context, _, token, _ = form_session
        if runtime.tesla is None or runtime.browser_auth is None:
            return _html(
                HTTPStatus.SERVICE_UNAVAILABLE,
                error_page(
                    "Tesla setup is unavailable", "The Tesla integration is not configured."
                ),
            )
        location = runtime.tesla.onboarding.start(
            context,
            completion_mode="browser",
            browser_session_binding=runtime.browser_auth.session_binding(token),
        )
        return _redirect(location)

    async def refresh_browser_pairing(request: Request) -> Response:
        form_session = await _require_form_session(runtime, request)
        if isinstance(form_session, Response):
            return form_session
        context = form_session[0]
        if runtime.tesla is None:
            return _html(
                HTTPStatus.SERVICE_UNAVAILABLE,
                error_page(
                    "Tesla setup is unavailable", "The Tesla integration is not configured."
                ),
            )
        try:
            runtime.tesla.onboarding.refresh_fleet_status(
                context, request.path_params["vehicle_id"]
            )
        except CrossUserAccessError:
            return _html(
                HTTPStatus.FORBIDDEN,
                error_page("Vehicle unavailable", "That vehicle is not available to this account."),
            )
        except TeslaReauthorizationRequired:
            return _html(
                HTTPStatus.UNAUTHORIZED,
                error_page(
                    "Tesla reconnection required",
                    "Authorize Tesla again before refreshing vehicle status.",
                    retry_path="/onboarding",
                ),
            )
        except (TeslaAPIError, TeslaOnboardingError):
            return _html(
                HTTPStatus.BAD_GATEWAY,
                error_page(
                    "Status refresh failed",
                    "Tesla did not return a usable status. Wait briefly and try again.",
                    retry_path="/onboarding",
                ),
            )
        return _redirect("/onboarding?refreshed=1", status=HTTPStatus.SEE_OTHER)

    async def inspect_browser_telemetry(request: Request) -> Response:
        session = _optional_browser_session(runtime, request)
        if session is None:
            return _html(
                HTTPStatus.UNAUTHORIZED,
                error_page("Sign-in required", "Sign in before inspecting telemetry."),
                cookies=(_expired_session_cookie(),),
            )
        if runtime.tesla is None or runtime.tesla.telemetry is None:
            return _html(
                HTTPStatus.SERVICE_UNAVAILABLE,
                error_page(
                    "Telemetry setup unavailable",
                    "The operator has not enabled Fleet Telemetry control.",
                    retry_path="/onboarding",
                ),
            )
        vehicle_id = request.path_params["vehicle_id"]
        try:
            document = runtime.tesla.telemetry.inspect(session[0], vehicle_id)
        except (
            CrossUserAccessError,
            TelemetryConfigurationError,
            TeslaAPIError,
            TeslaOnboardingError,
        ) as error:
            status, body = _browser_telemetry_failure(
                error, retry_path=f"/onboarding/vehicles/{vehicle_id}/telemetry"
            )
            return _html(status, body)
        message = None
        for query_name, text in (
            ("configured", "Tesla accepted and synchronized this exact telemetry configuration."),
            (
                "verified",
                "Tesla still reports the exact configuration in sync; Woodhouse recorded its "
                "trusted profile provenance.",
            ),
            ("removed", "The telemetry configuration was removed from this vehicle."),
            ("reconciled", "All required opted-in vehicles are ready for transport cutover."),
        ):
            if _optional_query_value(request.query_params, query_name) == "1":
                message = text
                break
        return _html(
            HTTPStatus.OK,
            telemetry_configuration_page(document, session[1].csrf_token, message=message),
        )

    async def mutate_browser_telemetry(request: Request) -> Response:
        form_session = await _require_form_session(runtime, request)
        if isinstance(form_session, Response):
            return form_session
        context, _, _, form = form_session
        vehicle_id = request.path_params["vehicle_id"]
        operation = request.path_params["operation"]
        if runtime.tesla is None or runtime.tesla.telemetry is None:
            return _html(
                HTTPStatus.SERVICE_UNAVAILABLE,
                error_page("Telemetry setup unavailable", "Telemetry control is not configured."),
            )
        retry_path = f"/onboarding/vehicles/{vehicle_id}/telemetry"
        try:
            if operation == "apply":
                runtime.tesla.telemetry.apply(
                    context,
                    vehicle_id,
                    expected_config_hash=form.get("expected_config_hash", ""),
                    confirm=form.get("confirm") == "yes",
                    transport_maintenance_opt_in=(
                        form.get("transport_maintenance_opt_in") == "yes"
                    ),
                )
                result = "configured"
            elif operation == "verify":
                runtime.tesla.telemetry.verify(context, vehicle_id)
                result = "verified"
            elif operation == "remove":
                runtime.tesla.telemetry.remove(
                    context, vehicle_id, confirm=form.get("confirm") == "yes"
                )
                result = "removed"
            elif operation == "reconcile":
                if form.get("confirm") != "yes":
                    return _html(
                        HTTPStatus.BAD_REQUEST,
                        error_page(
                            "Confirmation required",
                            "Select and approve the transport migration canary before continuing.",
                            retry_path=retry_path,
                        ),
                    )
                reconciliation = runtime.tesla.telemetry.reconcile_opted_in_transport(
                    context, canary_vehicle_id=vehicle_id
                )
                if reconciliation.get("status") != "ready_for_server_cutover":
                    return _html(
                        HTTPStatus.CONFLICT,
                        error_page(
                            "Transport cutover blocked",
                            "At least one required vehicle is not opted in, has field drift, or "
                            "failed Tesla synchronization. The old server trust must remain "
                            "active.",
                            retry_path=retry_path,
                        ),
                    )
                result = "reconciled"
            else:
                return _json(HTTPStatus.NOT_FOUND, {"error": "not_found"})
        except (
            CrossUserAccessError,
            TelemetryConfigurationError,
            TeslaAPIError,
            TeslaOnboardingError,
        ) as error:
            status, body = _browser_telemetry_failure(error, retry_path=retry_path)
            return _html(status, body)
        return _redirect(f"{retry_path}?{result}=1", status=HTTPStatus.SEE_OTHER)

    async def tesla_public_key(_request: Request) -> Response:
        if runtime.tesla is None:
            return _json(HTTPStatus.SERVICE_UNAVAILABLE, {"error": "tesla_not_configured"})
        return Response(
            runtime.tesla.public_key_pem,
            media_type="application/x-pem-file",
            headers={"Cache-Control": "public, max-age=300"},
        )

    async def start_tesla_oauth(request: Request) -> Response:
        if runtime.tesla is None:
            return _json(HTTPStatus.SERVICE_UNAVAILABLE, {"error": "tesla_not_configured"})
        context = _authorize_api(runtime, request, {})
        if isinstance(context, Response):
            return context
        return _redirect(runtime.tesla.onboarding.start(context))

    async def complete_tesla_oauth(request: Request) -> Response:
        if runtime.tesla is None:
            return _json(HTTPStatus.SERVICE_UNAVAILABLE, {"error": "tesla_not_configured"})
        browser_session = _optional_browser_session(runtime, request)
        browser_binding = (
            runtime.browser_auth.session_binding(browser_session[2])
            if runtime.browser_auth is not None and browser_session is not None
            else None
        )
        try:
            state = _required_query_value(request.query_params, "state")
            if _optional_query_value(request.query_params, "error") is not None:
                pending = runtime.tesla.onboarding.decline(
                    state=state,
                    expected_browser_session_binding=browser_binding,
                )
                if pending.completion_mode == "browser":
                    return _redirect("/onboarding?tesla=denied", status=HTTPStatus.SEE_OTHER)
                return _json(HTTPStatus.BAD_REQUEST, {"error": "tesla_authorization_denied"})
            code = _required_query_value(request.query_params, "code")
            result = runtime.tesla.onboarding.callback(
                state=state,
                code=code,
                expected_browser_session_binding=browser_binding,
            )
        except InvalidOAuthStateError:
            return _json(HTTPStatus.BAD_REQUEST, {"error": "invalid_oauth_state"})
        except TeslaAuthenticationError:
            return _json(HTTPStatus.BAD_GATEWAY, {"error": "tesla_authorization_failed"})
        except (TeslaAPIError, TeslaOnboardingError):
            return _json(HTTPStatus.BAD_GATEWAY, {"error": "tesla_onboarding_failed"})
        if result.completion_mode == "browser":
            if browser_session is None or browser_session[0].user_id != result.owner_user_id:
                return _html(
                    HTTPStatus.FORBIDDEN,
                    error_page(
                        "Sign-in required",
                        "Sign in again before viewing the connected vehicles.",
                    ),
                )
            return _redirect("/onboarding?connected=1", status=HTTPStatus.SEE_OTHER)
        return _json(
            HTTPStatus.OK,
            {
                "status": "connected",
                "connection_id": result.connection_id,
                "region": result.region,
                "fleet_api_base_url": result.base_url,
                "vehicles": runtime.tesla.onboarding.vehicle_documents(result.vehicles),
            },
        )

    async def list_tesla_vehicles(request: Request) -> Response:
        if runtime.tesla is None:
            return _json(HTTPStatus.SERVICE_UNAVAILABLE, {"error": "tesla_not_configured"})
        context = _authorize_api(runtime, request, {})
        if isinstance(context, Response):
            return context
        return _json(
            HTTPStatus.OK,
            {"vehicles": runtime.tesla.onboarding.list_vehicles(context)},
        )

    async def refresh_tesla_fleet_status(request: Request) -> Response:
        if runtime.tesla is None:
            return _json(HTTPStatus.SERVICE_UNAVAILABLE, {"error": "tesla_not_configured"})
        payload = await _json_body(request)
        if isinstance(payload, Response):
            return payload
        context = _authorize_api(runtime, request, payload)
        if isinstance(context, Response):
            return context
        try:
            vehicle = runtime.tesla.onboarding.refresh_fleet_status(
                context, request.path_params["vehicle_id"]
            )
        except TeslaReauthorizationRequired:
            return _json(HTTPStatus.UNAUTHORIZED, {"error": "tesla_reauthorization_required"})
        except CrossUserAccessError:
            return _json(HTTPStatus.FORBIDDEN, {"error": "vehicle_forbidden"})
        except TeslaAPIError as error:
            _log_tesla_failure("tesla_fleet_status_failed", error)
            return _json(HTTPStatus.BAD_GATEWAY, {"error": "tesla_fleet_status_failed"})
        except TeslaOnboardingError:
            LOGGER.warning(
                "tesla_fleet_status_failed category=onboarding_error upstream_status=none"
            )
            return _json(HTTPStatus.BAD_GATEWAY, {"error": "tesla_fleet_status_failed"})
        return _json(HTTPStatus.OK, {"vehicle": vehicle})

    async def rotate_tesla_refresh_token(request: Request) -> Response:
        if runtime.tesla is None:
            return _json(HTTPStatus.SERVICE_UNAVAILABLE, {"error": "tesla_not_configured"})
        payload = await _json_body(request)
        if isinstance(payload, Response):
            return payload
        context = _authorize_api(runtime, request, payload)
        if isinstance(context, Response):
            return context
        try:
            result = runtime.tesla.onboarding.rotate_refresh_token(context)
        except TeslaReauthorizationRequired:
            return _json(HTTPStatus.UNAUTHORIZED, {"error": "tesla_reauthorization_required"})
        except (TeslaAPIError, TeslaOnboardingError):
            return _json(HTTPStatus.BAD_GATEWAY, {"error": "tesla_refresh_failed"})
        return _json(HTTPStatus.OK, result)

    routes: list[BaseRoute] = [
        Route("/health", health),
        Route("/healthz", health),
        Route("/", onboarding),
        Route("/onboarding", onboarding),
        Route("/.well-known/oauth-protected-resource", oauth_resource_metadata),
        Route("/.well-known/oauth-protected-resource/mcp", oauth_resource_metadata),
        Route("/auth/login", start_platform_login),
        Route("/auth/callback", complete_platform_login),
        Route("/auth/logout", logout, methods=["POST"]),
        Route("/onboarding/tesla/start", start_browser_tesla_oauth, methods=["POST"]),
        Route(
            "/onboarding/vehicles/{vehicle_id:str}/refresh",
            refresh_browser_pairing,
            methods=["POST"],
        ),
        Route(
            "/onboarding/vehicles/{vehicle_id:str}/telemetry",
            inspect_browser_telemetry,
        ),
        Route(
            "/onboarding/vehicles/{vehicle_id:str}/telemetry/{operation:str}",
            mutate_browser_telemetry,
            methods=["POST"],
        ),
        Route(TESLA_PUBLIC_KEY_PATH, tesla_public_key),
        Route("/tesla/oauth/start", start_tesla_oauth),
        Route("/oauth/callback", complete_tesla_oauth),
        Route("/tesla/vehicles", list_tesla_vehicles),
        Route("/tesla/oauth/refresh", rotate_tesla_refresh_token, methods=["POST"]),
        Route(
            "/tesla/vehicles/{vehicle_id:str}/fleet-status",
            refresh_tesla_fleet_status,
            methods=["POST"],
        ),
    ]
    if mcp_app is not None:
        routes.append(Mount("/", app=mcp_app))
    else:

        async def unavailable_mcp(_request: Request) -> Response:
            return _json(HTTPStatus.SERVICE_UNAVAILABLE, {"error": "tesla_mcp_not_configured"})

        routes.append(Route("/mcp", unavailable_mcp, methods=["GET", "POST", "DELETE"]))
    return Starlette(
        routes=routes,
        middleware=[
            Middleware(SafeAccessLogMiddleware),
            Middleware(RequestBodyBoundaryMiddleware),
        ],
        lifespan=lifespan,
    )


def _authorize_api(
    runtime: GatewayRuntime, request: Request, payload: object
) -> UserContext | Response:
    try:
        assert_no_caller_identity_claims(payload)
        return runtime.auth_boundary.authorize(request.headers.get("authorization"), payload)
    except CallerIdentityClaimError:
        return _json(HTTPStatus.BAD_REQUEST, {"error": "caller_identity_fields_forbidden"})
    except AuthenticationError:
        return _json(
            HTTPStatus.UNAUTHORIZED,
            {"error": "unauthorized"},
            headers={"WWW-Authenticate": 'Bearer realm="mcp-gateway"'},
        )


async def _json_body(request: Request) -> object | Response:
    content_length = request.headers.get("content-length")
    if content_length is None:
        return _json(HTTPStatus.BAD_REQUEST, {"error": "invalid_json_request"})
    try:
        length = int(content_length)
    except ValueError:
        return _json(HTTPStatus.BAD_REQUEST, {"error": "invalid_json_request"})
    if length < 0 or length > MAX_REQUEST_BYTES:
        return _json(HTTPStatus.BAD_REQUEST, {"error": "invalid_json_request"})
    try:
        body = await request.body()
        if len(body) != length:
            raise ValueError("Incomplete request body")
        return cast(object, json.loads(body.decode("utf-8")))
    except (json.JSONDecodeError, UnicodeDecodeError, ValueError, RecursionError):
        return _json(HTTPStatus.BAD_REQUEST, {"error": "invalid_json_request"})


def _optional_browser_session(
    runtime: GatewayRuntime, request: Request
) -> tuple[UserContext, BrowserSession, str] | None:
    if runtime.browser_auth is None:
        return None
    token = _cookie_token(request.headers.get("cookie"), SESSION_COOKIE_NAME)
    if token is None:
        return None
    try:
        context, session = runtime.browser_auth.authenticate_session(token)
    except (AuthenticationError, BrowserAuthenticationError):
        return None
    return context, session, token


async def _require_form_session(
    runtime: GatewayRuntime, request: Request
) -> tuple[UserContext, BrowserSession, str, dict[str, str]] | Response:
    session = _optional_browser_session(runtime, request)
    if session is None:
        return _html(
            HTTPStatus.UNAUTHORIZED,
            error_page("Sign-in required", "Sign in before continuing."),
            cookies=(_expired_session_cookie(),),
        )
    content_type = request.headers.get("content-type", "").partition(";")[0].strip().casefold()
    content_length = request.headers.get("content-length")
    if content_type != "application/x-www-form-urlencoded" or content_length is None:
        return _html(
            HTTPStatus.BAD_REQUEST,
            error_page("Invalid request", "Return to onboarding and try again."),
        )
    try:
        length = int(content_length)
        if length < 0 or length > MAX_FORM_BYTES:
            raise ValueError("Form body size is invalid")
        body = await request.body()
        if len(body) != length:
            raise ValueError("Incomplete form body")
        parsed = parse_qs(body.decode("utf-8"), strict_parsing=True, max_num_fields=8)
        form = {key: values[0] for key, values in parsed.items() if len(values) == 1}
    except (UnicodeDecodeError, ValueError):
        return _html(
            HTTPStatus.BAD_REQUEST,
            error_page("Invalid request", "Return to onboarding and try again."),
        )
    if form.get("csrf_token") != session[1].csrf_token:
        return _html(
            HTTPStatus.FORBIDDEN,
            error_page("Request expired", "Return to onboarding and try again."),
        )
    return (*session, form)


def _required_query_value(query: QueryParams, name: str) -> str:
    values = query.getlist(name)
    if len(values) != 1 or not values[0]:
        raise InvalidOAuthStateError("OAuth callback parameter is invalid")
    return values[0]


def _optional_query_value(query: QueryParams, name: str) -> str | None:
    try:
        return _required_query_value(query, name)
    except InvalidOAuthStateError:
        return None


def _session_cookie(token: str) -> str:
    max_age = int(WEB_SESSION_LIFETIME.total_seconds())
    return (
        f"{SESSION_COOKIE_NAME}={token}; Path=/; Max-Age={max_age}; Secure; HttpOnly; SameSite=Lax"
    )


def _login_cookie(token: str) -> str:
    return f"{LOGIN_COOKIE_NAME}={token}; Path=/; Max-Age=600; Secure; HttpOnly; SameSite=Lax"


def _expired_session_cookie() -> str:
    return f"{SESSION_COOKIE_NAME}=; Path=/; Max-Age=0; Secure; HttpOnly; SameSite=Lax"


def _expired_login_cookie() -> str:
    return f"{LOGIN_COOKIE_NAME}=; Path=/; Max-Age=0; Secure; HttpOnly; SameSite=Lax"


def _browser_telemetry_failure(
    error: CrossUserAccessError
    | TelemetryConfigurationError
    | TeslaAPIError
    | TeslaOnboardingError,
    *,
    retry_path: str,
) -> tuple[HTTPStatus, bytes]:
    if isinstance(error, CrossUserAccessError):
        return HTTPStatus.FORBIDDEN, error_page(
            "Vehicle unavailable",
            "That vehicle is not available to this account.",
            retry_path="/onboarding",
        )
    if isinstance(error, TeslaReauthorizationRequired):
        return HTTPStatus.UNAUTHORIZED, error_page(
            "Tesla reconnection required",
            "Authorize Tesla again before changing telemetry configuration.",
            retry_path="/onboarding",
        )
    if isinstance(error, TelemetryConfigurationError):
        return HTTPStatus.CONFLICT, error_page(
            "Telemetry operation stopped safely",
            f"Woodhouse rejected the operation ({error.category}). "
            "Inspect the current plan and Tesla errors before retrying.",
            retry_path=retry_path,
        )
    if isinstance(error, TeslaOnboardingError):
        return HTTPStatus.BAD_GATEWAY, error_page(
            "Telemetry setup unavailable",
            "Woodhouse could not resolve a usable Tesla connection or vehicle record. "
            "Refresh onboarding before retrying.",
            retry_path="/onboarding",
        )
    return HTTPStatus.BAD_GATEWAY, error_page(
        "Tesla telemetry operation failed",
        f"Tesla did not complete the operation ({error.category}). "
        "Inspect Tesla telemetry errors before retrying.",
        retry_path=retry_path,
    )


def _log_tesla_failure(event: str, error: TeslaAPIError) -> None:
    upstream_status = str(error.status_code) if error.status_code is not None else "none"
    LOGGER.warning(
        "%s category=%s upstream_status=%s",
        event,
        error.category,
        upstream_status,
    )
