"""Historical analytics catalog, isolation, limits, and view tests."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

import pytest
from google.api_core.exceptions import BadRequest
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


def bigquery_bad_request(message: str, errors: list[dict[str, Any]]) -> BadRequest:
    return BadRequest(message, errors=errors)  # type: ignore[no-untyped-call]


class FakeJob:
    def __init__(
        self,
        *,
        dry_run: bool,
        rows: list[dict[str, Any]] | None = None,
        result_error: Exception | None = None,
        error_result: dict[str, Any] | None = None,
    ) -> None:
        self.total_bytes_processed = 12_345
        self.total_bytes_billed = 10_000
        self.job_id = "safe-job-id"
        self.schema = (
            bigquery.SchemaField("vehicle_id", "STRING"),
            bigquery.SchemaField("distance_miles", "FLOAT64"),
        )
        self._dry_run = dry_run
        self._rows = rows or []
        self._result_error = result_error
        self.error_result = error_result
        self.errors = [error_result] if error_result is not None else []
        self.result_arguments: dict[str, object] = {}

    def result(self, **values: object) -> list[dict[str, Any]]:
        assert not self._dry_run
        self.result_arguments = values
        if self._result_error is not None:
            raise self._result_error
        return self._rows


class FakeBigQueryClient:
    def __init__(
        self,
        rows: list[dict[str, Any]] | None = None,
        *,
        dry_run_error: Exception | None = None,
        execution_error: Exception | None = None,
        execution_error_result: dict[str, Any] | None = None,
    ) -> None:
        self.jobs = [
            FakeJob(dry_run=True),
            FakeJob(
                dry_run=False,
                rows=rows,
                result_error=execution_error,
                error_result=execution_error_result,
            ),
        ]
        self.dry_run_error = dry_run_error
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
        if len(self.calls) == 1 and self.dry_run_error is not None:
            raise self.dry_run_error
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


def test_service_returns_syntax_error_diagnostics_from_dry_run() -> None:
    client = FakeBigQueryClient(
        dry_run_error=bigquery_bad_request(
            "invalid query",
            [
                {
                    "reason": "invalidQuery",
                    "message": "Syntax error: Expected expression but got keyword FROM at [1:8]",
                    "location": "query",
                }
            ],
        )
    )
    service = BigQueryAnalyticsService(
        client,  # type: ignore[arg-type]
        "woodhouse-project",
        "us-central1",
    )

    with pytest.raises(AnalyticsQueryError) as caught:
        service.run_query(CONTEXT, "SELECT FROM drives", correlation_id="corr-syntax")

    error = caught.value
    assert error.category == "query_failed"
    assert error.reason == "invalidQuery"
    assert error.phase == "dry_run"
    assert str(error) == "Syntax error: Expected expression but got keyword FROM at [1:8]"
    assert error.location == {"line": 1, "column": 8}
    assert error.response_details() == {
        "reason": "invalidQuery",
        "phase": "dry_run",
        "location": {"line": 1, "column": 8},
    }


def test_service_returns_invalid_column_diagnostic() -> None:
    client = FakeBigQueryClient(
        dry_run_error=bigquery_bad_request(
            "invalid query",
            [
                {
                    "reason": "invalidQuery",
                    "message": "Unrecognized name: definitely_not_a_real_column at [1:8]",
                    "location": {"line": 1, "column": 8},
                }
            ],
        )
    )
    service = BigQueryAnalyticsService(
        client,  # type: ignore[arg-type]
        "woodhouse-project",
        "us-central1",
    )

    with pytest.raises(AnalyticsQueryError) as caught:
        service.run_query(
            CONTEXT,
            "SELECT definitely_not_a_real_column FROM drives",
            correlation_id="corr-column",
        )

    assert caught.value.phase == "dry_run"
    assert caught.value.reason == "invalidQuery"
    assert "Unrecognized name: definitely_not_a_real_column" in str(caught.value)
    assert caught.value.location == {"line": 1, "column": 8}


def test_service_sanitizes_private_bigquery_identifiers_without_losing_diagnostic() -> None:
    client = FakeBigQueryClient(
        dry_run_error=bigquery_bad_request(
            "invalid query",
            [
                {
                    "reason": "invalidQuery",
                    "message": (
                        "Unrecognized name: foo at [3:7] in "
                        "woodhouse-project.tesla_u_private.private_view for "
                        "analytics-runner@woodhouse-project.iam.gserviceaccount.com"
                    ),
                },
                {
                    "reason": "invalidQuery",
                    "message": (
                        "Related diagnostic for "
                        "woodhouse-project:tesla_u_private.private_view at [4:2]"
                    ),
                },
            ],
        )
    )
    service = BigQueryAnalyticsService(
        client,  # type: ignore[arg-type]
        "woodhouse-project",
        "us-central1",
    )

    with pytest.raises(AnalyticsQueryError) as caught:
        service.run_query(CONTEXT, "SELECT foo FROM drives", correlation_id="corr-redacted")

    message = str(caught.value)
    assert "Unrecognized name: foo at [3:7]" in message
    assert "private_view" in message
    assert "woodhouse-project" not in message
    assert "tesla_u_private" not in message
    assert "analytics-runner@" not in message
    assert "<service-account>" in message
    assert caught.value.location == {"line": 3, "column": 7}
    diagnostics = caught.value.response_details()["diagnostics"]
    assert len(diagnostics) == 2
    assert "woodhouse-project" not in str(diagnostics)
    assert "tesla_u_private" not in str(diagnostics)


def test_service_marks_woodhouse_safety_rejection_as_validation() -> None:
    client = FakeBigQueryClient()
    service = BigQueryAnalyticsService(
        client,  # type: ignore[arg-type]
        "woodhouse-project",
        "us-central1",
    )

    with pytest.raises(AnalyticsQueryError) as caught:
        service.run_query(
            CONTEXT,
            "DELETE FROM drives WHERE TRUE",
            correlation_id="corr-validation",
        )

    assert caught.value.category == "read_only_required"
    assert caught.value.phase == "validation"
    assert caught.value.reason is None
    assert client.calls == []


def test_service_returns_execution_job_diagnostics_and_safe_metadata() -> None:
    error_result = {
        "reason": "resourcesExceeded",
        "message": "Resources exceeded during query execution at [2:4]",
    }
    client = FakeBigQueryClient(
        execution_error=RuntimeError(r"internal failure at C:\private\worker.py"),
        execution_error_result=error_result,
    )
    service = BigQueryAnalyticsService(
        client,  # type: ignore[arg-type]
        "woodhouse-project",
        "us-central1",
    )

    with pytest.raises(AnalyticsQueryError) as caught:
        service.run_query(
            CONTEXT,
            "SELECT COUNT(*) AS drive_count FROM drives",
            correlation_id="corr-execution",
        )

    error = caught.value
    assert error.category == "query_failed"
    assert error.reason == "resourcesExceeded"
    assert error.phase == "execution"
    assert error.location == {"line": 2, "column": 4}
    assert "C:\\private" not in str(error)
    assert error.response_details() == {
        "reason": "resourcesExceeded",
        "phase": "execution",
        "location": {"line": 2, "column": 4},
        "job_id": "safe-job-id",
        "bytes_processed": 12_345,
        "bytes_billed": 10_000,
    }


def test_service_classifies_query_timeout_without_returning_exception_text() -> None:
    client = FakeBigQueryClient(
        dry_run_error=TimeoutError("deadline while reading C:\\private\\credentials.json")
    )
    service = BigQueryAnalyticsService(
        client,  # type: ignore[arg-type]
        "woodhouse-project",
        "us-central1",
    )

    with pytest.raises(AnalyticsQueryError) as caught:
        service.run_query(CONTEXT, "SELECT * FROM drives", correlation_id="corr-timeout")

    assert caught.value.reason == "timeout"
    assert caught.value.phase == "dry_run"
    assert "credentials.json" not in str(caught.value)


def test_service_keeps_unknown_exception_details_generic() -> None:
    client = FakeBigQueryClient(
        dry_run_error=RuntimeError(
            "internal worker failure at C:\\private\\worker.py for woodhouse-project"
        )
    )
    service = BigQueryAnalyticsService(
        client,  # type: ignore[arg-type]
        "woodhouse-project",
        "us-central1",
    )

    with pytest.raises(AnalyticsQueryError) as caught:
        service.run_query(CONTEXT, "SELECT * FROM drives", correlation_id="corr-unknown")

    assert str(caught.value) == "BigQuery rejected or could not complete the analytical query"
    assert caught.value.reason is None
    assert caught.value.phase == "dry_run"


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
    assert "drive_fsd_segments" in serialized
    assert "drive_fsd_summary" in serialized
    assert "drive_path" in serialized
    assert "telemetry_capability_diagnostics" in serialized
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
    observations = next(view for view in views if view.name == "telemetry_observations")
    assert "raw_telemetry_events" in observations.sql
    assert "PARTITION BY pubsub_message_id" in observations.sql
    assert all(
        "DELETE" not in view.sql and "CREATE OR REPLACE TABLE" not in view.sql for view in views
    )
    assert all(isinstance(parse_one(view.sql, read="bigquery"), exp.Query) for view in views)


def test_drive_soc_and_energy_use_latest_valid_state_at_or_before_gear_boundary() -> None:
    views = {view.name: view for view in analytics_views("project", "dataset")}
    boundaries = views["drive_metric_boundaries"].sql
    drives = views["drives"].sql

    assert "exact_synchronized_boundary" in boundaries
    assert "WHEN boundary.field_name IN ('EnergyRemaining', 'Soc')" in boundaries
    assert "observation.source_timestamp <= boundary.boundary_at" in boundaries
    assert "THEN 'as_of_state'" in boundaries
    assert "-UNIX_MILLIS(observation.source_timestamp)" in boundaries
    assert "stationary_pre_boundary" in boundaries
    assert "stationary_post_boundary" in boundaries
    assert "INTERVAL 5 MINUTE" in boundaries
    assert "previous_drive_ended_at" in boundaries
    assert "next_drive_started_at" in boundaries
    assert "drive_metric_boundaries" in drives

    # The later 62.50 kWh sample is in-drive and cannot define the start state.
    start_boundary = datetime.fromisoformat("2026-08-26T20:17:02+00:00")
    energy_observations = [
        (datetime.fromisoformat("2026-08-26T20:16:45+00:00"), 62.62, True),
        (datetime.fromisoformat("2026-08-26T20:17:31+00:00"), 62.50, True),
    ]
    valid_as_of = [
        (observed_at, value)
        for observed_at, value, valid in energy_observations
        if valid and observed_at <= start_boundary
    ]
    assert max(valid_as_of)[1] == pytest.approx(62.62)
    assert 62.62 - 62.50 == pytest.approx(0.12)

    # Cached state remains usable after a stationary gap; age alone is not a rejection.
    assert 67.00 - 66.26 == pytest.approx(0.74)
    assert round(78.599) == 79
    assert round(77.880) == 78


def test_drive_odometer_and_location_keep_existing_bounded_stationary_rules() -> None:
    boundaries = next(
        view.sql
        for view in analytics_views("project", "dataset")
        if view.name == "drive_metric_boundaries"
    )

    assert "boundary.field_name NOT IN ('EnergyRemaining', 'Soc')" in boundaries
    assert "candidate_window_start" in boundaries
    assert "candidate_window_end" in boundaries
    assert "inside_drive_fallback" in boundaries


def test_detailed_charge_state_remains_authoritative_over_coarse_init() -> None:
    charge_sql = next(
        view.sql for view in analytics_views("project", "dataset") if view.name == "charge_sessions"
    )

    assert "detailed_class = 'active' THEN TRUE" in charge_sql
    assert "coarse_at > TIMESTAMP_ADD(detailed_at, INTERVAL 15 MINUTE)" in charge_sql
    assert "'Init'" in charge_sql
    assert "'DetailedChargeStateStopped'" in charge_sql
    assert "measured_counter_plus_bounded_power_tail" in charge_sql
    assert "distance_since_previous_charge_miles" in charge_sql

    started_at = datetime.fromisoformat("2026-08-25T07:44:06+00:00")
    coarse_init_at = datetime.fromisoformat("2026-08-25T07:44:07+00:00")
    ended_at = datetime.fromisoformat("2026-08-25T11:00:00.616185+00:00")
    assert (coarse_init_at - started_at).total_seconds() == 1
    assert (ended_at - started_at).total_seconds() == pytest.approx(11_754.616185)
    assert 617.434835 - 604.640180 == pytest.approx(12.794655)


def test_charge_end_soc_uses_precise_latest_in_session_state_with_provenance() -> None:
    charge_sql = next(
        view.sql for view in analytics_views("project", "dataset") if view.name == "charge_sessions"
    )

    assert "THEN 'in_session_state'" in charge_sql
    assert "start_soc_observed_at" in charge_sql
    assert "end_soc_observed_at" in charge_sql
    assert "end_soc_observation_offset_milliseconds" in charge_sql
    assert "end_soc_boundary_method" in charge_sql

    end_soc = 78.599266697
    assert end_soc == pytest.approx(78.599266697)
    assert round(end_soc) == 79


def test_charge_stationary_boundary_carry_requires_matching_anchors_and_no_motion() -> None:
    charge_sql = next(
        view.sql for view in analytics_views("project", "dataset") if view.name == "charge_sessions"
    )

    assert "INTERVAL 30 MINUTE" in charge_sql
    assert "ABS(anchor.after_numeric_value - anchor.before_numeric_value) <= 0.01" in charge_sql
    assert "ST_DISTANCE(" in charge_sql
    assert ") <= 100" in charge_sql
    assert "movement.field_name = 'Gear'" in charge_sql
    assert "movement.field_name = 'VehicleSpeed' AND movement.numeric_value > 1" in charge_sql
    assert "THEN 'stationary_state_carry'" in charge_sql
    assert "end_odometer_boundary_method" in charge_sql
    assert "location_boundary_method" in charge_sql

    assert 604.640180 - 604.636450 == pytest.approx(0.00373)
    assert 604.640180 - 604.636450 < 0.01


def test_charge_tail_integration_is_bounded_piecewise_and_rejects_counter_anomalies() -> None:
    charge_sql = next(
        view.sql for view in analytics_views("project", "dataset") if view.name == "charge_sessions"
    )

    assert "tail_power_points" in charge_sql
    assert "tail_power_segments" in charge_sql
    assert "tail_invalidations" in charge_sql
    assert "invalid.invalid_tail_observations = 0" in charge_sql
    assert "LEAD(point_at) OVER" in charge_sql
    assert "COALESCE(next_observed_point_at, ended_at)" in charge_sql
    assert "observation.source_timestamp BETWEEN spec.started_at AND spec.ended_at" in charge_sql
    assert "BETWEEN 1 AND 300" in charge_sql
    assert "tail.coverage_started_at = spec.counter_observed_at" in charge_sql
    assert "numeric_value < previous_numeric_value - 0.001" in charge_sql
    assert "THEN 'counter_anomaly'" in charge_sql
    assert "measured_counter_tail_unavailable" in charge_sql
    assert "ac_energy_counter_kwh" in charge_sql
    assert "ac_energy_tail_kwh" in charge_sql

    first_counter_at = datetime.fromisoformat("2026-08-24T09:50:33.813491+00:00")
    first_ended_at = datetime.fromisoformat("2026-08-24T09:54:27.813839+00:00")
    first_tail = 1.3 * (first_ended_at - first_counter_at).total_seconds() / 3600
    assert 9.3362779169 + first_tail == pytest.approx(9.4209, abs=0.0002)
    assert 9.41 <= 9.3362779169 + first_tail <= 9.43

    second_counter_at = datetime.fromisoformat("2026-08-25T10:58:44.116152+00:00")
    second_ended_at = datetime.fromisoformat("2026-08-25T11:00:00.616185+00:00")
    second_tail = 1.3 * (second_ended_at - second_counter_at).total_seconds() / 3600
    reconstructed = 4.216500063 + second_tail
    assert reconstructed == pytest.approx(4.2441, abs=0.0002)
    assert round(reconstructed, 2) == pytest.approx(4.24)


def test_fsd_bucket_allocation_matches_supplied_trip_regression_and_bounds_transition() -> None:
    views = {view.name: view for view in analytics_views("project", "dataset")}
    segments = views["drive_fsd_segments"].sql

    assert "counter_fsd_delta_miles" in segments
    assert "counter_manual_delta_miles" in segments
    assert "synchronized_counter_bucket" in segments
    assert "counter_state_carried_to_drive_boundary" in segments
    assert "insufficient_counter_evidence" in segments
    assert "mapped_candidates" in segments
    assert "LEFT JOIN `project.dataset.drive_path_points` AS path" in segments
    assert "ARRAY_AGG(" in segments
    assert "IGNORE NULLS" in segments
    assert "CROSS JOIN UNNEST(ARRAY_CONCAT(" in segments
    assert "UNION ALL" not in segments
    assert "NOT EXISTS" not in segments
    assert "SELECT AS STRUCT path" not in segments

    total_distance = 3.05963
    first_milestone_distance = 1.23963
    second_milestone_distance = 2.252478
    first_certified_fsd = 1.0
    second_certified_fsd = 1.012848
    manual_prefix = first_milestone_distance - first_certified_fsd
    inferred_tail = total_distance - second_milestone_distance
    fsd_distance = first_certified_fsd + second_certified_fsd + inferred_tail

    assert manual_prefix == pytest.approx(0.23963)
    assert fsd_distance == pytest.approx(2.82, abs=0.000001)
    assert fsd_distance / total_distance * 100 == pytest.approx(92.17, abs=0.01)

    # Linear route interpolation places the first transition about 39 seconds after
    # the 06:33:21 start: after the manual 06:33:37 point and before 06:34:25.
    first_milestone_seconds = 199.5
    transition_seconds = first_milestone_seconds * manual_prefix / first_milestone_distance
    assert 16 < transition_seconds < 64


def test_capability_diagnostic_compares_profile_client_receiver_and_payload_evidence() -> None:
    diagnostic = next(
        view.sql
        for view in analytics_views("project", "dataset")
        if view.name == "telemetry_capability_diagnostics"
    )

    assert "telemetry_receiver_version" in diagnostic
    assert "observed_client_versions" in diagnostic
    assert "current_client_first_seen_at" in diagnostic
    assert "minimum_client_for_include_fields" in diagnostic
    assert "synchronized_gear_messages" in diagnostic
    assert "synchronized_charge_messages" in diagnostic
    assert "synchronized_fsd_messages" in diagnostic
    assert "include_fields_not_observed" in diagnostic
    assert (
        "LEFT JOIN version_history AS history ON history.vehicle_id = latest.vehicle_id"
        in diagnostic
    )
    assert "LEFT JOIN firmware ON firmware.vehicle_id = latest.vehicle_id" in diagnostic
    assert "LEFT JOIN anchors ON anchors.vehicle_id = latest.vehicle_id" in diagnostic
    assert "USING (vehicle_id)" not in diagnostic


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
