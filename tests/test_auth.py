"""Platform authentication, allowlist, and tenant-isolation tests."""

import logging
from dataclasses import replace
from typing import cast

import pytest
from starlette.testclient import TestClient
from tesla_personal_platform.auth import (
    AllowedUser,
    Authenticator,
    CallerIdentityClaimError,
    ConfigurationError,
    CrossUserAccessError,
    EmailNotVerifiedError,
    IdentityMismatchError,
    InvalidTokenError,
    UserDisabledError,
    UserNotAllowedError,
    UserStatus,
    VerifiedIdentity,
    authorize_trusted_owner,
)
from tesla_personal_platform.auth.admin import (
    AnalyticsViewReconciliation,
    AnalyticsViewReconciliationError,
    AnalyticsViewSyncService,
    UserAdminService,
)
from tesla_personal_platform.auth.memory import InMemoryIdentityStore
from tesla_personal_platform.mcp_gateway.app import (
    _browser_telemetry_failure,
    _log_tesla_failure,
    create_app,
)
from tesla_personal_platform.mcp_gateway.auth_boundary import GatewayAuthBoundary
from tesla_personal_platform.mcp_gateway.gateway_runtime import GatewayRuntime
from tesla_personal_platform.mcp_gateway.http_boundary import MAX_REQUEST_BYTES
from tesla_personal_platform.mcp_gateway.mcp_auth import MCPAuthorizationSettings
from tesla_personal_platform.mcp_gateway.mcp_tools import TeslaMCPService
from tesla_personal_platform.mcp_gateway.telemetry_control import TelemetryConfigurationError
from tesla_personal_platform.mcp_gateway.tesla_onboarding import (
    TeslaOnboardingError,
    TeslaOnboardingService,
)
from tesla_personal_platform.mcp_gateway.tesla_runtime import TeslaRuntime
from tesla_personal_platform.tesla_client import TeslaAPIError, TeslaReauthorizationRequired


class TokenMapVerifier:
    """Test verifier that returns only explicitly configured trusted claims."""

    def __init__(self, tokens: dict[str, VerifiedIdentity]) -> None:
        self._tokens = tokens

    def verify(self, token: str) -> VerifiedIdentity:
        return self._tokens[token]


def active_user(
    email: str = "homer@example.com",
    *,
    user_id: str = "usr_homer",
    dataset_id: str = "tesla_u_homer",
) -> AllowedUser:
    return AllowedUser(email, user_id, dataset_id, UserStatus.ACTIVE)


def boundary_for(
    identities: InMemoryIdentityStore,
    tokens: dict[str, VerifiedIdentity],
) -> GatewayAuthBoundary:
    return GatewayAuthBoundary(Authenticator(TokenMapVerifier(tokens), identities))


@pytest.mark.parametrize("path", ["/health", "/healthz"])
def test_gateway_health_routes_are_unauthenticated(path: str) -> None:
    runtime = GatewayRuntime(boundary_for(InMemoryIdentityStore(), {}), None, None, None)
    with TestClient(create_app(runtime)) as client:
        response = client.get(path)

    assert response.status_code == 200
    assert response.json() == {
        "phase": "official-mcp-asgi",
        "service": "mcp-gateway",
        "status": "ok",
    }


def test_tesla_public_key_route_is_public_and_exact() -> None:
    public_key = b"-----BEGIN PUBLIC KEY-----\ntest\n-----END PUBLIC KEY-----\n"
    runtime = TeslaRuntime(cast(TeslaOnboardingService, object()), public_key)
    gateway = GatewayRuntime(boundary_for(InMemoryIdentityStore(), {}), runtime, None, None)
    with TestClient(create_app(gateway)) as client:
        response = client.get("/.well-known/appspecific/com.tesla.3p.public-key.pem")

    assert response.status_code == 200
    assert response.headers["Content-Type"] == "application/x-pem-file"
    assert response.content == public_key


def test_tesla_public_key_route_fails_closed_before_configuration() -> None:
    runtime = GatewayRuntime(boundary_for(InMemoryIdentityStore(), {}), None, None, None)
    with TestClient(create_app(runtime)) as client:
        response = client.get("/.well-known/appspecific/com.tesla.3p.public-key.pem")

    assert response.status_code == 503


