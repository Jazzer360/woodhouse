"""BigQuery per-user ACL construction tests."""

from google.cloud import bigquery
from tesla_personal_platform.auth.bigquery_admin import (
    BigQueryDatasetProvisioner,
    restricted_service_account_access,
)
from tesla_personal_platform.auth.models import AllowedUser, UserStatus


def test_dataset_access_is_exact_minimal_and_idempotent() -> None:
    existing = [bigquery.AccessEntry("OWNER", "userByEmail", "operator@example.com")]

    once = restricted_service_account_access(
        existing, "gateway@example.iam", "processor@example.iam"
    )
    twice = restricted_service_account_access(once, "gateway@example.iam", "processor@example.iam")

    assert twice == once
    assert {(entry.role, entry.entity_type, entry.entity_id) for entry in twice} == {
        ("READER", "userByEmail", "gateway@example.iam"),
        ("WRITER", "userByEmail", "processor@example.iam"),
    }


class RecordingBigQueryClient:
    def __init__(self, current: bigquery.Dataset) -> None:
        self.current = current
        self.updated_fields: list[str] = []

    def create_dataset(
        self,
        dataset: bigquery.Dataset,
        *,
        exists_ok: bool,
        timeout: int,
    ) -> bigquery.Dataset:
        del exists_ok, timeout
        return dataset

    def get_dataset(self, reference: object, *, timeout: int) -> bigquery.Dataset:
        del reference, timeout
        return self.current

    def update_dataset(
        self,
        dataset: bigquery.Dataset,
        fields: list[str],
        *,
        timeout: int,
    ) -> bigquery.Dataset:
        del timeout
        self.current = dataset
        self.updated_fields = fields
        return dataset


def test_existing_dataset_metadata_and_access_drift_are_repaired() -> None:
    current = bigquery.Dataset("project.tesla_u_homer")
    current.location = "us-central1"
    current.description = "drifted"
    current.labels = {"managed_by": "someone-else"}
    current.default_table_expiration_ms = 86_400_000
    current.default_partition_expiration_ms = 86_400_000
    current.access_entries = [bigquery.AccessEntry("OWNER", "userByEmail", "operator@example.com")]
    client = RecordingBigQueryClient(current)
    provisioner = BigQueryDatasetProvisioner(
        client,  # type: ignore[arg-type]
        "project",
        "us-central1",
        "gateway@example.iam",
        "processor@example.iam",
    )

    provisioner.provision(
        AllowedUser(
            "homer@example.com",
            "usr_homer",
            "tesla_u_homer",
            UserStatus.ACTIVE,
        )
    )

    assert client.current.description == "Isolated Tesla history for one approved platform user."
    assert client.current.labels == {
        "application": "tesla-personal-platform",
        "data_class": "restricted-user-telemetry",
        "managed_by": "add-user",
    }
    assert client.current.default_table_expiration_ms is None
    assert client.current.default_partition_expiration_ms is None
    assert {
        (entry.role, entry.entity_type, entry.entity_id) for entry in client.current.access_entries
    } == {
        ("READER", "userByEmail", "gateway@example.iam"),
        ("WRITER", "userByEmail", "processor@example.iam"),
    }
    assert set(client.updated_fields) == {
        "access_entries",
        "default_partition_expiration_ms",
        "default_table_expiration_ms",
        "description",
        "labels",
    }
