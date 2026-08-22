"""Google-specific verification adapter tests without network access."""

import pytest
from google.oauth2 import id_token
from tesla_personal_platform.auth import InvalidTokenError
from tesla_personal_platform.auth.google_oidc import (
    GOOGLE_CANONICAL_ISSUER,
    GoogleOIDCVerifier,
)


@pytest.mark.parametrize("issuer", ["accounts.google.com", "https://accounts.google.com"])
def test_google_oidc_verifier_returns_only_verified_identity_claims(
    monkeypatch: pytest.MonkeyPatch,
    issuer: str,
) -> None:
    def verify(token: str, request: object, audience: str) -> dict[str, object]:
        del token, request
        assert audience == "configured-client-id"
        return {
            "iss": issuer,
            "sub": "immutable-google-subject",
            "email": "homer@example.com",
            "email_verified": True,
        }

    monkeypatch.setattr(id_token, "verify_oauth2_token", verify)

    identity = GoogleOIDCVerifier("configured-client-id").verify("signed-token")

    assert identity.issuer == GOOGLE_CANONICAL_ISSUER
    assert identity.subject == "immutable-google-subject"
    assert identity.email == "homer@example.com"
    assert identity.email_verified is True


def test_google_oidc_verifier_rejects_untrusted_issuer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def verify(token: str, request: object, audience: str) -> dict[str, object]:
        del token, request, audience
        return {"iss": "https://attacker.example", "sub": "subject"}

    monkeypatch.setattr(id_token, "verify_oauth2_token", verify)

    with pytest.raises(InvalidTokenError, match="issuer"):
        GoogleOIDCVerifier("configured-client-id").verify("signed-token")