def test_oauth_protected_resource_metadata_is_public_and_exact() -> None:
    authorization = MCPAuthorizationSettings(
        "https://woodhouse.derekjass.com/mcp",
        "https://tenant.example.auth0.com/",
        ("mcp:access",),
    )
    runtime = GatewayRuntime(boundary_for(InMemoryIdentityStore(), {}), None, authorization, None)
    with TestClient(create_app(runtime)) as client:
        response = client.get("/.well-known/oauth-protected-resource")

    assert response.status_code == 200
    assert response.json() == {
        "resource": "https://woodhouse.derekjass.com/mcp",
        "authorization_servers": ["https://tenant.example.auth0.com/"],
        "scopes_supported": ["mcp:access"],
        "resource_documentation": "https://woodhouse.derekjass.com/onboarding",
    }


def test_mcp_route_fails_closed_when_service_or_oauth_is_unavailable() -> None:
    runtime = GatewayRuntime(boundary_for(InMemoryIdentityStore(), {}), None, None, None)
    with TestClient(create_app(runtime)) as client:
        response = client.post("/mcp", json={"jsonrpc": "2.0", "method": "ping"})

    assert response.status_code == 503
    assert response.json() == {"error": "tesla_mcp_not_configured"}


def test_asgi_boundary_rejects_large_body_before_routing() -> None:
    runtime = GatewayRuntime(boundary_for(InMemoryIdentityStore(), {}), None, None, None)
    with TestClient(create_app(runtime)) as client:
        response = client.post("/mcp", content=b"x" * (MAX_REQUEST_BYTES + 1))

    assert response.status_code == 413
    assert response.json() == {"error": "body_too_large"}


def test_official_mcp_route_returns_oauth_resource_challenge_without_redirect() -> None:
    authorization = MCPAuthorizationSettings(
        "https://woodhouse.derekjass.com/mcp",
        "https://tenant.example.auth0.com/",
        ("mcp:access",),
    )
    tesla = TeslaRuntime(
        cast(TeslaOnboardingService, object()),
        b"",
        mcp_service=cast(TeslaMCPService, object()),
    )
    runtime = GatewayRuntime(boundary_for(InMemoryIdentityStore(), {}), tesla, authorization, None)
    with TestClient(create_app(runtime), follow_redirects=False) as client:
        response = client.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-11-25",
                    "capabilities": {},
                    "clientInfo": {"name": "test", "version": "1"},
                },
            },
            headers={"Accept": "application/json, text/event-stream"},
        )

    assert response.status_code == 401
    assert "location" not in response.headers
    assert "resource_metadata=" in response.headers["www-authenticate"]


@pytest.mark.parametrize(
    ("base_url", "expected_status"),
    [
        ("https://woodhouse.derekjass.com", 200),
        ("https://unexpected.example", 421),
    ],
)
def test_authenticated_mcp_transport_accepts_only_the_configured_public_host(
    base_url: str,
    expected_status: int,
) -> None:
    issuer = "https://tenant.example.auth0.com/"
    subject = "auth0-homer"
    identity = VerifiedIdentity(
        issuer,
        subject,
        "homer@example.com",
        email_verified=True,
    )
    identities = InMemoryIdentityStore(
        [replace(active_user(), oidc_issuer=issuer, oidc_subject=subject)]
    )
    authorization = MCPAuthorizationSettings(
        "https://woodhouse.derekjass.com/mcp",
        issuer,
        ("mcp:access",),
    )
    tesla = TeslaRuntime(
        cast(TeslaOnboardingService, object()),
        b"",
        mcp_service=cast(TeslaMCPService, object()),
    )
    runtime = GatewayRuntime(
        boundary_for(identities, {"valid-token": identity}),
        tesla,
        authorization,
        None,
    )
    with TestClient(create_app(runtime), base_url=base_url) as client:
        response = client.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-11-25",
                    "capabilities": {},
                    "clientInfo": {"name": "host-validation-test", "version": "1"},
                },
            },
            headers={
                "Accept": "application/json, text/event-stream",
                "Authorization": "Bearer valid-token",
            },
        )

    assert response.status_code == expected_status


def test_tesla_failure_log_contains_only_safe_metadata(
    caplog: pytest.LogCaptureFixture,
) -> None:
    error = TeslaAPIError(
        "credential-bearing response must not be logged",
        category="http_error",
        status_code=403,
    )

    with caplog.at_level(logging.WARNING):
        _log_tesla_failure("tesla_fleet_status_failed", error)

    assert "tesla_fleet_status_failed category=http_error upstream_status=403" in caplog.text
    assert "credential-bearing" not in caplog.text


