"""Per-user BigQuery provisioning and managed analytics-view reconciliation."""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from uuid import uuid4

from google.api_core.exceptions import NotFound
from google.cloud import bigquery
from tesla_personal_platform.analytics import AnalyticsView, analytics_views
from tesla_personal_platform.auth.admin import (
    AnalyticsViewReconciliation,
    AnalyticsViewReconciliationError,
)
from tesla_personal_platform.auth.models import AllowedUser
from tesla_personal_platform.shared_models import RAW_TELEMETRY_SCHEMA, RAW_TELEMETRY_TABLE

DATA_VIEWER_ACCESS_ROLE = "READER"
DATA_EDITOR_ACCESS_ROLE = "WRITER"
USER_BY_EMAIL_ENTITY_TYPE = "userByEmail"
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
ANALYTICS_VIEW_LABELS = {
    "application": "tesla-personal-platform",
    "data_class": "restricted-user-telemetry",
    "managed_by": "analytics-view-reconciler",
    "layer": "analytics",
}
LEGACY_ANALYTICS_VIEW_MANAGERS = frozenset({"add-user", "analytics-view-reconciler"})
ANALYTICS_VIEW_VALIDATION_TIMEOUT_SECONDS = 120
ANALYTICS_PREFLIGHT_TTL = timedelta(hours=1)


@dataclass(frozen=True, slots=True)
class _ViewSnapshot:
    reference: bigquery.TableReference
    description: str | None
    labels: dict[str, str]
    view_query: str | None
    view_use_legacy_sql: bool


def restricted_dataset_access(
    existing: Iterable[bigquery.AccessEntry],
    owner_service_account: str,
    gateway_service_account: str,
    processor_service_account: str,
    approved_user_email: str,
) -> list[bigquery.AccessEntry]:
    """Return the exact isolated ACL for one approved user's dataset."""
    del existing
    return [
        bigquery.AccessEntry(
            "OWNER",
            USER_BY_EMAIL_ENTITY_TYPE,
            owner_service_account,
        ),
        bigquery.AccessEntry(
            DATA_VIEWER_ACCESS_ROLE,
            USER_BY_EMAIL_ENTITY_TYPE,
            gateway_service_account,
        ),
        bigquery.AccessEntry(
            DATA_EDITOR_ACCESS_ROLE,
            USER_BY_EMAIL_ENTITY_TYPE,
            processor_service_account,
        ),
        bigquery.AccessEntry(
            DATA_VIEWER_ACCESS_ROLE,
            USER_BY_EMAIL_ENTITY_TYPE,
            approved_user_email,
        ),
    ]


def temporary_dataset_reader_access(
    existing: Iterable[bigquery.AccessEntry],
    reconciler_service_account: str,
) -> list[bigquery.AccessEntry]:
    """Temporarily add one reader without changing any permanent dataset ACL entry."""
    entries = list(existing)
    reader = bigquery.AccessEntry(
        DATA_VIEWER_ACCESS_ROLE,
        USER_BY_EMAIL_ENTITY_TYPE,
        reconciler_service_account,
    )
    if reader not in entries:
        entries.append(reader)
    return entries


def _view_labels(description: str, sql: str) -> dict[str, str]:
    labels = ANALYTICS_VIEW_LABELS.copy()
    labels["definition_hash"] = sha256(f"{description}\0{sql}".encode()).hexdigest()[:16]
    return labels


def _is_managed_analytics_view(table: bigquery.Table) -> bool:
    labels = table.labels or {}
    return (
        table.table_type == "VIEW"
        and labels.get("application") == ANALYTICS_VIEW_LABELS["application"]
        and labels.get("data_class") == ANALYTICS_VIEW_LABELS["data_class"]
        and labels.get("layer") == ANALYTICS_VIEW_LABELS["layer"]
        and labels.get("managed_by") in LEGACY_ANALYTICS_VIEW_MANAGERS
    )


def _preflight_labels(description: str, sql: str) -> dict[str, str]:
    labels = _view_labels(description, sql)
    labels["managed_by"] = "analytics-view-preflight"
    return labels


