"""Platform authentication, allowlist, and tenant-isolation tests."""

from dataclasses import replace

import pytest
from tesla_personal_platform.auth import (
    AllowedUser,
    Authenticator,
    CallerIdentityClaimError,
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
from tesla_personal_platform.auth.admin import UserAdminService
from tesla_personal_platform.auth.memory import InMemoryIdentityStore
from tesla_personal_platform.mcp_gateway.auth_boundary import GatewayAuthBoundary
from tesla_personal_platform.mcp_gateway.main import _decode_json_request


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


def test_json_decoder_rejects_parser_recursion_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def raise_recursion_error(_: str) -> object:
        raise RecursionError("simulated parser nesting failure")

    monkeypatch.setattr(
        "tesla_personal_platform.mcp_gateway.main.json.loads",
        raise_recursion_error,
    )

    with pytest.raises(ValueError, match="safe nesting depth"):
        _decode_json_request(b"{}")


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


class RecordingDatasetProvisioner:
    def __init__(self) -> None:
        self.datasets: list[str] = []

    def provision(self, user: AllowedUser) -> None:
        self.datasets.append(user.dataset_id)


class FailingDatasetProvisioner:
    def provision(self, user: AllowedUser) -> None:
        del user
        raise RuntimeError("simulated provisioning failure")


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
