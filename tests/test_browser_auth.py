"""Browser onboarding authentication and rendering tests."""

from datetime import UTC, datetime, timedelta
from typing import cast
from urllib.parse import parse_qs, urlsplit
from urllib.request import Request

import pytest
from tesla_personal_platform.auth import (
    AllowedUser,
    OIDCAccessTokenVerifier,
    OIDCIDTokenVerifier,
    UserContext,
    UserDisabledError,
    UserStatus,
    VerifiedIdentity,
)
from tesla_personal_platform.auth.memory import InMemoryIdentityStore
from tesla_personal_platform.mcp_gateway.browser_auth import (
    BrowserAuthenticationError,
    BrowserAuthService,
    BrowserLoginResult,
    BrowserLoginStart,
    BrowserOIDCConfig,
    BrowserSession,
    PendingBrowserLogin,
    _RejectRedirects,
)
from tesla_personal_platform.mcp_gateway.onboarding_web import (
    onboarding_page,
    telemetry_configuration_page,
)

NOW = datetime(2026, 8, 22, tzinfo=UTC)
IDENTITY = VerifiedIdentity(
    "https://tenant.example.auth0.com/",
    "auth0|homer",
    "homer@example.com",
    True,
)


class MemoryBrowserStore:
    def __init__(self) -> None:
        self.logins: dict[str, PendingBrowserLogin] = {}
        self.sessions: dict[str, BrowserSession] = {}

    def create_login(self, state: str, login: PendingBrowserLogin) -> None:
        self.logins[state] = login

    def consume_login(self, state: str, *, now: datetime) -> PendingBrowserLogin:
        try:
            login = self.logins.pop(state)
        except KeyError as error:
            raise BrowserAuthenticationError("invalid") from error
        if login.expires_at <= now:
            raise BrowserAuthenticationError("expired")
        return login

    def create_session(self, token: str, session: BrowserSession) -> None:
        self.sessions[token] = session

    def get_session(self, token: str, *, now: datetime) -> BrowserSession:
        session = self.sessions[token]
        if session.expires_at <= now:
            raise BrowserAuthenticationError("expired")
        return session

    def delete_session(self, token: str) -> None:
        self.sessions.pop(token, None)


class AccessVerifier:
    def verify(self, token: str) -> VerifiedIdentity:
        assert token == "access-value"
        return IDENTITY


class IDVerifier:
    def verify(self, token: str, *, nonce: str) -> VerifiedIdentity:
        assert token == "id-value"
        assert nonce
        return IDENTITY


class TokenClient:
    def exchange_code(self, code: str, *, code_verifier: str) -> dict[str, str]:
        assert code == "authorization-code"
        assert code_verifier
        return {"access_token": "access-value", "id_token": "id-value"}


def service(
    identities: InMemoryIdentityStore,
) -> tuple[BrowserAuthService, MemoryBrowserStore]:
    store = MemoryBrowserStore()
    config = BrowserOIDCConfig(
        issuer=IDENTITY.issuer,
        audience="https://woodhouse.derekjass.com/mcp",
        client_id="browser-client",
        client_secret="test-only-secret",
        redirect_uri="https://woodhouse.derekjass.com/auth/callback",
    )
    return (
        BrowserAuthService(
            config=config,
            store=store,
            identities=identities,
            access_tokens=cast(OIDCAccessTokenVerifier, AccessVerifier()),
            id_tokens=cast(OIDCIDTokenVerifier, IDVerifier()),
            token_client=TokenClient(),
        ),
        store,
    )


def allowed_user(status: UserStatus = UserStatus.ACTIVE) -> AllowedUser:
    return AllowedUser("homer@example.com", "usr_homer", "tesla_u_homer", status)


def begin(instance: BrowserAuthService) -> tuple[str, str]:
    login = instance.start(now=NOW)
    return login.authorization_url, login.browser_binding_token


def test_browser_login_uses_pkce_nonce_state_and_resource_audience() -> None:
    instance, store = service(InMemoryIdentityStore([allowed_user()]))

    location, binding = begin(instance)
    query = parse_qs(urlsplit(location).query)

    assert location.startswith("https://tenant.example.auth0.com/authorize?")
    assert query["audience"] == ["https://woodhouse.derekjass.com/mcp"]
    assert query["code_challenge_method"] == ["S256"]
    assert "mcp:access" in query["scope"][0]
    pending = store.logins[query["state"][0]]
    assert pending.nonce == query["nonce"][0]
    assert pending.code_verifier not in location
    assert pending.browser_binding_hash != binding