@pytest.mark.parametrize(
    ("error", "expected_status", "expected_text"),
    [
        (
            CrossUserAccessError("outside boundary"),
            403,
            "That vehicle is not available to this account.",
        ),
        (
            TeslaReauthorizationRequired("expired"),
            401,
            "Authorize Tesla again before changing telemetry configuration.",
        ),
        (
            TelemetryConfigurationError("stale_plan", "changed"),
            409,
            "Woodhouse rejected the operation (stale_plan).",
        ),
        (
            TeslaOnboardingError("missing connection"),
            502,
            "Woodhouse could not resolve a usable Tesla connection or vehicle record.",
        ),
        (
            TeslaAPIError("upstream", category="rate_limited"),
            502,
            "Tesla did not complete the operation (rate_limited).",
        ),
    ],
)
def test_browser_telemetry_failures_have_actionable_statuses(
    error: CrossUserAccessError
    | TelemetryConfigurationError
    | TeslaAPIError
    | TeslaOnboardingError,
    expected_status: int,
    expected_text: str,
) -> None:
    status, body = _browser_telemetry_failure(error, retry_path="/telemetry")

    assert status == expected_status
    assert expected_text in body.decode()


def test_non_allowlisted_login_is_rejected() -> None:
    identity = VerifiedIdentity(
        "https://accounts.google.com",
        "not-invited",
        "marge@example.com",
        email_verified=True,
    )
    boundary = boundary_for(InMemoryIdentityStore(), {"token": identity})

    with pytest.raises(UserNotAllowedError):
        boundary.authorize("Bearer token", {})


def test_malformed_first_login_email_is_rejected_as_invalid_token() -> None:
    identity = VerifiedIdentity(
        "https://accounts.google.com",
        "malformed-email",
        "not-an-email",
        email_verified=True,
    )
    boundary = boundary_for(InMemoryIdentityStore(), {"token": identity})

    with pytest.raises(InvalidTokenError, match="invalid email claim"):
        boundary.authorize("Bearer token", {})


def test_unverified_email_cannot_bootstrap_an_invitation() -> None:
    identity = VerifiedIdentity(
        "https://accounts.google.com",
        "google-homer",
        "homer@example.com",
        email_verified=False,
    )
    boundary = boundary_for(InMemoryIdentityStore([active_user()]), {"token": identity})

    with pytest.raises(EmailNotVerifiedError):
        boundary.authorize("Bearer token", {})


def test_first_login_binds_immutable_identity_to_existing_random_user() -> None:
    identity = VerifiedIdentity(
        "https://accounts.google.com",
        "google-homer",
        "Homer@Example.com",
        email_verified=True,
    )
    identities = InMemoryIdentityStore([active_user()])
    boundary = boundary_for(identities, {"token": identity})

    context = boundary.authorize("Bearer token", {"method": "initialize"})

    assert context.user_id == "usr_homer"
    assert context.dataset_id == "tesla_u_homer"
    assert identities.get_user("homer@example.com") == replace(
        active_user(),
        oidc_issuer="https://accounts.google.com",
        oidc_subject="google-homer",
    )


def test_later_email_change_uses_immutable_binding_not_email() -> None:
    first = VerifiedIdentity(
        "https://accounts.google.com",
        "google-homer",
        "homer@example.com",
        email_verified=True,
    )
    changed = VerifiedIdentity(
        "https://accounts.google.com",
        "google-homer",
        "homer.simpson@example.net",
        email_verified=False,
    )
    identities = InMemoryIdentityStore([active_user()])
    boundary = boundary_for(identities, {"first": first, "changed": changed})
    original = boundary.authorize("Bearer first", {})

    later = boundary.authorize("Bearer changed", {})

    assert later == original


@pytest.mark.parametrize(
    "changed",
    [
        VerifiedIdentity(
            "https://accounts.google.com",
            "different-subject",
            "homer@example.com",
            email_verified=True,
        ),
        VerifiedIdentity(
            "https://issuer.example",
            "google-homer",
            "homer@example.com",
            email_verified=True,
        ),
    ],
)
def test_issuer_or_subject_mismatch_cannot_take_over_bound_invitation(
    changed: VerifiedIdentity,
) -> None:
    bound = replace(
        active_user(),
        oidc_issuer="https://accounts.google.com",
        oidc_subject="google-homer",
    )
    boundary = boundary_for(InMemoryIdentityStore([bound]), {"changed": changed})

    with pytest.raises(IdentityMismatchError):
        boundary.authorize("Bearer changed", {})


def test_disabled_user_is_blocked_after_binding() -> None:
    identity = VerifiedIdentity(
        "https://accounts.google.com",
        "google-homer",
        "homer@example.com",
        email_verified=True,
    )
    identities = InMemoryIdentityStore([active_user()])
    boundary = boundary_for(identities, {"token": identity})
    boundary.authorize("Bearer token", {})
    bound = identities.get_user("homer@example.com")
    assert bound is not None
    identities.replace_user(replace(bound, status=UserStatus.DISABLED))

    with pytest.raises(UserDisabledError):
        boundary.authorize("Bearer token", {})


