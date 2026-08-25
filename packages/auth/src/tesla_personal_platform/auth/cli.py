"""Command-line entry points for trusted manual user administration."""

import argparse
import json
from typing import NoReturn

from google.api_core.exceptions import GoogleAPICallError
from google.cloud import bigquery, firestore
from tesla_personal_platform.auth.admin import UserAdminService
from tesla_personal_platform.auth.bigquery_admin import BigQueryDatasetProvisioner
from tesla_personal_platform.auth.errors import AuthenticationError
from tesla_personal_platform.auth.firestore import FirestoreAllowlistAdminStore
from tesla_personal_platform.auth.models import AllowedUser

DEFAULT_LOCATION = "us-central1"


def _base_parser(description: str) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument("--project-id", required=True, help="Explicit target GCP project")
    parser.add_argument("--email", required=True, help="Invitation email lookup key")
    return parser


def _service(arguments: argparse.Namespace) -> UserAdminService:
    project_id = str(arguments.project_id)
    gateway = getattr(arguments, "gateway_service_account", None) or (
        f"tpp-mcp-gateway@{project_id}.iam.gserviceaccount.com"
    )
    processor = getattr(arguments, "processor_service_account", None) or (
        f"tpp-telemetry-processor@{project_id}.iam.gserviceaccount.com"
    )
    owner = f"tpp-dataset-owner@{project_id}.iam.gserviceaccount.com"
    admin = f"tpp-user-admin@{project_id}.iam.gserviceaccount.com"
    location = str(getattr(arguments, "location", DEFAULT_LOCATION))
    allowlist = FirestoreAllowlistAdminStore(firestore.Client(project=project_id))
    datasets = BigQueryDatasetProvisioner(
        bigquery.Client(project=project_id),
        project_id,
        location,
        owner,
        gateway,
        processor,
        admin,
    )
    return UserAdminService(allowlist, datasets)


def _print_result(action: str, user: AllowedUser) -> None:
    print(
        json.dumps(
            {
                "action": action,
                "dataset_id": user.dataset_id,
                "email": user.invitation_email,
                "status": user.status.value,
                "user_id": user.user_id,
            },
            sort_keys=True,
        )
    )


def _fail(parser: argparse.ArgumentParser, error: Exception) -> NoReturn:
    parser.exit(1, f"error: {error}\n")


def add_user_main() -> int:
    """Provision or repair one manual invitation and its analytics boundary."""
    parser = _base_parser("Add or repair one approved Tesla Personal Platform user")
    parser.add_argument("--location", default=DEFAULT_LOCATION)
    parser.add_argument("--notes")
    parser.add_argument("--gateway-service-account")
    parser.add_argument("--processor-service-account")
    arguments = parser.parse_args()
    try:
        user = _service(arguments).add_user(arguments.email, arguments.notes)
    except (AuthenticationError, GoogleAPICallError, ValueError) as error:
        _fail(parser, error)
    _print_result("add-user", user)
    return 0


def disable_user_main() -> int:
    """Disable one allowlist record without deleting history or identity state."""
    parser = _base_parser("Disable one Tesla Personal Platform user")
    arguments = parser.parse_args()
    try:
        user = _service(arguments).disable_user(arguments.email)
    except (AuthenticationError, GoogleAPICallError, ValueError) as error:
        _fail(parser, error)
    _print_result("disable-user", user)
    return 0


def reset_user_identity_main() -> int:
    """Clear one immutable binding for an explicit provider migration or recovery."""
    parser = _base_parser("Reset one platform user's OIDC binding")
    parser.add_argument(
        "--confirm-user-id",
        required=True,
        help="Opaque user_id printed by add-user; must exactly match the record",
    )
    arguments = parser.parse_args()
    try:
        user = _service(arguments).reset_user_identity(
            arguments.email,
            arguments.confirm_user_id,
        )
    except (AuthenticationError, GoogleAPICallError, ValueError) as error:
        _fail(parser, error)
    _print_result("reset-user-identity", user)
    return 0
