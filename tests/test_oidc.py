"""Provider-neutral OIDC verifier tests without external network calls."""

from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from tesla_personal_platform.auth import InvalidTokenError
from tesla_personal_platform.auth.oidc import (
    OIDCAccessTokenVerifier,
    OIDCIDTokenVerifier,
    OIDCJWTDecoder,
)

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


def test_access_token_without_email_still_returns_immutable_identity() -> None:
    verifier = OIDCAccessTokenVerifier(
        issuer=ISSUER,
        audience=RESOURCE,
        required_scopes=frozenset({"mcp:access"}),
        decoder=Decoder(claims(email=None, email_verified=False)),
    )

    identity = verifier.verify("signed-token")

    assert identity.subject == "auth0|homer"
    assert identity.email is None
    assert identity.email_verified is False


def test_production_decoder_enforces_signature_algorithm_and_registered_claims() -> None:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    other_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    now = datetime.now(UTC)
    base_claims = {
        "iss": ISSUER,
        "sub": "auth0|homer",
        "aud": RESOURCE,
        "iat": now,
        "exp": now + timedelta(minutes=5),
    }
    decoder = OIDCJWTDecoder(
        ISSUER,
        key_resolver=lambda _token: private_key.public_key(),
    )

    token = jwt.encode(base_claims, private_key, algorithm="RS256")
    assert decoder(token, RESOURCE)["sub"] == "auth0|homer"

    invalid_claim_sets = [
        {**base_claims, "iss": "https://untrusted.example/"},
        {**base_claims, "aud": "https://other.example/mcp"},
        {key: value for key, value in base_claims.items() if key != "iat"},
    ]
    for invalid_claims in invalid_claim_sets:
        invalid = jwt.encode(invalid_claims, private_key, algorithm="RS256")
        with pytest.raises(InvalidTokenError, match="verification failed"):
            decoder(invalid, RESOURCE)

    invalid_signature = jwt.encode(base_claims, other_key, algorithm="RS256")
    with pytest.raises(InvalidTokenError, match="verification failed"):
        decoder(invalid_signature, RESOURCE)

    wrong_algorithm = jwt.encode(
        base_claims, "test-only-key-that-is-at-least-32b", algorithm="HS256"
    )
    with pytest.raises(InvalidTokenError, match="verification failed"):
        decoder(wrong_algorithm, RESOURCE)


def test_id_token_requires_login_nonce() -> None:
    verifier = OIDCIDTokenVerifier(
        issuer=ISSUER,
        audience="browser-client",
        decoder=Decoder(claims(nonce="expected")),
    )

    with pytest.raises(InvalidTokenError, match="nonce"):
        verifier.verify("signed-token", nonce="different")

    assert verifier.verify("signed-token", nonce="expected").subject == "auth0|homer"
