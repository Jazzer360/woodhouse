"""BigQuery per-user ACL construction tests."""

import pytest
from google.cloud import bigquery
from tesla_personal_platform.auth.bigquery_admin import (
    ANALYTICS_VIEW_LABELS,
    AnalyticsViewReconciler,
    BigQueryDatasetProvisioner,
    restricted_dataset_access,
    temporary_dataset_reader_access,
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
        self.deleted_table_ids: list[str] = []
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
        if table.view_query is not None:
            table._properties["type"] = "VIEW"
        for current in self.created_tables:
            if current.reference == table.reference:
                return current
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

    def list_tables(self, dataset: object, *, timeout: int) -> list[bigquery.Table]:
        del dataset, timeout
        return list(self.created_tables)

    def delete_table(
        self,
        table: object,
        *,
        not_found_ok: bool,
        timeout: int,
    ) -> None:
        del not_found_ok, timeout
        reference_text = str(table)
        self.created_tables = [
            current for current in self.created_tables if str(current.reference) != reference_text
        ]
        self.deleted_table_ids.append(reference_text.rsplit(".", 1)[-1])


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
    assert all(view.labels["managed_by"] == "analytics-view-reconciler" for view in views)
    assert all(len(view.labels["definition_hash"]) == 16 for view in views)
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


def test_temporary_view_access_preserves_acl_and_adds_only_reconciler_read() -> None:
    existing = [
        bigquery.AccessEntry("OWNER", "userByEmail", "dataset-owner@example.iam"),
        bigquery.AccessEntry("READER", "userByEmail", "homer@example.com"),
    ]
    access = temporary_dataset_reader_access(existing, "admin@example.iam")

    assert {(entry.role, entry.entity_id) for entry in access} == {
        ("OWNER", "dataset-owner@example.iam"),
        ("READER", "homer@example.com"),
        ("READER", "admin@example.iam"),
    }


def test_reconciler_removes_only_stale_labeled_views_and_restores_exact_acl() -> None:
    current = bigquery.Dataset("project.tesla_u_homer")
    current.location = "us-central1"
    current.access_entries = [
        bigquery.AccessEntry("OWNER", "userByEmail", "dataset-owner@example.iam"),
        bigquery.AccessEntry("READER", "userByEmail", "homer@example.com"),
    ]
    client = RecordingBigQueryClient(current)

    stale = bigquery.Table("project.tesla_u_homer.retired_managed_view")
    stale._properties["type"] = "VIEW"
    stale.labels = {**ANALYTICS_VIEW_LABELS, "managed_by": "add-user"}
    stale.view_query = "SELECT 1"
    unmanaged = bigquery.Table("project.tesla_u_homer.personal_view")
    unmanaged._properties["type"] = "VIEW"
    unmanaged.labels = {"managed_by": "user"}
    unmanaged.view_query = "SELECT 1"
    raw = bigquery.Table("project.tesla_u_homer.raw_telemetry_events")
    raw._properties["type"] = "TABLE"
    raw.labels = {**ANALYTICS_VIEW_LABELS}
    client.created_tables.extend((stale, unmanaged, raw))

    result = AnalyticsViewReconciler(
        client,  # type: ignore[arg-type]
        "project",
        "reconciler@example.iam",
    ).reconcile("tesla_u_homer")

    assert result.desired_view_count == 18
    assert result.removed_view_count == 1
    assert client.deleted_table_ids == ["retired_managed_view"]
    assert {table.table_id for table in client.created_tables} >= {
        "personal_view",
        "raw_telemetry_events",
        "drives",
        "charge_sessions",
    }
    assert client.dataset_access_snapshots[0] == {
        ("OWNER", "userByEmail", "dataset-owner@example.iam"),
        ("READER", "userByEmail", "homer@example.com"),
        ("READER", "userByEmail", "reconciler@example.iam"),
    }
    assert client.dataset_access_snapshots[-1] == {
        ("OWNER", "userByEmail", "dataset-owner@example.iam"),
        ("READER", "userByEmail", "homer@example.com"),
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
