"""BigQuery per-user ACL construction tests."""

import pytest
from google.cloud import bigquery
from tesla_personal_platform.auth.bigquery_admin import (
    BigQueryDatasetProvisioner,
    restricted_dataset_access,
    temporary_view_provisioning_access,
)
from tesla_personal_platform.auth.models import AllowedUser, UserStatus


def test_dataset_access_is_exact_minimal_and_idempotent() -> None:
    existing = [bigquery.AccessEntry("OWNER", "userByEmail", "operator@example.com")]

    once = restricted_dataset_access(
        existing,
        "dataset-owner@example.iam",
        "gateway@example.iam",
        "processor@example.iam",
        "homer@example.com",
    )
    twice = restricted_dataset_access(
        once,
        "dataset-owner@example.iam",
        "gateway@example.iam",
        "processor@example.iam",
        "homer@example.com",
    )

    assert twice == once
    assert {(entry.role, entry.entity_type, entry.entity_id) for entry in twice} == {
        ("OWNER", "userByEmail", "dataset-owner@example.iam"),
        ("READER", "userByEmail", "gateway@example.iam"),
        ("WRITER", "userByEmail", "processor@example.iam"),
        ("READER", "userByEmail", "homer@example.com"),
    }


class RecordingBigQueryClient:
    def __init__(self, current: bigquery.Dataset, *, fail_view_creation: bool = False) -> None:
        self.current = current
        self.updated_dataset_fields: list[list[str]] = []
        self.dataset_access_snapshots: list[set[tuple[str, str, str]]] = []
        self.created_tables: list[bigquery.Table] = []
        self.updated_table_fields: list[list[str]] = []
        self.fail_view_creation = fail_view_creation

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
        self.updated_dataset_fields.append(fields)
        self.dataset_access_snapshots.append(
            {(entry.role, entry.entity_type, entry.entity_id) for entry in dataset.access_entries}
        )
        return dataset

    def create_table(
        self,
        table: bigquery.Table,
        *,
        exists_ok: bool,
        timeout: int,
    ) -> bigquery.Table:
        del exists_ok, timeout
        if self.fail_view_creation and table.view_query is not None:
            raise RuntimeError("view validation failed")
        self.created_tables.append(table)
        return table

    def get_table(self, reference: object, *, timeout: int) -> bigquery.Table:
        del timeout
        reference_text = str(reference)
        for table in self.created_tables:
            if str(table.reference) == reference_text:
                return table
        raise AssertionError(f"Unexpected table reference: {reference_text}")

    def update_table(
        self,
        table: bigquery.Table,
        fields: list[str],
        *,
        timeout: int,
    ) -> bigquery.Table:
        del timeout
        self.updated_table_fields.append(fields)
        return table


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
        "dataset-owner@example.iam",
        "gateway@example.iam",
        "processor@example.iam",
        "admin@example.iam",
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
        ("OWNER", "userByEmail", "dataset-owner@example.iam"),
        ("READER", "userByEmail", "gateway@example.iam"),
        ("WRITER", "userByEmail", "processor@example.iam"),
        ("READER", "userByEmail", "homer@example.com"),
    }
    assert set(client.updated_dataset_fields[0]) == {
        "access_entries",
        "default_partition_expiration_ms",
        "default_table_expiration_ms",
        "description",
        "labels",
    }
    table = client.created_tables[0]
    assert table.table_id == "raw_telemetry_events"
    assert table.expires is None
    assert table.time_partitioning.field == "source_timestamp"
    assert table.clustering_fields == ["vehicle_id", "record_type"]
    assert {field.name for field in table.schema} >= {
        "source_timestamp",
        "ingested_at",
        "vehicle_id",
        "record_type",
        "payload",
        "pubsub_message_id",
    }
    assert set(client.updated_table_fields[0]) == {
        "description",
        "expires",
        "labels",
    }
    views = client.created_tables[1:]
    assert [view.table_id for view in views] == [
        "telemetry_field_catalog",
        "telemetry_observations",
        "charging_samples",
        "climate_samples",
        "driving_samples",
        "location_samples",
        "media_samples",
        "vehicle_state_changes",
        "drive_metric_boundaries",
        "drives",
        "charge_sessions",
        "drive_path_points",
        "drive_fsd_segments",
        "drive_fsd_summary",
        "drive_path",
        "telemetry_capability_diagnostics",
        "media_history",
        "daily_vehicle_summary",
    ]
    assert all(view.view_query for view in views)
    assert all(view.view_use_legacy_sql is False for view in views)
    assert all(view.labels["layer"] == "analytics" for view in views)
    assert all(
        set(fields) == {"description", "labels", "view_query", "view_use_legacy_sql"}
        for fields in client.updated_table_fields[1:]
    )
    assert client.dataset_access_snapshots[-2] == {
        ("OWNER", "userByEmail", "dataset-owner@example.iam"),
        ("READER", "userByEmail", "gateway@example.iam"),
        ("WRITER", "userByEmail", "processor@example.iam"),
        ("READER", "userByEmail", "homer@example.com"),
        ("READER", "userByEmail", "admin@example.iam"),
    }
    assert client.dataset_access_snapshots[-1] == {
        ("OWNER", "userByEmail", "dataset-owner@example.iam"),
        ("READER", "userByEmail", "gateway@example.iam"),
        ("WRITER", "userByEmail", "processor@example.iam"),
        ("READER", "userByEmail", "homer@example.com"),
    }


def test_view_validation_failure_restores_permanent_dataset_acl() -> None:
    current = bigquery.Dataset("project.tesla_u_homer")
    current.location = "us-central1"
    current.access_entries = []
    client = RecordingBigQueryClient(current, fail_view_creation=True)
    provisioner = BigQueryDatasetProvisioner(
        client,  # type: ignore[arg-type]
        "project",
        "us-central1",
        "dataset-owner@example.iam",
        "gateway@example.iam",
        "processor@example.iam",
        "admin@example.iam",
    )

    with pytest.raises(RuntimeError, match="view validation failed"):
        provisioner.provision(
            AllowedUser(
                "homer@example.com",
                "usr_homer",
                "tesla_u_homer",
                UserStatus.ACTIVE,
            )
        )

    assert client.dataset_access_snapshots[-1] == {
        ("OWNER", "userByEmail", "dataset-owner@example.iam"),
        ("READER", "userByEmail", "gateway@example.iam"),
        ("WRITER", "userByEmail", "processor@example.iam"),
        ("READER", "userByEmail", "homer@example.com"),
    }


def test_temporary_view_access_adds_only_admin_read() -> None:
    access = temporary_view_provisioning_access(
        "dataset-owner@example.iam",
        "gateway@example.iam",
        "processor@example.iam",
        "homer@example.com",
        "admin@example.iam",
    )

    assert {(entry.role, entry.entity_id) for entry in access} == {
        ("OWNER", "dataset-owner@example.iam"),
        ("READER", "gateway@example.iam"),
        ("WRITER", "processor@example.iam"),
        ("READER", "homer@example.com"),
        ("READER", "admin@example.iam"),
    }


def test_approved_user_access_is_scoped_to_that_users_dataset() -> None:
    homer_access = restricted_dataset_access(
        (),
        "dataset-owner@example.iam",
        "gateway@example.iam",
        "processor@example.iam",
        "homer@example.com",
    )

    identities = {entry.entity_id for entry in homer_access}
    assert "homer@example.com" in identities
    assert "marge@example.com" not in identities