@pytest.mark.parametrize(
    "partially_bound",
    [
        replace(active_user(), oidc_issuer="https://accounts.google.com"),
        replace(active_user(), oidc_subject="google-homer"),
    ],
)
def test_partially_bound_invitation_is_a_configuration_error(
    partially_bound: AllowedUser,
) -> None:
    identity = VerifiedIdentity(
        "https://accounts.google.com",
        "google-homer",
        "homer@example.com",
        email_verified=True,
    )
    boundary = boundary_for(InMemoryIdentityStore([partially_bound]), {"token": identity})

    with pytest.raises(ConfigurationError, match="partially bound"):
        boundary.authorize("Bearer token", {})


@pytest.mark.parametrize(
    "payload",
    [
        {"user_id": "usr_other"},
        {"arguments": {"dataset_id": "tesla_u_other"}},
        {"arguments": [{"ownership_claim": "mine"}]},
        {"owner_user_id": "usr_other"},
    ],
)
def test_gateway_rejects_caller_supplied_tenant_and_ownership_claims(
    payload: object,
) -> None:
    identity = VerifiedIdentity(
        "https://accounts.google.com",
        "google-homer",
        "homer@example.com",
        email_verified=True,
    )
    boundary = boundary_for(InMemoryIdentityStore([active_user()]), {"token": identity})

    with pytest.raises(CallerIdentityClaimError):
        boundary.authorize("Bearer token", payload)


def test_gateway_rejects_excessively_nested_caller_payload() -> None:
    identity = VerifiedIdentity(
        "https://accounts.google.com",
        "google-homer",
        "homer@example.com",
        email_verified=True,
    )
    boundary = boundary_for(InMemoryIdentityStore([active_user()]), {"token": identity})
    payload: object = {}
    for _ in range(65):
        payload = {"arguments": payload}

    with pytest.raises(CallerIdentityClaimError, match="maximum nesting depth"):
        boundary.authorize("Bearer token", payload)


def test_trusted_cross_user_resource_is_rejected() -> None:
    identity = VerifiedIdentity(
        "https://accounts.google.com",
        "google-homer",
        "homer@example.com",
        email_verified=True,
    )
    boundary = boundary_for(InMemoryIdentityStore([active_user()]), {"token": identity})
    context = boundary.authorize("Bearer token", {})

    with pytest.raises(CrossUserAccessError):
        authorize_trusted_owner(context, "usr_marge")


class MemoryAdminStore:
    def __init__(self) -> None:
        self.users: dict[str, AllowedUser] = {}
        self.allocations = 0

    def ensure_invitation(self, email: str, notes: str | None = None) -> AllowedUser:
        del notes
        key = email.strip().casefold()
        if key not in self.users:
            self.allocations += 1
            self.users[key] = AllowedUser(
                key,
                f"usr_random_{self.allocations}",
                f"tesla_u_random_{self.allocations}",
                UserStatus.DISABLED,
            )
        return self.users[key]

    def activate(self, email: str) -> AllowedUser:
        key = email.strip().casefold()
        self.users[key] = replace(self.users[key], status=UserStatus.ACTIVE)
        return self.users[key]

    def disable(self, email: str) -> AllowedUser:
        key = email.strip().casefold()
        self.users[key] = replace(self.users[key], status=UserStatus.DISABLED)
        return self.users[key]

    def reset_identity(self, email: str, expected_user_id: str) -> AllowedUser:
        key = email.strip().casefold()
        user = self.users[key]
        if user.user_id != expected_user_id:
            raise ValueError("mismatch")
        self.users[key] = replace(user, oidc_issuer=None, oidc_subject=None)
        return self.users[key]


class RecordingDatasetProvisioner:
    def __init__(self) -> None:
        self.datasets: list[str] = []

    def provision(self, user: AllowedUser) -> None:
        self.datasets.append(user.dataset_id)


class FailingDatasetProvisioner:
    def provision(self, user: AllowedUser) -> None:
        del user
        raise RuntimeError("simulated provisioning failure")


class ActiveUsers:
    def __init__(self, *users: AllowedUser) -> None:
        self.users = users

    def list_active_users(self) -> tuple[AllowedUser, ...]:
        return self.users


