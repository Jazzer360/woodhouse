"""Trusted CLI for regional Tesla partner registration and verification."""

import argparse
import json
from typing import NoReturn

from google.api_core.exceptions import GoogleAPICallError
from google.cloud import secretmanager
from tesla_personal_platform.tesla_client.errors import TeslaAPIError
from tesla_personal_platform.tesla_client.partner import PartnerRegistrar
from tesla_personal_platform.tesla_client.transport import UrllibTransport

REGIONAL_BASE_URLS = {
    "na": "https://fleet-api.prd.na.vn.cloud.tesla.com",
    "eu": "https://fleet-api.prd.eu.vn.cloud.tesla.com",
    "cn": "https://fleet-api.prd.cn.vn.cloud.tesla.cn",
}


def _secret(client: secretmanager.SecretManagerServiceClient, name: str) -> str:
    response = client.access_secret_version(request={"name": name})
    value = response.payload.data.decode("utf-8")
    if not value:
        raise ValueError("Secret Manager version is empty")
    return value


def _fail(parser: argparse.ArgumentParser, error: Exception) -> NoReturn:
    parser.exit(1, f"error: {error}\n")


def register_partner_main() -> int:
    """Register only missing regional partner records and verify their public key."""
    parser = argparse.ArgumentParser(description="Register or verify Tesla partner account")
    parser.add_argument("--project-id", required=True)
    parser.add_argument("--client-id", required=True, help="Tesla application client ID")
    parser.add_argument("--domain", required=True, help="Bare Tesla application hostname")
    parser.add_argument(
        "--region",
        action="append",
        choices=sorted(REGIONAL_BASE_URLS),
        default=None,
        help="Region to register; repeat as needed (default: na)",
    )
    parser.add_argument("--client-secret-name", default="tesla-client-secret")
    parser.add_argument("--public-key-secret-name", default="tesla-command-public-key")
    arguments = parser.parse_args()
    regions = arguments.region or ["na"]
    base_urls = [REGIONAL_BASE_URLS[region] for region in regions]
    manager = secretmanager.SecretManagerServiceClient()
    try:
        client_secret = _secret(
            manager,
            f"projects/{arguments.project_id}/secrets/{arguments.client_secret_name}/versions/latest",
        )
        public_key = _secret(
            manager,
            f"projects/{arguments.project_id}/secrets/{arguments.public_key_secret_name}/versions/latest",
        )
        results = PartnerRegistrar(UrllibTransport()).ensure_registered(
            client_id=arguments.client_id,
            client_secret=client_secret,
            domain=arguments.domain,
            expected_public_key_pem=public_key,
            base_urls=base_urls,
        )
    except (GoogleAPICallError, TeslaAPIError, UnicodeDecodeError, ValueError) as error:
        _fail(parser, error)
    print(
        json.dumps(
            {
                "domain": arguments.domain,
                "regions": [
                    {"base_url": result.base_url, "status": result.status} for result in results
                ],
            },
            sort_keys=True,
        )
    )
    return 0
