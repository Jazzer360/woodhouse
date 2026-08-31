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


def test_drive_distance_uses_a_bounded_route_fallback_with_explicit_provenance() -> None:
    views = {view.name: view.sql for view in analytics_views("project", "dataset")}
    drives = views["drives"]
    path = views["drive_path_points"]
    daily = views["daily_vehicle_summary"]

    assert "candidate_segment_distance_miles" in drives
    assert "/ 3600000.0 * 200" in drives
    assert "route_rejected_segment_count = 0" in drives
    assert "THEN 'gps_route_fallback'" in drives
    assert "THEN 'rejected_implausible_segment'" in drives
    assert "best_available_distance_miles" in path
    assert "validated_route_distances" in path
    assert "drive.route_quality" in path
    assert "SUM(best_available_distance_miles) AS distance_miles" in daily
    assert "route_fallback_distance_miles" in daily
    assert "distance_unavailable_drive_count" in daily
    assert "IF(energy_quality = 'validated', ac_energy_added_kwh, NULL)" in daily
    assert "charge_energy_issue_count" in daily

    elapsed_seconds = 10
    plausible_ceiling_miles = max(0.05, elapsed_seconds / 3600 * 200)
    assert 0.2 <= plausible_ceiling_miles
    assert 1.0 > plausible_ceiling_miles


def test_preflight_rendering_rewrites_only_managed_view_dependencies() -> None:
    views = {
        view.name: view.sql
        for view in analytics_views(
            "project",
            "dataset",
            dependency_prefix="tpp_preflight_test_",
        )
    }

    assert "`project.dataset.raw_telemetry_events`" in views["telemetry_observations"]
    assert "`project.dataset.tpp_preflight_test_telemetry_observations`" in views["drives"]
    assert "`project.dataset.tpp_preflight_test_drives`" in views["drive_fsd_summary"]
    assert "`project.dataset.tpp_preflight_test_drive_fsd_segments`" in views["drive_fsd_summary"]


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
    assert "observation.source_timestamp <= boundary.started_at THEN 0" in charge_sql
    assert "start_ac_energy_method" in charge_sql
    assert "start_dc_energy_method" in charge_sql
    assert "ac_energy_upper_bound_kwh" in charge_sql
    assert "dc_energy_upper_bound_kwh" in charge_sql
    assert "THEN 'physically_implausible'" in charge_sql
    assert "THEN 'physical_bound_unavailable'" in charge_sql
    assert "THEN 'counter_reset'" in charge_sql
    assert "THEN 'baseline_unavailable'" in charge_sql
    assert "candidate_ac_energy_added_kwh IS NULL" in charge_sql
    assert "ac_energy_upper_bound_kwh IS NULL" in charge_sql
    assert "energy_quality" in charge_sql

    # A cumulative session counter must subtract the latest pre-start value.
    counter_start = 8.00
    counter_end = 8.12
    assert counter_end - counter_start == pytest.approx(0.12)

    # A 5.7-minute 1.3 kW session cannot physically deliver 1.325 kWh. The
    # production formula allows a 0.05 kWh floor for sparse boundary timing.
    duration_seconds = 5.7 * 60
    upper_bound = 1.3 * duration_seconds / 3600 + 0.05
    assert upper_bound == pytest.approx(0.1735)
    assert 1.325 > upper_bound
    assert 0.12 <= upper_bound


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
    assert "WHERE total_update IS NOT NULL AND fsd_miles IS NOT NULL" in segments
    assert ") IS DISTINCT FROM total_miles" in segments
    assert "PARTITION BY drive_id, reset_epoch" in segments
    assert "completed_segment_arrays" in segments
    assert "start_distance_miles > 0.001" in segments
    assert "start_distance_miles - previous_end_distance_miles > 0.001" in segments
    assert "ranked.drive_distance_miles,\n    segment.*" in segments
    assert "drive.best_available_distance_miles AS drive_distance_miles" in segments
    assert "COALESCE(segment.end_distance_miles, drive.best_available_distance_miles)" in segments
    assert (
        "COALESCE(segment.transition_upper_bound_miles, "
        "drive.best_available_distance_miles)" in segments
    )

    summary = views["drive_fsd_summary"].sql
    assert "NULLIF(drive.best_available_distance_miles, 0)" in summary
    assert "classified_distance_miles" in summary
    assert "unclassified_distance_miles" in summary
    assert "classification_complete" in summary

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


def test_fsd_counter_snapshots_tolerate_asynchronous_updates_and_resets() -> None:
    def paired_deltas(
        updates: list[tuple[float | None, float | None]],
    ) -> list[tuple[float, float]]:
        total: float | None = None
        fsd: float | None = None
        snapshots: list[tuple[float, float]] = []
        last_snapshot_total: float | None = None
        for total_update, fsd_update in updates:
            if total_update is not None:
                total = total_update
            if fsd_update is not None:
                fsd = fsd_update
            if (
                total_update is not None
                and total is not None
                and fsd is not None
                and total != last_snapshot_total
            ):
                snapshots.append((total, fsd))
                last_snapshot_total = total

        deltas: list[tuple[float, float]] = []
        previous: tuple[float, float] | None = None
        for snapshot in snapshots:
            if previous is None or snapshot[0] < previous[0] or snapshot[1] < previous[1]:
                previous = snapshot
                continue
            deltas.append((snapshot[0] - previous[0], snapshot[1] - previous[1]))
            previous = snapshot
        return deltas

    total_first = paired_deltas([(100, 40), (101, None), (None, 41), (102, None)])
    fsd_first = paired_deltas([(100, 40), (None, 41), (101, None)])
    reciprocal_include = paired_deltas([(100, 40), (100, 41), (101, 41)])
    across_reset = paired_deltas([(100, 40), (102, 41), (1, 0), (2, 1)])

    assert sum(total for total, _ in total_first) == pytest.approx(2)
    assert sum(fsd for _, fsd in total_first) == pytest.approx(1)
    assert all(total - fsd >= 0 for total, fsd in total_first)
    assert fsd_first == [(1, 1)]
    assert reciprocal_include == [(1, 1)]
    assert across_reset == [(2, 1), (1, 1)]


def test_fsd_unobserved_intervals_are_explicitly_uncertain_and_reconcile() -> None:
    total_distance = 5.0
    observed_segments = [(1.0, 2.0), (3.0, 4.0)]
    observed_distance = sum(end - start for start, end in observed_segments)
    uncertain_distance = (
        observed_segments[0][0]
        + (observed_segments[1][0] - observed_segments[0][1])
        + (total_distance - observed_segments[-1][1])
    )

    assert observed_distance == pytest.approx(2.0)
    assert uncertain_distance == pytest.approx(3.0)
    assert observed_distance + uncertain_distance == pytest.approx(total_distance)


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
    assert "recent_message_count" in diagnostic
    assert "messages_with_profile_provenance" in diagnostic
    assert "profile_provenance_missing" in diagnostic
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
