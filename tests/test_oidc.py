"""Provider-neutral OIDC verifier tests without external network calls."""

from collections.abc import Mapping
from typing import Any

import pytest
from tesla_personal_platform.auth import InvalidTokenError
from tesla_personal_platform.auth.oidc import OIDCAccessTokenVerifier, OIDCIDTokenVerifier

ISSUER = "https://tenant.example.auth0.com/"
RESOURCE = "https://woodhouse.derekjass.com/mcp"


class Decoder:
    def __init__(self, claims: Mapping[str, Any]) -> None:
        self.claims = claims
        self.audiences: list[str] = []

    def __call__(self, token: str, audience: str) -> Mapping[str, Any]:
        assert token == "signed-token"
        self.audiences.append(audience)
        return self.claims


def claims(**updates: object) -> dict[str, object]:
    result: dict[str, object] = {
        "iss": ISSUER,
        "sub": "auth0|homer",
        "scope": "openid email profile mcp:access",
        "email": "homer@example.com",
        "email_verified": True,
    }
    result.update(updates)
    return result


def test_access_token_requires_exact_resource_scope_and_issuer() -> None:
    decoder = Decoder(claims())
    verifier = OIDCAccessTokenVerifier(
        issuer=ISSUER,
        audience=RESOURCE,
        required_scopes=frozenset({"mcp:access"}),
        decoder=decoder,
    )

    identity = verifier.verify("signed-token")

    assert decoder.audiences == [RESOURCE]
    assert identity.subject == "auth0|homer"
    assert identity.email_verified is True


def test_access_token_missing_required_scope_is_rejected() -> None:
    verifier = OIDCAccessTokenVerifier(
        issuer=ISSUER,
        audience=RESOURCE,
        required_scopes=frozenset({"mcp:access"}),
        decoder=Decoder(claims(scope="openid email")),
    )

    with pytest.raises(InvalidTokenError, match="required scopes"):
        verifier.verify("signed-token")


def test_userinfo_fallback_must_match_access_token_subject() -> None:
    verifier = OIDCAccessTokenVerifier(
        issuer=ISSUER,
        audience=RESOURCE,
        required_scopes=frozenset({"mcp:access"}),
        decoder=Decoder(claims(email=None, email_verified=False)),
        userinfo=lambda _token: {
            "sub": "auth0|marge",
            "email": "homer@example.com",
            "email_verified": True,
        },
    )

    with pytest.raises(InvalidTokenError, match="subject"):
        verifier.verify("signed-token")


def test_id_token_requires_login_nonce() -> None:
    verifier = OIDCIDTokenVerifier(
        issuer=ISSUER,
        audience="browser-client",
        decoder=Decoder(claims(nonce="expected")),
    )

    with pytest.raises(InvalidTokenError, match="nonce"):
        verifier.verify("signed-token", nonce="different")

    assert verifier.verify("signed-token", nonce="expected").subject == "auth0|homer"
