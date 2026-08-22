"""Idempotent Tesla partner registration and public-key verification."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from urllib.parse import urlencode

from tesla_personal_platform.tesla_client.errors import (
    TeslaAPIError,
    TeslaConfigurationError,
)
from tesla_personal_platform.tesla_client.fleet import normalize_base_url
from tesla_personal_platform.tesla_client.oauth import TESLA_TOKEN_URL
from tesla_personal_platform.tesla_client.transport import HttpResponse, HttpTransport


@dataclass(frozen=True, slots=True)
class PartnerRegistration:
    """Safe result for one regional registration check."""

    base_url: str
    status: str


class PartnerRegistrar:
    """Verify existing registration before creating it, then verify again."""

    def __init__(self, transport: HttpTransport) -> None:
        self._transport = transport

    def ensure_registered(
        self,
        *,
        client_id: str,
        client_secret: str,
        domain: str,
        expected_public_key_pem: str,
        base_urls: Sequence[str],
    ) -> list[PartnerRegistration]:
        if not client_id.strip() or not client_secret.strip():
            raise TeslaConfigurationError("Tesla partner credentials are required")
        normalized_domain = _normalize_domain(domain)
        expected_key = _normalize_pem(expected_public_key_pem)
        results: list[PartnerRegistration] = []
        for value in base_urls:
            base_url = normalize_base_url(value)
            token = self._partner_token(client_id, client_secret, base_url)
            current = self._registered_key(token, base_url, normalized_domain)
            if current is not None:
                if _normalize_pem(current) != expected_key:
                    raise TeslaConfigurationError(f"Tesla registration key mismatch for {base_url}")
                results.append(PartnerRegistration(base_url, "already_registered"))
                continue

            response = self._transport.request(
                "POST",
                f"{base_url}/api/1/partner_accounts",
                headers={"Authorization": f"Bearer {token}"},
                json_body={"domain": normalized_domain},
            )
            if (response.status < 200 or response.status >= 300) and response.status != 409:
                raise TeslaAPIError(
                    f"Tesla partner registration failed with status {response.status}"
                )
            verified = self._registered_key(token, base_url, normalized_domain)
            if verified is None or _normalize_pem(verified) != expected_key:
                raise TeslaConfigurationError(
                    f"Tesla did not verify the expected public key for {base_url}"
                )
            results.append(PartnerRegistration(base_url, "registered"))
        return results

    def _partner_token(self, client_id: str, client_secret: str, audience: str) -> str:
        response = self._transport.request(
            "POST",
            TESLA_TOKEN_URL,
            form={
                "grant_type": "client_credentials",
                "client_id": client_id,
                "client_secret": client_secret,
                "audience": audience,
                "scope": "openid vehicle_device_data vehicle_cmds vehicle_charging_cmds",
            },
        )
        document = _json_mapping(response, "Tesla partner token")
        token = document.get("access_token")
        if not isinstance(token, str) or not token:
            raise TeslaAPIError("Tesla partner token response is missing access_token")
        return token

    def _registered_key(self, token: str, base_url: str, domain: str) -> str | None:
        query = urlencode({"domain": domain})
        response = self._transport.request(
            "GET",
            f"{base_url}/api/1/partner_accounts/public_key?{query}",
            headers={"Authorization": f"Bearer {token}"},
        )
        if response.status == 404:
            return None
        document = _json_mapping(response, "Tesla public-key verification")
        payload = document.get("response", document)
        if isinstance(payload, str):
            return payload
        if isinstance(payload, Mapping):
            key = payload.get("public_key")
            if isinstance(key, str):
                return key
        raise TeslaAPIError("Tesla public-key verification response is invalid")


def _json_mapping(response: HttpResponse, operation: str) -> Mapping[str, object]:
    if response.status < 200 or response.status >= 300:
        raise TeslaAPIError(f"{operation} failed with status {response.status}")
    try:
        document = response.json()
    except (UnicodeDecodeError, ValueError) as error:
        raise TeslaAPIError(f"{operation} returned invalid JSON") from error
    if not isinstance(document, Mapping):
        raise TeslaAPIError(f"{operation} returned an invalid document")
    return document


def _normalize_domain(value: str) -> str:
    domain = value.strip().lower().rstrip("/")
    labels = domain.split(".")
    if (
        len(domain) > 253
        or len(labels) < 2
        or any(
            not label
            or len(label) > 63
            or label.startswith("-")
            or label.endswith("-")
            or not label.isascii()
            or any(not (character.isalnum() or character == "-") for character in label)
            for label in labels
        )
    ):
        raise TeslaConfigurationError("Tesla application domain must be a bare hostname")
    return domain


def _normalize_pem(value: str) -> str:
    normalized = "\n".join(line.strip() for line in value.strip().splitlines())
    if not normalized.startswith("-----BEGIN PUBLIC KEY-----") or not normalized.endswith(
        "-----END PUBLIC KEY-----"
    ):
        raise TeslaConfigurationError("Expected a PEM-encoded public key")
    return normalized
