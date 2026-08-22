"""Authenticated encryption for Tesla token state persisted in Firestore."""

import base64
import json
import os
from datetime import datetime

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
        decoded = base64.urlsafe_b64decode(value.encode("ascii"))
        if len(decoded) < 13:
            raise ValueError("Encrypted Tesla token state is invalid")
        plaintext = self._cipher.decrypt(
            decoded[:12],
            decoded[12:],
            owner_user_id.encode("utf-8"),
        )
        document = json.loads(plaintext.decode("utf-8"))
        return TokenSet(
            access_token=str(document["access_token"]),
            refresh_token=str(document["refresh_token"]),
            expires_at=datetime.fromisoformat(str(document["expires_at"])),
            scopes=tuple(str(scope) for scope in document["scopes"]),
            tesla_subject=str(document["tesla_subject"]),
        )
