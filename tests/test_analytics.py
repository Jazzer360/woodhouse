"""Historical analytics catalog, isolation, limits, and view tests."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

import pytest
from google.cloud import bigquery
from sqlglot import exp, parse_one
from tesla_personal_platform.analytics import (
    ALLOWED_ANALYTICS_OBJECTS,
    MAX_RESULT_BYTES,
    MAX_RESULT_ROWS,
    AnalyticsQueryError,
    BigQueryAnalyticsService,
    analytics_views,
    validate_analytics_query,
)
from tesla_personal_platform.analytics.telemetry_fields import (
    category_sample_specs,
    telemetry_catalog_entries,
)
from tesla_personal_platform.auth import UserContext

CONTEXT = UserContext("usr_private", "tesla_u_private", "issuer", "subject")


class FakeJob:
    def __init__(self, *, dry_run: bool, rows: list[dict[str, Any]] | None = None) -> None:
        self.total_bytes_processed = 12_345
        self.total_bytes_billed = 10_000
        self.job_id = "safe-job-id"
        self.schema = (
            bigquery.SchemaField("vehicle_id", "STRING"),
            bigquery.SchemaField("distance_miles", "FLOAT64"),
        )
        self._dry_run = dry_run
        self._rows = rows or []
        self.result_arguments: dict[str, object] = {}

    def result(self, **values: object) -> list[dict[str, Any]]:
        assert not self._dry_run
        self.result_arguments = values
        return self._rows


class FakeBigQueryClient:
    def __init__(self, rows: list[dict[str, Any]] | None = None) -> None:
        self.jobs = [FakeJob(dry_run=True), FakeJob(dry_run=False, rows=rows)]
        self.calls: list[tuple[str, bigquery.QueryJobConfig, str, float]] = []

    def list_tables(
        self,
        dataset: bigquery.DatasetReference,
        *,
        max_results: int,
    ) -> list[object]:
        assert str(dataset) == "woodhouse-project.tesla_u_private"
        assert max_results == 100
        return [type("TableItem", (), {"table_id": name})() for name in ALLOWED_ANALYTICS_OBJECTS]

    def query(
        self,
        sql: str,
        *,
        job_config: bigquery.QueryJobConfig,
        location: str,
        timeout: float,
    ) -> FakeJob:
        self.calls.append((sql, job_config, location, timeout))
        return self.jobs[len(self.calls) - 1]


@pytest.mark.parametrize(
    "sql,category",
    [
        ("DELETE FROM drives WHERE TRUE", "read_only_required"),
        ("CREATE TABLE stolen AS SELECT 1", "read_only_required"),
        ("SELECT 1; SELECT 2", "invalid_sql"),
        ("EXPORT DATA OPTIONS(uri='gs://bucket/file') AS SELECT 1", "read_only_required"),
        ("SELECT * FROM other_project.other_dataset.drives", "dataset_boundary"),
        ("SELECT * FROM another_table", "object_not_allowed"),
        ("SELECT EXTERNAL_QUERY('connection', 'SELECT 1')", "unsupported_function"),
        ("SELECT private_remote_function(vehicle_id) FROM drives", "unsupported_function"),
    ],
)
def test_query_validator_rejects_escape_paths(sql: str, category: str) -> None:
    with pytest.raises(AnalyticsQueryError) as caught:
        validate_analytics_query(sql)

    assert caught.value.category == category


def test_query_validator_allows_ctes_multiple_vehicles_and_safe_geography() -> None:
    validated = validate_analytics_query(
        """
        WITH recent AS (
          SELECT vehicle_id, distance_miles, start_latitude, start_longitude
          FROM drives
          WHERE started_at >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 30 DAY)
        )
        SELECT vehicle_id, SUM(distance_miles) AS miles,
          ST_GEOGPOINT(ANY_VALUE(start_longitude), ANY_VALUE(start_latitude)) AS first_place
        FROM recent
        GROUP BY vehicle_id
        ORDER BY miles DESC
        """
    )

    assert validated.referenced_objects == frozenset({"drives"})
    assert "recent" not in validated.referenced_objects
    assert "ST_GEOGPOINT" in validated.sql


def test_playlist_query_needs_no_dedicated_endpoint() -> None:
    validated = validate_analytics_query(
        """
        SELECT d.drive_id, d.vehicle_id, m.started_at, m.title, m.artist, m.album
        FROM drives AS d
        JOIN media_history AS m
          ON m.vehicle_id = d.vehicle_id
          AND m.started_at < COALESCE(d.ended_at, CURRENT_TIMESTAMP())
          AND COALESCE(m.ended_at, CURRENT_TIMESTAMP()) > d.started_at
        WHERE d.started_at >= TIMESTAMP('2026-08-01')
        ORDER BY d.started_at, m.started_at
        """
    )

    assert validated.referenced_objects == frozenset({"drives", "media_history"})


def test_field_catalog_and_dashboard_samples_are_queryable() -> None:
    catalog_query = validate_analytics_query(
        "SELECT category, field_name, interval_seconds FROM telemetry_field_catalog "
        "WHERE configured ORDER BY category, field_name"
    )
    graph_query = validate_analytics_query(
        "SELECT source_timestamp, vehicle_speed, longitudinal_acceleration "
        "FROM driving_samples WHERE source_timestamp >= TIMESTAMP('2026-08-01')"
    )

    assert catalog_query.referenced_objects == frozenset({"telemetry_field_catalog"})
    assert graph_query.referenced_objects == frozenset({"driving_samples"})


def test_service_derives_default_dataset_and_bounds_results(
    caplog: pytest.LogCaptureFixture,
) -> None:
    secret_row = {
        "vehicle_id": "veh_private",
        "distance_miles": 12.5,
        "observed_at": datetime(2026, 8, 24, tzinfo=UTC),
    }
    client = FakeBigQueryClient([secret_row] * (MAX_RESULT_ROWS + 1))
    service = BigQueryAnalyticsService(
        client,  # type: ignore[arg-type]
        "woodhouse-project",
        "us-central1",
    )

    with caplog.at_level(logging.INFO):
        result = service.run_query(
            CONTEXT,
            "SELECT vehicle_id, distance_miles FROM drives",
            correlation_id="corr-safe",
        )

    assert result["row_count"] == MAX_RESULT_ROWS
    assert result["truncated"] is True
    assert result["rows"][0]["observed_at"] == "2026-08-24T00:00:00+00:00"
    assert result["bytes_processed"] == 12_345
    assert len(client.calls) == 2
    dry_sql, dry_config, location, _ = client.calls[0]
    execute_sql, execute_config, _, _ = client.calls[1]
    assert execute_sql == dry_sql
    assert dry_config.dry_run is True
    assert execute_config.dry_run is False
    assert str(dry_config.default_dataset) == "woodhouse-project.tesla_u_private"
    assert dry_config.maximum_bytes_billed == 1_073_741_824
    assert location == "us-central1"
    assert client.jobs[1].result_arguments["max_results"] == MAX_RESULT_ROWS + 1
    assert "veh_private" not in caplog.text
    assert "SELECT vehicle_id" not in caplog.text


def test_service_returns_more_than_legacy_200_row_limit() -> None:
    rows = [{"vehicle_id": "veh_private", "distance_miles": float(index)} for index in range(250)]
    service = BigQueryAnalyticsService(
        FakeBigQueryClient(rows),  # type: ignore[arg-type]
        "woodhouse-project",
        "us-central1",
    )

    result = service.run_query(
        CONTEXT,
        "SELECT vehicle_id, distance_miles FROM drives",
        correlation_id="corr-deep-analysis",
    )

    assert result["row_count"] == 250
    assert result["truncated"] is False


def test_schema_is_descriptive_without_physical_namespace() -> None:
    service = BigQueryAnalyticsService(
        FakeBigQueryClient(),  # type: ignore[arg-type]
        "woodhouse-project",
        "us-central1",
    )

    result = service.get_schema(CONTEXT, correlation_id="corr-schema")
    serialized = str(result)

    assert "telemetry_observations" in serialized
    assert "telemetry_field_catalog" in serialized
    assert "driving_samples" in serialized
    assert "media_history" in serialized
    assert "join_keys" in serialized
    assert "partition_hint" in serialized
    assert result["unavailable_catalog_objects"] == []
    assert result["query_limits"]["maximum_result_rows"] == 1_000
    assert result["query_limits"]["maximum_result_bytes"] == 1_048_576
    assert MAX_RESULT_ROWS == 1_000
    assert MAX_RESULT_BYTES == 1_048_576
    assert "tesla_u_private" not in serialized
    assert "woodhouse-project" not in serialized


def test_derived_views_are_ordered_complete_and_raw_preserving() -> None:
    views = analytics_views("woodhouse-502615", "tesla_u_private")

    assert [view.name for view in views] == [
        "telemetry_field_catalog",
        "telemetry_observations",
        "charging_samples",
        "climate_samples",
        "driving_samples",
        "location_samples",
        "media_samples",
        "vehicle_state_changes",
        "drives",
        "charge_sessions",
        "media_history",
        "daily_vehicle_summary",
    ]
    observations = next(view for view in views if view.name == "telemetry_observations")
    assert "raw_telemetry_events" in observations.sql
    assert "PARTITION BY pubsub_message_id" in observations.sql
    assert all(
        "DELETE" not in view.sql and "CREATE OR REPLACE TABLE" not in view.sql for view in views
    )
    assert all(isinstance(parse_one(view.sql, read="bigquery"), exp.Query) for view in views)


def test_field_catalog_is_complete_and_matches_the_reviewed_profile() -> None:
    entries = telemetry_catalog_entries()
    configured = [entry for entry in entries if entry.configured]

    assert len(entries) == 239
    assert len(configured) == 131
    assert {entry.category for entry in entries} == {
        "Charging",
        "Climate",
        "Driving",
        "Location",
        "Media",
        "Powertrain",
        "Safety",
        "Service",
        "User Preference",
        "Vehicle Configuration",
        "Vehicle State",
    }
    speed = next(entry for entry in entries if entry.field_name == "VehicleSpeed")
    battery_level = next(entry for entry in entries if entry.field_name == "BatteryLevel")
    assert (speed.interval_seconds, speed.minimum_delta) == (1, 1.0)
    assert speed.target_client_version == "1.3.0"
    assert battery_level.configured is False
    assert battery_level.exclusion_reason is not None


def test_dashboard_samples_use_exact_sparse_emissions_and_typed_columns() -> None:
    specs = {spec.view_name: spec for spec in category_sample_specs()}

    assert set(specs) == {
        "charging_samples",
        "climate_samples",
        "driving_samples",
        "location_samples",
        "media_samples",
    }
    driving = {column.name: column for column in specs["driving_samples"].columns}
    location = {column.name: column for column in specs["location_samples"].columns}
    assert driving["vehicle_speed"].field_type == "FLOAT64"
    assert driving["brake_pedal"].field_type == "BOOLEAN"
    assert location["location_latitude"].field_name == "Location"
    assert location["location_longitude"].field_name == "Location"

    views = {view.name: view for view in analytics_views("project", "dataset")}
    assert (
        "GROUP BY source_timestamp, vehicle_id, pubsub_message_id" in views["driving_samples"].sql
    )
    assert "TIMESTAMP_BUCKET" not in views["driving_samples"].sql
    assert "invalid_fields" in views["driving_samples"].sql
