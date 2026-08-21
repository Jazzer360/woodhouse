"""Per-user BigQuery dataset provisioning for the manual allowlist workflow."""

from collections.abc import Iterable

from google.cloud import bigquery
from tesla_personal_platform.auth.models import AllowedUser

DATA_VIEWER_ACCESS_ROLE = "READER"
DATA_EDITOR_ACCESS_ROLE = "WRITER"
SERVICE_ACCOUNT_ENTITY_TYPE = "userByEmail"


def restricted_service_account_access(
    existing: Iterable[bigquery.AccessEntry],
    gateway_service_account: str,
    processor_service_account: str,
) -> list[bigquery.AccessEntry]:
    """Return the exact isolated BigQuery dataViewer/dataEditor-equivalent ACL."""
    del existing
    return [
        bigquery.AccessEntry(
            DATA_VIEWER_ACCESS_ROLE,
            SERVICE_ACCOUNT_ENTITY_TYPE,
            gateway_service_account,
        ),
        bigquery.AccessEntry(
            DATA_EDITOR_ACCESS_ROLE,
            SERVICE_ACCOUNT_ENTITY_TYPE,
            processor_service_account,
        ),
    ]


class BigQueryDatasetProvisioner:
    """Create or repair one opaque, non-expiring, per-user dataset."""

    def __init__(
        self,
        client: bigquery.Client,
        project_id: str,
        location: str,
        gateway_service_account: str,
        processor_service_account: str,
    ) -> None:
        self._client = client
        self._project_id = project_id
        self._location = location
        self._gateway_service_account = gateway_service_account
        self._processor_service_account = processor_service_account

    def provision(self, user: AllowedUser) -> None:
        """Idempotently enforce location, indefinite retention, labels, and access."""
        dataset = bigquery.Dataset(f"{self._project_id}.{user.dataset_id}")
        dataset.location = self._location
        dataset.description = "Isolated Tesla history for one approved platform user."
        dataset.default_table_expiration_ms = None
        dataset.default_partition_expiration_ms = None
        dataset.access_entries = restricted_service_account_access(
            (),
            self._gateway_service_account,
            self._processor_service_account,
        )
        dataset.labels = {
            "application": "tesla-personal-platform",
            "data_class": "restricted-user-telemetry",
            "managed_by": "add-user",
        }
        self._client.create_dataset(dataset, exists_ok=True, timeout=30)

        current = self._client.get_dataset(dataset.reference, timeout=30)
        if current.location != self._location:
            raise ValueError(
                f"Existing dataset {user.dataset_id} is in {current.location}, not {self._location}"
            )
        current.default_table_expiration_ms = None
        current.default_partition_expiration_ms = None
        current.access_entries = restricted_service_account_access(
            current.access_entries,
            self._gateway_service_account,
            self._processor_service_account,
        )
        self._client.update_dataset(
            current,
            [
                "access_entries",
                "default_partition_expiration_ms",
                "default_table_expiration_ms",
            ],
            timeout=30,
        )
