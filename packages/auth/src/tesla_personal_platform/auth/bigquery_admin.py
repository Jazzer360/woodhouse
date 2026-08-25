"""Per-user BigQuery dataset provisioning for the manual allowlist workflow."""

from collections.abc import Iterable

from google.cloud import bigquery
from tesla_personal_platform.analytics import analytics_views
from tesla_personal_platform.auth.models import AllowedUser
from tesla_personal_platform.shared_models import RAW_TELEMETRY_SCHEMA, RAW_TELEMETRY_TABLE

DATA_VIEWER_ACCESS_ROLE = "READER"
DATA_EDITOR_ACCESS_ROLE = "WRITER"
SERVICE_ACCOUNT_ENTITY_TYPE = "userByEmail"
DATASET_DESCRIPTION = "Isolated Tesla history for one approved platform user."
DATASET_LABELS = {
    "application": "tesla-personal-platform",
    "data_class": "restricted-user-telemetry",
    "managed_by": "add-user",
}
RAW_TABLE_LABELS = {
    "application": "tesla-personal-platform",
    "data_class": "restricted-user-telemetry",
    "managed_by": "add-user",
}


def restricted_service_account_access(
    existing: Iterable[bigquery.AccessEntry],
    owner_service_account: str,
    gateway_service_account: str,
    processor_service_account: str,
) -> list[bigquery.AccessEntry]:
    """Return the exact isolated ACL, including BigQuery's required direct owner."""
    del existing
    return [
        bigquery.AccessEntry(
            "OWNER",
            SERVICE_ACCOUNT_ENTITY_TYPE,
            owner_service_account,
        ),
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


def temporary_view_provisioning_access(
    owner_service_account: str,
    gateway_service_account: str,
    processor_service_account: str,
    admin_service_account: str,
) -> list[bigquery.AccessEntry]:
    """Add the narrow read grant BigQuery requires while validating view SQL."""
    return [
        *restricted_service_account_access(
            (),
            owner_service_account,
            gateway_service_account,
            processor_service_account,
        ),
        bigquery.AccessEntry(
            DATA_VIEWER_ACCESS_ROLE,
            SERVICE_ACCOUNT_ENTITY_TYPE,
            admin_service_account,
        ),
    ]


class BigQueryDatasetProvisioner:
    """Create or repair one opaque, non-expiring, per-user dataset."""

    def __init__(
        self,
        client: bigquery.Client,
        project_id: str,
        location: str,
        owner_service_account: str,
        gateway_service_account: str,
        processor_service_account: str,
        admin_service_account: str,
    ) -> None:
        self._client = client
        self._project_id = project_id
        self._location = location
        self._owner_service_account = owner_service_account
        self._gateway_service_account = gateway_service_account
        self._processor_service_account = processor_service_account
        self._admin_service_account = admin_service_account

    def provision(self, user: AllowedUser) -> None:
        """Idempotently enforce the dataset and append-only raw table contract."""
        dataset = bigquery.Dataset(f"{self._project_id}.{user.dataset_id}")
        dataset.location = self._location
        dataset.description = DATASET_DESCRIPTION
        dataset.default_table_expiration_ms = None
        dataset.default_partition_expiration_ms = None
        dataset.access_entries = restricted_service_account_access(
            (),
            self._owner_service_account,
            self._gateway_service_account,
            self._processor_service_account,
        )
        dataset.labels = DATASET_LABELS.copy()
        self._client.create_dataset(dataset, exists_ok=True, timeout=30)

        current = self._client.get_dataset(dataset.reference, timeout=30)
        if current.location != self._location:
            raise ValueError(
                f"Existing dataset {user.dataset_id} is in {current.location}, not {self._location}"
            )
        current.default_table_expiration_ms = None
        current.default_partition_expiration_ms = None
        current.description = DATASET_DESCRIPTION
        current.labels = DATASET_LABELS.copy()
        current.access_entries = restricted_service_account_access(
            current.access_entries,
            self._owner_service_account,
            self._gateway_service_account,
            self._processor_service_account,
        )
        self._client.update_dataset(
            current,
            [
                "access_entries",
                "default_partition_expiration_ms",
                "default_table_expiration_ms",
                "description",
                "labels",
            ],
            timeout=30,
        )
        table = bigquery.Table(
            f"{self._project_id}.{user.dataset_id}.{RAW_TELEMETRY_TABLE}",
            schema=[
                bigquery.SchemaField(
                    field.name,
                    field.field_type,
                    mode=field.mode,
                    description=field.description,
                )
                for field in RAW_TELEMETRY_SCHEMA
            ],
        )
        table.description = "Permanent append-only Fleet Telemetry observations."
        table.time_partitioning = bigquery.TimePartitioning(
            type_=bigquery.TimePartitioningType.DAY,
            field="source_timestamp",
        )
        table.clustering_fields = ["vehicle_id", "record_type"]
        table.labels = RAW_TABLE_LABELS.copy()
        table.expires = None
        self._client.create_table(table, exists_ok=True, timeout=30)

        current_table = self._client.get_table(table.reference, timeout=30)
        current_partitioning = current_table.time_partitioning
        if (
            current_partitioning is None
            or current_partitioning.field != "source_timestamp"
            or current_partitioning.type_ != bigquery.TimePartitioningType.DAY
            or current_table.clustering_fields != ["vehicle_id", "record_type"]
        ):
            raise ValueError(
                f"Existing table {user.dataset_id}.{RAW_TELEMETRY_TABLE} has an "
                "incompatible partition or clustering layout"
            )
        expected_schema = [(field.name, field.field_type, field.mode) for field in table.schema]
        actual_schema = [
            (field.name, field.field_type, field.mode) for field in current_table.schema
        ]
        if actual_schema != expected_schema:
            raise ValueError(
                f"Existing table {user.dataset_id}.{RAW_TELEMETRY_TABLE} has an "
                "incompatible schema; migrate it explicitly before ingestion"
            )
        current_table.description = table.description
        current_table.labels = RAW_TABLE_LABELS.copy()
        current_table.expires = None
        self._client.update_table(
            current_table,
            ["description", "expires", "labels"],
            timeout=30,
        )
        self._provision_analytics_views(user.dataset_id)

    def _provision_analytics_views(self, dataset_id: str) -> None:
        """Validate views with transient read, then restore the exact permanent ACL."""
        dataset_reference = bigquery.DatasetReference(self._project_id, dataset_id)
        dataset = self._client.get_dataset(dataset_reference, timeout=30)
        dataset.access_entries = temporary_view_provisioning_access(
            self._owner_service_account,
            self._gateway_service_account,
            self._processor_service_account,
            self._admin_service_account,
        )
        self._client.update_dataset(dataset, ["access_entries"], timeout=30)
        try:
            for definition in analytics_views(self._project_id, dataset_id):
                view = bigquery.Table(f"{self._project_id}.{dataset_id}.{definition.name}")
                view.description = definition.description
                view.labels = {
                    "application": "tesla-personal-platform",
                    "data_class": "restricted-user-telemetry",
                    "managed_by": "add-user",
                    "layer": "analytics",
                }
                view.view_query = definition.sql
                view.view_use_legacy_sql = False
                self._client.create_table(view, exists_ok=True, timeout=30)

                current_view = self._client.get_table(view.reference, timeout=30)
                if current_view.table_type not in {None, "VIEW"}:
                    raise ValueError(
                        f"Existing object {dataset_id}.{definition.name} is not a view"
                    )
                current_view.description = view.description
                current_view.labels = view.labels.copy()
                current_view.view_query = view.view_query
                current_view.view_use_legacy_sql = False
                self._client.update_table(
                    current_view,
                    ["description", "labels", "view_query", "view_use_legacy_sql"],
                    timeout=30,
                )
        finally:
            restored = self._client.get_dataset(dataset_reference, timeout=30)
            restored.access_entries = restricted_service_account_access(
                restored.access_entries,
                self._owner_service_account,
                self._gateway_service_account,
                self._processor_service_account,
            )
            self._client.update_dataset(restored, ["access_entries"], timeout=30)
