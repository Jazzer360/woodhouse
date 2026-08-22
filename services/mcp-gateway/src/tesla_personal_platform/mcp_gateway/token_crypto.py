"""Authenticated encryption for Tesla token state persisted in Firestore."""

import base64
import json
import os
from datetime import UTC, datetime

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from tesla_personal_platform.tesla_client import TokenSet


class TokenCipher:
    """Encrypt rotating Tesla credentials with a Secret Manager supplied key."""

    def __init__(self, key: bytes) -> None:
        if len(key) != 32:
            raise ValueError("Tesla token encryption key must contain exactly 32 bytes")
        self._cipher = AESGCM(key)

    @classmethod
    def from_base64(cls, encoded: str) -> "TokenCipher":
        try:
            key = base64.b64decode(encoded.strip(), validate=True)
        except ValueError as error:
            raise ValueError("Tesla token encryption key must be valid base64") from error
        return cls(key)

    def encrypt(self, tokens: TokenSet, *, owner_user_id: str) -> str:
        plaintext = json.dumps(
            {
                "access_token": tokens.access_token,
                "refresh_token": tokens.refresh_token,
                "expires_at": tokens.expires_at.isoformat(),
                "scopes": list(tokens.scopes),
                "tesla_subject": tokens.tesla_subject,
            },
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        nonce = os.urandom(12)
        ciphertext = self._cipher.encrypt(nonce, plaintext, owner_user_id.encode("utf-8"))
        return base64.urlsafe_b64encode(nonce + ciphertext).decode("ascii")

    def decrypt(self, value: str, *, owner_user_id: str) -> TokenSet:
        try:
            decoded = base64.b64decode(
                value.encode("ascii"),
                altchars=b"-_",
                validate=True,
            )
            if len(decoded) < 28:  # 12-byte nonce plus the 16-byte GCM tag
                raise ValueError("ciphertext is too short")
            plaintext = self._cipher.decrypt(
                decoded[:12],
                decoded[12:],
                owner_user_id.encode("utf-8"),
            )
            document = json.loads(plaintext.decode("utf-8"))
            if not isinstance(document, dict):
                raise ValueError("token document is not an object")
            access_token = _required_string(document, "access_token")
            refresh_token = _required_string(document, "refresh_token")
            tesla_subject = _required_string(document, "tesla_subject")
            raw_scopes = document["scopes"]
            if (
                not isinstance(raw_scopes, list)
                or not raw_scopes
                or not all(isinstance(scope, str) and scope for scope in raw_scopes)
            ):
                raise ValueError("token scopes are invalid")
            expires_at = datetime.fromisoformat(_required_string(document, "expires_at"))
            if expires_at.utcoffset() is None:
                expires_at = expires_at.replace(tzinfo=UTC)
            return TokenSet(
                access_token=access_token,
                refresh_token=refresh_token,
                expires_at=expires_at,
                scopes=tuple(raw_scopes),
                tesla_subject=tesla_subject,
            )
        except (
            InvalidTag,
            KeyError,
            TypeError,
            ValueError,
        ) as error:
            raise ValueError("Encrypted Tesla token state is invalid") from error


def _required_string(document: dict[str, object], key: str) -> str:
    value = document[key]
    if not isinstance(value, str) or not value:
        raise ValueError(f"token field {key} is invalid")
    return value