def test_first_approved_browser_login_binds_and_creates_opaque_session() -> None:
    identities = InMemoryIdentityStore([allowed_user()])
    instance, store = service(identities)
    location, binding = begin(instance)
    state = parse_qs(urlsplit(location).query)["state"][0]

    result = instance.complete(
        state=state,
        code="authorization-code",
        browser_binding_token=binding,
        now=NOW,
    )
    context, session = instance.authenticate_session(result.session_token, now=NOW)

    assert context.user_id == "usr_homer"
    assert session.subject == IDENTITY.subject
    assert result.session_token in store.sessions
    assert "access-value" not in str(store.sessions)
    assert "id-value" not in str(store.sessions)


def test_browser_login_state_is_bound_to_the_initiating_browser() -> None:
    instance, store = service(InMemoryIdentityStore([allowed_user()]))
    location, _ = begin(instance)
    state = parse_qs(urlsplit(location).query)["state"][0]

    with pytest.raises(BrowserAuthenticationError, match="browser binding"):
        instance.complete(
            state=state,
            code="authorization-code",
            browser_binding_token="different-browser",
            now=NOW,
        )

    assert state not in store.logins
    assert store.sessions == {}


def test_disabled_user_is_rechecked_on_every_browser_session_use() -> None:
    identities = InMemoryIdentityStore([allowed_user()])
    instance, _ = service(identities)
    location, binding = begin(instance)
    state = parse_qs(urlsplit(location).query)["state"][0]
    result = instance.complete(
        state=state,
        code="authorization-code",
        browser_binding_token=binding,
        now=NOW,
    )
    bound = identities.get_user("homer@example.com")
    assert bound is not None
    identities.replace_user(
        AllowedUser(
            bound.invitation_email,
            bound.user_id,
            bound.dataset_id,
            UserStatus.DISABLED,
            bound.oidc_issuer,
            bound.oidc_subject,
        )
    )

    with pytest.raises(UserDisabledError):
        instance.authenticate_session(result.session_token, now=NOW + timedelta(minutes=1))


def test_sensitive_browser_auth_values_are_absent_from_repr() -> None:
    config = BrowserOIDCConfig(
        issuer=IDENTITY.issuer,
        audience="https://woodhouse.derekjass.com/mcp",
        client_id="browser-client",
        client_secret="sensitive-client-secret",
        redirect_uri="https://woodhouse.derekjass.com/auth/callback",
    )
    pending = PendingBrowserLogin(
        "sensitive-verifier",
        "sensitive-nonce",
        "sensitive-binding-hash",
        NOW,
    )
    session = BrowserSession(IDENTITY.issuer, IDENTITY.subject, "sensitive-csrf", NOW)
    result = BrowserLoginResult(
        "sensitive-session",
        "sensitive-result-csrf",
        UserContext("usr_homer", "tesla_u_homer", IDENTITY.issuer, IDENTITY.subject),
    )
    start = BrowserLoginStart(
        "https://tenant.example.auth0.com/authorize?state=sensitive-state",
        "sensitive-binding",
    )

    rendered = repr((config, pending, session, result, start))
    assert "sensitive" not in rendered


def test_browser_token_exchange_redirects_are_rejected() -> None:
    handler = _RejectRedirects()
    request = Request("https://tenant.example.auth0.com/oauth/token", method="POST")

    assert (
        handler.redirect_request(  # type: ignore[no-untyped-call]
            request,
            None,
            307,
            "Temporary Redirect",
            {},
            "https://attacker.example/token",
        )
        is None
    )


def test_onboarding_page_lists_each_vehicle_and_escapes_content() -> None:
    html = onboarding_page(
        csrf_token="csrf-value",
        vehicles=[
            {
                "vehicle_id": "veh_one",
                "display_name": "One <script>",
                "state": "offline",
                "virtual_key_status": "pending",
                "virtual_key_pairing_url": "https://www.tesla.com/_ak/example?vin=ONE",
            },
            {
                "vehicle_id": "veh_two",
                "display_name": "Two",
                "state": "online",
                "virtual_key_status": "paired",
                "virtual_key_pairing_url": "https://www.tesla.com/_ak/example?vin=TWO",
            },
        ],
    ).decode()

    assert "One &lt;script&gt;" in html
    assert "/onboarding/vehicles/veh_one/refresh" in html
    assert "/onboarding/vehicles/veh_two/refresh" in html
    assert html.count("Pair Virtual Key in Tesla") == 1
    assert "owner_user_id" not in html


def test_telemetry_page_preserves_existing_transport_maintenance_opt_in() -> None:
    html = telemetry_configuration_page(
        {
            "vehicle_id": "veh_one",
            "display_name": "Woodhouse",
            "desired_config_hash": "a" * 64,
            "diff": {"status": "in_sync", "changes": {}},
            "persisted": {"transport_maintenance_opt_in": True},
        },
        "csrf-value",
    ).decode()

    assert 'value="yes" checked' in html
    assert "a" * 64 in html
    assert "/onboarding/vehicles/veh_one/telemetry/verify" in html
    assert "Verify and record provenance" in html