class RecordingViewReconciler:
    def __init__(self, *, fail: bool = False) -> None:
        self.dataset_ids: list[str] = []
        self.fail = fail

    def reconcile(self, dataset_id: str) -> AnalyticsViewReconciliation:
        self.dataset_ids.append(dataset_id)
        if self.fail:
            raise ValueError(f"private dataset was {dataset_id}")
        return AnalyticsViewReconciliation(18, 1)


class SafelyFailingViewReconciler:
    def reconcile(self, dataset_id: str) -> AnalyticsViewReconciliation:
        del dataset_id
        raise AnalyticsViewReconciliationError("preflight", "drive_fsd_segments")


def test_add_user_is_idempotent_and_repairs_dataset_access_each_run() -> None:
    store = MemoryAdminStore()
    datasets = RecordingDatasetProvisioner()
    service = UserAdminService(store, datasets)

    first = service.add_user("Homer@Example.com")
    second = service.add_user("homer@example.com", "updated notes")

    assert first == second
    assert first.status is UserStatus.ACTIVE
    assert store.allocations == 1
    assert datasets.datasets == [first.dataset_id, first.dataset_id]


def test_disable_user_is_idempotent_and_preserves_identifiers() -> None:
    store = MemoryAdminStore()
    service = UserAdminService(store, RecordingDatasetProvisioner())
    added = service.add_user("homer@example.com")

    first = service.disable_user("homer@example.com")
    second = service.disable_user("HOMER@example.com")

    assert first == second
    assert first.status is UserStatus.DISABLED
    assert first.user_id == added.user_id
    assert first.dataset_id == added.dataset_id


def test_new_invitation_stays_disabled_if_dataset_provisioning_fails() -> None:
    store = MemoryAdminStore()
    service = UserAdminService(store, FailingDatasetProvisioner())

    with pytest.raises(RuntimeError, match="simulated provisioning failure"):
        service.add_user("homer@example.com")

    assert store.users["homer@example.com"].status is UserStatus.DISABLED


def test_identity_reset_requires_exact_user_id_and_preserves_tenant() -> None:
    store = MemoryAdminStore()
    service = UserAdminService(store, RecordingDatasetProvisioner())
    added = service.add_user("homer@example.com")
    store.users[added.invitation_email] = replace(
        added,
        oidc_issuer="https://accounts.google.com",
        oidc_subject="legacy-google-subject",
    )

    with pytest.raises(ValueError, match="mismatch"):
        service.reset_user_identity("homer@example.com", "usr_wrong")

    reset = service.reset_user_identity("homer@example.com", added.user_id)
    assert reset.user_id == added.user_id
    assert reset.dataset_id == added.dataset_id
    assert reset.oidc_issuer is None
    assert reset.oidc_subject is None


def test_analytics_view_sync_reconciles_every_active_tenant_without_identity_input() -> None:
    views = RecordingViewReconciler()
    summary = AnalyticsViewSyncService(
        ActiveUsers(
            active_user(),
            active_user(
                "marge@example.com",
                user_id="usr_marge",
                dataset_id="tesla_u_marge",
            ),
        ),
        views,
    ).sync_active_users()

    assert views.dataset_ids == ["tesla_u_homer", "tesla_u_marge"]
    assert summary.active_user_count == 2
    assert summary.desired_view_count == 36
    assert summary.removed_view_count == 2


def test_analytics_view_sync_refuses_duplicate_tenant_identifiers_before_writes() -> None:
    views = RecordingViewReconciler()
    service = AnalyticsViewSyncService(
        ActiveUsers(
            active_user(),
            active_user(
                "marge@example.com",
                user_id="usr_marge",
                dataset_id="tesla_u_homer",
            ),
        ),
        views,
    )

    with pytest.raises(RuntimeError, match="duplicate tenant identifiers"):
        service.sync_active_users()

    assert views.dataset_ids == []


def test_analytics_view_sync_sanitizes_per_tenant_failures() -> None:
    service = AnalyticsViewSyncService(
        ActiveUsers(active_user()), RecordingViewReconciler(fail=True)
    )

    with pytest.raises(RuntimeError) as error:
        service.sync_active_users()

    assert str(error.value) == "Analytics view reconciliation failed for one active tenant"
    assert "tesla_u_homer" not in str(error.value)


def test_analytics_view_sync_reports_only_safe_canonical_failure_context() -> None:
    service = AnalyticsViewSyncService(ActiveUsers(active_user()), SafelyFailingViewReconciler())

    with pytest.raises(RuntimeError) as error:
        service.sync_active_users()

    assert str(error.value) == (
        "Analytics view reconciliation failed for one active tenant: "
        "Analytics view preflight failed for drive_fsd_segments"
    )
    assert "tesla_u_homer" not in str(error.value)
