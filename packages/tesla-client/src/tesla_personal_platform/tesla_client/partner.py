"""Idempotent Tesla partner registration and public-key verification."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from urllib.parse import urlencode

from cryptography.exceptions import UnsupportedAlgorithm
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from tesla_personal_platform.tesla_client.errors import (
    TeslaAPIError,
    TeslaConfigurationError,
)
from tesla_personal_platform.tesla_client.fleet import normalize_base_url
from tesla_personal_platform.tesla_client.models import JsonValue, ValueResponse, json_object
from tesla_personal_platform.tesla_client.oauth import TESLA_TOKEN_URL
from tesla_personal_platform.tesla_client.transport import HttpResponse, HttpTransport


@dataclass(frozen=True, slots=True)
class PartnerRegistration:
    """Safe result for one regional registration check."""

    base_url: str
    status: str


class TeslaPartnerClient:
    """Typed application-level partner endpoints; never exposed to MCP callers."""

    def __init__(self, transport: HttpTransport) -> None:
        self._transport = transport

    def register(
        self,
        partner_token: str,
        *,
        base_url: str,
        domain: str,
        allow_existing: bool = False,
    ) -> PartnerRegistration:
        normalized_base_url = normalize_base_url(base_url)
        response = self._transport.request(
            "POST",
            f"{normalized_base_url}/api/1/partner_accounts",
            headers={"Authorization": f"Bearer {partner_token}"},
            json_body={"domain": _normalize_domain(domain)},
        )
        if allow_existing and response.status == 409:
            return PartnerRegistration(normalized_base_url, "already_registered")
        _json_mapping(response, "Tesla partner registration")
        return PartnerRegistration(normalized_base_url, "registered")

    def public_key(self, partner_token: str, *, base_url: str, domain: str) -> ValueResponse:
        normalized_base_url = normalize_base_url(base_url)
        query = urlencode({"domain": _normalize_domain(domain)})
        document = _json_mapping(
            self._transport.request(
                "GET",
                f"{normalized_base_url}/api/1/partner_accounts/public_key?{query}",
                headers={"Authorization": f"Bearer {partner_token}"},
            ),
            "Tesla public-key verification",
        )
        value = document.get("response", document)
        return ValueResponse(
            json_object({str(key): item for key, item in value.items()})
            if isinstance(value, Mapping)
            else _safe_value(value)
        )

    def fleet_telemetry_errors(self, partner_token: str, *, base_url: str) -> ValueResponse:
        return self._get_value(partner_token, base_url, "fleet_telemetry_errors")

    def fleet_telemetry_error_vins(self, partner_token: str, *, base_url: str) -> ValueResponse:
        return self._get_value(partner_token, base_url, "fleet_telemetry_error_vins")

    def _get_value(self, token: str, base_url: str, endpoint: str) -> ValueResponse:
        document = _json_mapping(
            self._transport.request(
                "GET",
                f"{normalize_base_url(base_url)}/api/1/partner_accounts/{endpoint}",
                headers={"Authorization": f"Bearer {token}"},
            ),
            "Tesla partner diagnostic",
        )
        value = document.get("response")
        return ValueResponse(
            json_object({str(key): item for key, item in value.items()})
            if isinstance(value, Mapping)
            else _safe_value(value)
        )


class PartnerRegistrar:
    """Create or confirm a registration, then verify its hosted public key."""

    def __init__(self, transport: HttpTransport) -> None:
        self._transport = transport
        self._partner = TeslaPartnerClient(transport)

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
        expected_key = _public_key_point(expected_public_key_pem)
        results: list[PartnerRegistration] = []
        for value in base_urls:
            base_url = normalize_base_url(value)
            token = self._partner_token(client_id, client_secret, base_url)
            registration = self._partner.register(
                token,
                base_url=base_url,
                domain=normalized_domain,
                allow_existing=True,
            )
            verified = self._registered_key(token, base_url, normalized_domain)
            if verified is None or _public_key_point(verified) != expected_key:
                raise TeslaConfigurationError(
                    f"Tesla did not verify the expected public key for {base_url}"
                )
            results.append(registration)
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
    except (UnicodeDecodeError, ValueError):
        raise TeslaAPIError(f"{operation} returned invalid JSON") from None
    if not isinstance(document, Mapping):
        raise TeslaAPIError(f"{operation} returned an invalid document")
    return document


def _safe_value(value: object) -> JsonValue:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return [
            json_object({str(key): nested for key, nested in item.items()})
            if isinstance(item, Mapping)
            else _safe_value(item)
            for item in value
        ]
    raise TeslaAPIError("Tesla partner response payload is invalid", category="invalid_payload")


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


def _public_key_point(value: str) -> bytes:
    encoded = value.strip()
    try:
        if encoded.startswith("-----BEGIN PUBLIC KEY-----"):
            key = serialization.load_pem_public_key(encoded.encode("ascii"))
            if not isinstance(key, ec.EllipticCurvePublicKey) or not isinstance(
                key.curve, ec.SECP256R1
            ):
                raise TeslaConfigurationError("Expected a secp256r1 public key")
            return key.public_bytes(
                serialization.Encoding.X962,
                serialization.PublicFormat.UncompressedPoint,
            )

        point = bytes.fromhex(encoded)
        if len(point) != 65 or point[0] != 0x04:
            raise TeslaConfigurationError("Expected an uncompressed secp256r1 public key")
        key = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), point)
        return key.public_bytes(
            serialization.Encoding.X962,
            serialization.PublicFormat.UncompressedPoint,
        )
    except (UnicodeEncodeError, UnsupportedAlgorithm, ValueError) as error:
        raise TeslaConfigurationError(
            "Expected a secp256r1 public key in PEM or uncompressed-point hex"
        ) from error