class AnalyticsViewReconciler:
    """Make one private dataset's managed views exactly match source definitions."""

    def __init__(
        self,
        client: bigquery.Client,
        project_id: str,
        reconciler_service_account: str,
        *,
        remove_stale_managed_views: bool = True,
    ) -> None:
        self._client = client
        self._project_id = project_id
        self._reconciler_service_account = reconciler_service_account
        self._remove_stale_managed_views = remove_stale_managed_views

    def reconcile(self, dataset_id: str) -> AnalyticsViewReconciliation:
        """Update desired views and remove only stale Woodhouse-managed views."""
        dataset_reference = bigquery.DatasetReference(self._project_id, dataset_id)
        dataset = self._client.get_dataset(dataset_reference, timeout=30)
        permanent_access = list(dataset.access_entries)
        dataset.access_entries = temporary_dataset_reader_access(
            permanent_access,
            self._reconciler_service_account,
        )
        self._client.update_dataset(dataset, ["access_entries"], timeout=30)
        try:
            definitions = analytics_views(self._project_id, dataset_id)
            self._preflight(dataset_id)
            desired_names = {definition.name for definition in definitions}
            self._promote(dataset_id, definitions)

            removed = 0
            if self._remove_stale_managed_views:
                for table_item in self._client.list_tables(dataset_reference, timeout=30):
                    if table_item.table_id in desired_names:
                        continue
                    current_table = self._client.get_table(table_item.reference, timeout=30)
                    if not _is_managed_analytics_view(current_table):
                        continue
                    self._client.delete_table(
                        current_table.reference,
                        not_found_ok=True,
                        timeout=30,
                    )
                    removed += 1
                managed_names = set()
                for table_item in self._client.list_tables(dataset_reference, timeout=30):
                    current_table = self._client.get_table(table_item.reference, timeout=30)
                    if _is_managed_analytics_view(current_table):
                        managed_names.add(current_table.table_id)
                if managed_names != desired_names:
                    raise RuntimeError("Managed analytics view postcondition failed")
            return AnalyticsViewReconciliation(len(definitions), removed)
        finally:
            restored = self._client.get_dataset(dataset_reference, timeout=30)
            restored.access_entries = permanent_access
            self._client.update_dataset(restored, ["access_entries"], timeout=30)

    def _preflight(self, dataset_id: str) -> None:
        """Validate the complete candidate graph before changing canonical views."""
        prefix = f"tpp_preflight_{uuid4().hex[:12]}_"
        definitions = analytics_views(
            self._project_id,
            dataset_id,
            dependency_prefix=prefix,
        )
        created: list[bigquery.TableReference] = []
        failure: tuple[str, Exception] | None = None
        expires = datetime.now(UTC) + ANALYTICS_PREFLIGHT_TTL
        for definition in definitions:
            try:
                view = bigquery.Table(f"{self._project_id}.{dataset_id}.{prefix}{definition.name}")
                view.description = f"Short-lived validation shadow for {definition.name}."
                view.labels = _preflight_labels(definition.description, definition.sql)
                view.view_query = definition.sql
                view.view_use_legacy_sql = False
                view.expires = expires
                created_view = self._client.create_table(
                    view,
                    exists_ok=False,
                    timeout=ANALYTICS_VIEW_VALIDATION_TIMEOUT_SECONDS,
                )
                created.append(created_view.reference)
            except Exception as error:
                failure = (definition.name, error)
                break

        cleanup_error: Exception | None = None
        for reference in reversed(created):
            try:
                self._client.delete_table(reference, not_found_ok=True, timeout=30)
            except Exception as error:
                cleanup_error = cleanup_error or error

        if cleanup_error is not None:
            raise AnalyticsViewReconciliationError(
                "preflight cleanup", "shadow graph"
            ) from cleanup_error
        if failure is not None:
            failed_view_name, cause = failure
            raise AnalyticsViewReconciliationError("preflight", failed_view_name) from cause

    def _promote(
        self,
        dataset_id: str,
        definitions: tuple[AnalyticsView, ...],
    ) -> None:
        """Promote a validated graph and reverse prior changes if promotion fails."""
        changes: list[tuple[bigquery.TableReference, _ViewSnapshot | None]] = []
        active_view_name = "unknown"
        try:
            for definition in definitions:
                active_view_name = definition.name
                desired = bigquery.Table(f"{self._project_id}.{dataset_id}.{definition.name}")
                desired.description = definition.description
                desired.labels = _view_labels(definition.description, definition.sql)
                desired.view_query = definition.sql
                desired.view_use_legacy_sql = False
                try:
                    current = self._client.get_table(desired.reference, timeout=30)
                except NotFound:
                    self._client.create_table(
                        desired,
                        exists_ok=False,
                        timeout=ANALYTICS_VIEW_VALIDATION_TIMEOUT_SECONDS,
                    )
                    changes.append((desired.reference, None))
                    current = self._client.get_table(desired.reference, timeout=30)
                else:
                    if current.table_type not in {None, "VIEW"}:
                        raise ValueError("Existing canonical analytics object is not a view")
                    changes.append(
                        (
                            current.reference,
                            _ViewSnapshot(
                                current.reference,
                                current.description,
                                dict(current.labels or {}),
                                current.view_query,
                                current.view_use_legacy_sql,
                            ),
                        )
                    )

                current.description = desired.description
                current.labels = desired.labels.copy()
                current.view_query = desired.view_query
                current.view_use_legacy_sql = False
                self._client.update_table(
                    current,
                    ["description", "labels", "view_query", "view_use_legacy_sql"],
                    timeout=ANALYTICS_VIEW_VALIDATION_TIMEOUT_SECONDS,
                )
        except Exception as cause:
            rollback_cause = self._rollback_promotion(changes)
            if rollback_cause is not None:
                raise AnalyticsViewReconciliationError(
                    "promotion rollback", active_view_name
                ) from rollback_cause
            raise AnalyticsViewReconciliationError("promotion", active_view_name) from cause

    def _rollback_promotion(
        self,
        changes: list[tuple[bigquery.TableReference, _ViewSnapshot | None]],
    ) -> Exception | None:
        """Best-effort exact reversal of canonical changes in dependency-safe order."""
        first_error: Exception | None = None
        for reference, snapshot in reversed(changes):
            try:
                if snapshot is None:
                    self._client.delete_table(reference, not_found_ok=True, timeout=30)
                    continue
                current = self._client.get_table(reference, timeout=30)
                current.description = snapshot.description
                current.labels = snapshot.labels.copy()
                current.view_query = snapshot.view_query
                current.view_use_legacy_sql = snapshot.view_use_legacy_sql
                self._client.update_table(
                    current,
                    ["description", "labels", "view_query", "view_use_legacy_sql"],
                    timeout=ANALYTICS_VIEW_VALIDATION_TIMEOUT_SECONDS,
                )
            except Exception as rollback_error:
                first_error = first_error or rollback_error
        return first_error


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
        self._view_reconciler = AnalyticsViewReconciler(
            client,
            project_id,
            admin_service_account,
            remove_stale_managed_views=False,
        )

    def provision(self, user: AllowedUser) -> None:
        """Idempotently enforce the dataset and append-only raw table contract."""
        dataset = bigquery.Dataset(f"{self._project_id}.{user.dataset_id}")
        dataset.location = self._location
        dataset.description = DATASET_DESCRIPTION
        dataset.default_table_expiration_ms = None
        dataset.default_partition_expiration_ms = None
        dataset.access_entries = restricted_dataset_access(
            (),
            self._owner_service_account,
            self._gateway_service_account,
            self._processor_service_account,
            user.invitation_email,
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
        current.access_entries = restricted_dataset_access(
            current.access_entries,
            self._owner_service_account,
            self._gateway_service_account,
            self._processor_service_account,
            user.invitation_email,
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
        self._view_reconciler.reconcile(user.dataset_id)
