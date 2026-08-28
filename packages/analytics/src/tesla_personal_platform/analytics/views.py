"""Versioned BigQuery logical views derived entirely from permanent raw history."""

# ruff: noqa: S608 -- all interpolated identifiers pass _identifier before SQL construction.

import json
from dataclasses import dataclass
from typing import Final

from .telemetry_fields import category_sample_specs, telemetry_catalog_entries


@dataclass(frozen=True, slots=True)
class AnalyticsView:
    """One ordered, rebuildable BigQuery view definition."""

    name: str
    description: str
    sql: str


VIEW_LABELS: Final = {
    "application": "tesla-personal-platform",
    "data_class": "restricted-user-telemetry",
    "managed_by": "add-user",
    "layer": "analytics",
}


def _identifier(value: str) -> str:
    if not value or not value.replace("-", "").replace("_", "").isalnum():
        raise ValueError("BigQuery project and dataset identifiers must be opaque safe identifiers")
    return value


def _string_literal(value: str) -> str:
    return json.dumps(value, ensure_ascii=True)


def _field_catalog_sql() -> str:
    rows: list[str] = []
    for entry in telemetry_catalog_entries():
        interval = (
            str(entry.interval_seconds)
            if entry.interval_seconds is not None
            else "CAST(NULL AS INT64)"
        )
        delta = (
            repr(entry.minimum_delta)
            if entry.minimum_delta is not None
            else "CAST(NULL AS FLOAT64)"
        )
        includes = (
            "ARRAY<STRING>["
            + ", ".join(_string_literal(value) for value in entry.include_fields)
            + "]"
        )
        exclusion = (
            _string_literal(entry.exclusion_reason)
            if entry.exclusion_reason is not None
            else "CAST(NULL AS STRING)"
        )
        rows.append(
            "STRUCT("
            f"{_string_literal(entry.field_name)} AS field_name, "
            f"{_string_literal(entry.category)} AS category, "
            f"{_string_literal(entry.value_type)} AS value_type, "
            f"{_string_literal(entry.description)} AS description, "
            f"{'TRUE' if entry.configured else 'FALSE'} AS configured, "
            f"{interval} AS interval_seconds, "
            f"{delta} AS minimum_delta, "
            f"{includes} AS include_fields, "
            f"{exclusion} AS exclusion_reason, "
            f"{_string_literal(entry.profile_version)} AS profile_version, "
            f"{_string_literal(entry.schema_version)} AS schema_version, "
            f"{_string_literal(entry.target_client_version)} AS target_client_version"
            ")"
        )
    return "SELECT * FROM UNNEST([\n  " + ",\n  ".join(rows) + "\n])"


def _category_sample_sql(observations_table: str, category_index: int) -> str:
    spec = category_sample_specs()[category_index]
    field_names = ", ".join(_string_literal(field.field_name) for field in spec.fields)
    aggregates: list[str] = []
    for column in spec.columns:
        condition = f"field_name = {_string_literal(column.field_name)} AND NOT is_invalid"
        value = f"IF({condition}, {column.value_expression}, NULL)"
        aggregate = "LOGICAL_OR" if column.field_type == "BOOLEAN" else "MAX"
        aggregates.append(f"  {aggregate}({value}) AS {column.name}")
    projected = ",\n".join(aggregates)
    return f"""
SELECT
  source_timestamp,
  MAX(ingested_at) AS ingested_at,
  MAX(processed_at) AS processed_at,
  vehicle_id,
  pubsub_message_id,
  MAX(transport_message_id) AS transport_message_id,
  MAX(telemetry_config_version) AS telemetry_config_version,
  MAX(telemetry_config_hash) AS telemetry_config_hash,
  MAX(telemetry_client_version) AS telemetry_client_version,
  ARRAY_AGG(DISTINCT field_name ORDER BY field_name) AS observed_fields,
  ARRAY_AGG(
    DISTINCT IF(is_invalid, field_name, NULL)
    IGNORE NULLS
    ORDER BY IF(is_invalid, field_name, NULL)
  )
    AS invalid_fields,
{projected}
FROM {observations_table}
WHERE field_name IN ({field_names})
GROUP BY source_timestamp, vehicle_id, pubsub_message_id
""".strip()


def analytics_views(project_id: str, dataset_id: str) -> tuple[AnalyticsView, ...]:
    """Return dependency-ordered views for one trusted per-user dataset."""
    project = _identifier(project_id)
    dataset = _identifier(dataset_id)

    def table(name: str) -> str:
        return f"`{project}.{dataset}.{name}`"

    observations = f"""
WITH raw_deduplicated AS (
  SELECT * EXCEPT(delivery_rank)
  FROM (
    SELECT
      raw.*,
      ROW_NUMBER() OVER (
        PARTITION BY pubsub_message_id
        ORDER BY processed_at, ingested_at
      ) AS delivery_rank
    FROM {table("raw_telemetry_events")} AS raw
    WHERE record_type = 'V'
  )
  WHERE delivery_rank = 1
)
SELECT
  raw.source_timestamp,
  raw.ingested_at,
  raw.processed_at,
  raw.vehicle_id,
  raw.record_type,
  JSON_VALUE(datum, '$.key') AS field_name,
  JSON_QUERY(datum, '$.value.invalid') IS NOT NULL AS is_invalid,
  COALESCE(
    SAFE_CAST(JSON_VALUE(datum, '$.value.doubleValue') AS FLOAT64),
    SAFE_CAST(JSON_VALUE(datum, '$.value.floatValue') AS FLOAT64),
    SAFE_CAST(JSON_VALUE(datum, '$.value.longValue') AS FLOAT64),
    SAFE_CAST(JSON_VALUE(datum, '$.value.intValue') AS FLOAT64),
    SAFE_CAST(JSON_VALUE(datum, '$.value.stringValue') AS FLOAT64)
  ) AS numeric_value,
  COALESCE(
    JSON_VALUE(datum, '$.value.stringValue'),
    JSON_VALUE(datum, '$.value.shiftStateValue'),
    JSON_VALUE(datum, '$.value.detailedChargeStateValue'),
    JSON_VALUE(datum, '$.value.chargingValue'),
    JSON_VALUE(datum, '$.value.laneAssistLevelValue'),
    JSON_VALUE(datum, '$.value.scheduledChargingModeValue'),
    JSON_VALUE(datum, '$.value.sentryModeStateValue'),
    JSON_VALUE(datum, '$.value.speedAssistLevelValue'),
    JSON_VALUE(datum, '$.value.mediaStatusValue'),
    JSON_VALUE(datum, '$.value.bmsStateValue'),
    JSON_VALUE(datum, '$.value.buckleStatusValue'),
    JSON_VALUE(datum, '$.value.carTypeValue'),
    JSON_VALUE(datum, '$.value.chargePortValue'),
    JSON_VALUE(datum, '$.value.chargePortLatchValue'),
    JSON_VALUE(datum, '$.value.doorValue'),
    JSON_VALUE(datum, '$.value.driveInverterStateValue'),
    JSON_VALUE(datum, '$.value.hvilStatusValue'),
    JSON_VALUE(datum, '$.value.hvacPowerValue'),
    JSON_VALUE(datum, '$.value.windowStateValue'),
    JSON_VALUE(datum, '$.value.seatFoldPositionValue'),
    JSON_VALUE(datum, '$.value.tractorAirStatusValue'),
    JSON_VALUE(datum, '$.value.followDistanceValue'),
    JSON_VALUE(datum, '$.value.forwardCollisionSensitivityValue'),
    JSON_VALUE(datum, '$.value.guestModeMobileAccessValue'),
    JSON_VALUE(datum, '$.value.trailerAirStatusValue'),
    JSON_VALUE(datum, '$.value.hvacAutoModeValue'),
    JSON_VALUE(datum, '$.value.cabinOverheatProtectionModeValue'),
    JSON_VALUE(datum, '$.value.cabinOverheatProtectionTemperatureLimitValue'),
    JSON_VALUE(datum, '$.value.defrostModeValue'),
    JSON_VALUE(datum, '$.value.climateKeeperModeValue'),
    JSON_VALUE(datum, '$.value.fastChargerValue'),
    JSON_VALUE(datum, '$.value.cableTypeValue'),
    JSON_VALUE(datum, '$.value.tonneauTentModeValue'),
    JSON_VALUE(datum, '$.value.tonneauPositionValue'),
    JSON_VALUE(datum, '$.value.powershareTypeValue'),
    JSON_VALUE(datum, '$.value.powershareStateValue'),
    JSON_VALUE(datum, '$.value.powershareStopReasonValue'),
    JSON_VALUE(datum, '$.value.displayStateValue'),
    JSON_VALUE(datum, '$.value.distanceUnitValue'),
    JSON_VALUE(datum, '$.value.temperatureUnitValue'),
    JSON_VALUE(datum, '$.value.pressureUnitValue'),
    JSON_VALUE(datum, '$.value.chargeUnitPreferenceValue'),
    JSON_VALUE(datum, '$.value.turnSignalStateValue'),
    JSON_VALUE(datum, '$.value.sunroofInstalledStateValue')
  ) AS string_value,
  SAFE_CAST(JSON_VALUE(datum, '$.value.booleanValue') AS BOOL) AS boolean_value,
  SAFE_CAST(JSON_VALUE(datum, '$.value.locationValue.latitude') AS FLOAT64) AS latitude,
  SAFE_CAST(JSON_VALUE(datum, '$.value.locationValue.longitude') AS FLOAT64) AS longitude,
  JSON_QUERY(datum, '$.value') AS value_json,
  raw.telemetry_config_version,
  raw.telemetry_config_hash,
  raw.transport_message_id,
  raw.pubsub_message_id,
  raw.telemetry_client_version,
  raw.telemetry_receiver_version,
  raw.receiver_record_version
FROM raw_deduplicated AS raw
CROSS JOIN UNNEST(JSON_QUERY_ARRAY(raw.payload, '$.data')) AS datum
WHERE JSON_VALUE(datum, '$.key') IS NOT NULL
""".strip()

    state_changes = f"""
WITH ordered AS (
  SELECT
    observation.*,
    LAG(numeric_value) OVER field_window AS previous_numeric_value,
    LAG(string_value) OVER field_window AS previous_string_value,
    LAG(boolean_value) OVER field_window AS previous_boolean_value,
    LAG(latitude) OVER field_window AS previous_latitude,
    LAG(longitude) OVER field_window AS previous_longitude,
    LAG(TO_JSON_STRING(value_json)) OVER field_window AS previous_value_text
  FROM {table("telemetry_observations")} AS observation
  WHERE NOT is_invalid
  WINDOW field_window AS (
    PARTITION BY vehicle_id, field_name
    ORDER BY source_timestamp, pubsub_message_id
  )
)
SELECT
  source_timestamp,
  vehicle_id,
  field_name,
  numeric_value,
  previous_numeric_value,
  string_value,
  previous_string_value,
  boolean_value,
  previous_boolean_value,
  latitude,
  longitude,
  previous_latitude,
  previous_longitude,
  value_json,
  PARSE_JSON(previous_value_text) AS previous_value_json,
  pubsub_message_id
FROM ordered
WHERE previous_value_text IS NULL OR previous_value_text != TO_JSON_STRING(value_json)
""".strip()

    drive_metric_boundaries = f"""
WITH gear_events AS (
  SELECT
    source_timestamp,
    vehicle_id,
    pubsub_message_id,
    string_value AS gear,
    string_value IN ('ShiftStateD', 'ShiftStateR', 'D', 'R') AS is_driving
  FROM {table("vehicle_state_changes")}
  WHERE field_name = 'Gear' AND string_value IS NOT NULL
), ordered_gears AS (
  SELECT
    *,
    LAG(is_driving, 1, FALSE) OVER (
      PARTITION BY vehicle_id ORDER BY source_timestamp
    ) AS was_driving
  FROM gear_events
), marked_gears AS (
  SELECT
    *,
    IF(is_driving AND NOT was_driving, 1, 0) AS begins_drive
  FROM ordered_gears
), sessionized_gears AS (
  SELECT
    *,
    SUM(begins_drive) OVER (
      PARTITION BY vehicle_id ORDER BY source_timestamp ROWS UNBOUNDED PRECEDING
    ) AS drive_number
  FROM marked_gears
), segments AS (
  SELECT
    vehicle_id,
    drive_number,
    MIN(IF(is_driving, source_timestamp, NULL)) AS started_at,
    ARRAY_AGG(IF(is_driving, pubsub_message_id, NULL) IGNORE NULLS
      ORDER BY source_timestamp LIMIT 1)[SAFE_OFFSET(0)] AS start_gear_message_id,
    MIN(IF(NOT is_driving, source_timestamp, NULL)) AS ended_at,
    ARRAY_AGG(IF(NOT is_driving, pubsub_message_id, NULL) IGNORE NULLS
      ORDER BY source_timestamp LIMIT 1)[SAFE_OFFSET(0)] AS end_gear_message_id
  FROM sessionized_gears
  WHERE drive_number > 0
  GROUP BY vehicle_id, drive_number
  HAVING started_at IS NOT NULL
), identified AS (
  SELECT
    TO_HEX(SHA256(CONCAT(vehicle_id, '|', CAST(started_at AS STRING)))) AS drive_id,
    vehicle_id,
    started_at,
    ended_at,
    start_gear_message_id,
    end_gear_message_id,
    LAG(ended_at) OVER (PARTITION BY vehicle_id ORDER BY started_at)
      AS previous_drive_ended_at,
    LEAD(started_at) OVER (PARTITION BY vehicle_id ORDER BY started_at)
      AS next_drive_started_at
  FROM segments
), boundary_specs AS (
  SELECT
    drive.*,
    boundary_name,
    field_name,
    IF(boundary_name = 'start', started_at, ended_at) AS boundary_at,
    IF(boundary_name = 'start', start_gear_message_id, end_gear_message_id)
      AS boundary_message_id,
    IF(
      boundary_name = 'start',
      IF(previous_drive_ended_at IS NULL,
        TIMESTAMP_SUB(started_at, INTERVAL 5 MINUTE),
        GREATEST(TIMESTAMP_SUB(started_at, INTERVAL 5 MINUTE), previous_drive_ended_at)),
      GREATEST(TIMESTAMP_SUB(ended_at, INTERVAL 90 SECOND), started_at)
    ) AS candidate_window_start,
    IF(
      boundary_name = 'start',
      LEAST(TIMESTAMP_ADD(started_at, INTERVAL 90 SECOND),
        COALESCE(ended_at, TIMESTAMP_ADD(started_at, INTERVAL 90 SECOND))),
      IF(next_drive_started_at IS NULL,
        TIMESTAMP_ADD(ended_at, INTERVAL 5 MINUTE),
        LEAST(TIMESTAMP_ADD(ended_at, INTERVAL 5 MINUTE), next_drive_started_at))
    ) AS candidate_window_end
  FROM identified AS drive
  CROSS JOIN UNNEST([
    STRUCT('start' AS boundary_name, 'Odometer' AS field_name),
    STRUCT('start', 'EnergyRemaining'),
    STRUCT('start', 'Soc'),
    STRUCT('start', 'Location'),
    STRUCT('end', 'Odometer'),
    STRUCT('end', 'EnergyRemaining'),
    STRUCT('end', 'Soc'),
    STRUCT('end', 'Location')
  ])
  WHERE boundary_name = 'start' OR ended_at IS NOT NULL
), candidates AS (
  SELECT
    boundary.*,
    observation.source_timestamp AS selected_observation_at,
    observation.numeric_value AS selected_numeric_value,
    observation.latitude AS selected_latitude,
    observation.longitude AS selected_longitude,
    observation.pubsub_message_id AS observation_message_id,
    CASE
      WHEN observation.source_timestamp IS NULL THEN 'unavailable'
      WHEN observation.pubsub_message_id = boundary.boundary_message_id
        THEN 'exact_synchronized_boundary'
      WHEN boundary.boundary_name = 'start'
        AND observation.source_timestamp <= boundary.boundary_at
        THEN 'stationary_pre_boundary'
      WHEN boundary.boundary_name = 'end'
        AND observation.source_timestamp >= boundary.boundary_at
        THEN 'stationary_post_boundary'
      ELSE 'inside_drive_fallback'
    END AS inference_method,
    ROW_NUMBER() OVER (
      PARTITION BY boundary.drive_id, boundary.boundary_name, boundary.field_name
      ORDER BY
        CASE
          WHEN observation.pubsub_message_id = boundary.boundary_message_id THEN 0
          WHEN boundary.boundary_name = 'start'
            AND observation.source_timestamp <= boundary.boundary_at THEN 1
          WHEN boundary.boundary_name = 'end'
            AND observation.source_timestamp >= boundary.boundary_at THEN 1
          ELSE 2
        END,
        ABS(TIMESTAMP_DIFF(observation.source_timestamp, boundary.boundary_at, MILLISECOND)),
        observation.source_timestamp
    ) AS candidate_rank
  FROM boundary_specs AS boundary
  LEFT JOIN {table("telemetry_observations")} AS observation
    ON observation.vehicle_id = boundary.vehicle_id
    AND NOT observation.is_invalid
    AND observation.field_name = boundary.field_name
    AND observation.source_timestamp BETWEEN boundary.candidate_window_start
      AND boundary.candidate_window_end
)
SELECT
  drive_id,
  vehicle_id,
  started_at,
  ended_at,
  boundary_name,
  field_name,
  boundary_at,
  selected_observation_at,
  TIMESTAMP_DIFF(selected_observation_at, boundary_at, MILLISECOND)
    AS observation_offset_milliseconds,
  selected_numeric_value,
  selected_latitude,
  selected_longitude,
  inference_method,
  boundary_message_id,
  observation_message_id
FROM candidates
WHERE candidate_rank = 1
""".strip()

    drives = f"""
WITH sessions AS (
  SELECT drive_id, vehicle_id, started_at, ended_at
  FROM {table("drive_metric_boundaries")}
  GROUP BY drive_id, vehicle_id, started_at, ended_at
), joined AS (
  SELECT
    drive.*,
    observation.source_timestamp AS observation_timestamp,
    observation.field_name,
    observation.numeric_value,
    observation.telemetry_config_hash,
    TIMESTAMP_DIFF(
      observation.source_timestamp,
      LAG(observation.source_timestamp) OVER (
        PARTITION BY drive.drive_id ORDER BY observation.source_timestamp
      ),
      SECOND
    ) AS sample_gap_seconds
  FROM sessions AS drive
  LEFT JOIN {table("telemetry_observations")} AS observation
    ON observation.vehicle_id = drive.vehicle_id
    AND NOT observation.is_invalid
    AND observation.source_timestamp >= drive.started_at
    AND observation.source_timestamp <= COALESCE(drive.ended_at, CURRENT_TIMESTAMP())
), observation_summary AS (
  SELECT
    drive_id,
    vehicle_id,
    started_at,
    ended_at,
    MAX(IF(field_name = 'VehicleSpeed', numeric_value, NULL)) AS maximum_speed_mph,
    COUNT(observation_timestamp) AS observation_count,
    MAX(sample_gap_seconds) AS largest_sample_gap_seconds,
    ARRAY_AGG(telemetry_config_hash IGNORE NULLS
      ORDER BY observation_timestamp DESC LIMIT 1)[SAFE_OFFSET(0)] AS telemetry_config_hash
  FROM joined
  GROUP BY drive_id, vehicle_id, started_at, ended_at
), boundaries AS (
  SELECT
    drive_id,
    MAX(IF(boundary_name = 'start' AND field_name = 'Odometer',
      selected_numeric_value, NULL)) AS start_odometer_miles,
    MAX(IF(boundary_name = 'end' AND field_name = 'Odometer',
      selected_numeric_value, NULL)) AS end_odometer_miles,
    MAX(IF(boundary_name = 'start' AND field_name = 'EnergyRemaining',
      selected_numeric_value, NULL)) AS start_energy_kwh,
    MAX(IF(boundary_name = 'end' AND field_name = 'EnergyRemaining',
      selected_numeric_value, NULL)) AS end_energy_kwh,
    MAX(IF(boundary_name = 'start' AND field_name = 'Soc',
      selected_numeric_value, NULL)) AS start_soc_percent,
    MAX(IF(boundary_name = 'end' AND field_name = 'Soc',
      selected_numeric_value, NULL)) AS end_soc_percent,
    MAX(IF(boundary_name = 'start' AND field_name = 'Location',
      selected_latitude, NULL)) AS start_latitude,
    MAX(IF(boundary_name = 'start' AND field_name = 'Location',
      selected_longitude, NULL)) AS start_longitude,
    MAX(IF(boundary_name = 'end' AND field_name = 'Location',
      selected_latitude, NULL)) AS end_latitude,
    MAX(IF(boundary_name = 'end' AND field_name = 'Location',
      selected_longitude, NULL)) AS end_longitude,
    CASE
      WHEN COUNTIF(field_name IN ('Odometer', 'EnergyRemaining', 'Soc')
        AND inference_method = 'exact_synchronized_boundary') = 6
        THEN 'exact_synchronized_boundary'
      WHEN COUNTIF(field_name IN ('Odometer', 'EnergyRemaining', 'Soc')
        AND inference_method = 'inside_drive_fallback') = 0
        THEN 'stationary_boundary_inference'
      ELSE 'sparse_boundary_fallback'
    END AS boundary_quality
  FROM {table("drive_metric_boundaries")}
  GROUP BY drive_id
), metrics AS (
  SELECT
    observation.*,
    boundary.* EXCEPT(drive_id),
    GREATEST(end_odometer_miles - start_odometer_miles, 0) AS distance_miles,
    GREATEST(start_energy_kwh - end_energy_kwh, 0) AS energy_used_kwh
  FROM observation_summary AS observation
  JOIN boundaries AS boundary USING (drive_id)
)
SELECT
  drive_id,
  vehicle_id,
  started_at,
  ended_at,
  ended_at IS NULL AS is_ongoing,
  TIMESTAMP_DIFF(COALESCE(ended_at, CURRENT_TIMESTAMP()), started_at, SECOND)
    AS duration_seconds,
  start_odometer_miles,
  end_odometer_miles,
  distance_miles,
  start_energy_kwh,
  end_energy_kwh,
  energy_used_kwh,
  start_soc_percent,
  end_soc_percent,
  SAFE_DIVIDE(energy_used_kwh * 1000, NULLIF(distance_miles, 0))
    AS efficiency_wh_per_mile,
  maximum_speed_mph,
  start_latitude,
  start_longitude,
  end_latitude,
  end_longitude,
  observation_count,
  largest_sample_gap_seconds,
  telemetry_config_hash,
  boundary_quality
FROM metrics
""".strip()

    charge_sessions = f"""
WITH raw_charge_events AS (
  SELECT
    source_timestamp,
    vehicle_id,
    pubsub_message_id,
    field_name,
    string_value AS charge_state,
    CASE
      WHEN field_name = 'DetailedChargeState'
        AND string_value IN ('DetailedChargeStateCharging') THEN 'active'
      WHEN field_name = 'DetailedChargeState'
        AND string_value IN (
          'DetailedChargeStateDisconnected', 'DetailedChargeStateNoPower',
          'DetailedChargeStateComplete', 'DetailedChargeStateStopped'
        ) THEN 'terminal'
      WHEN field_name = 'DetailedChargeState'
        AND string_value IN ('DetailedChargeStateStarting') THEN 'starting'
      WHEN field_name = 'ChargeState'
        AND string_value IN ('Charging', 'ChargeStateCharging') THEN 'active'
      WHEN field_name = 'ChargeState'
        AND string_value IN (
          'Disconnected', 'NoPower', 'Complete', 'Stopped', 'Idle', 'Init',
          'ChargeStateDisconnected', 'ChargeStateNoPower', 'ChargeStateComplete',
          'ChargeStateStopped', 'ChargeStateStarting', 'Starting'
        ) THEN 'terminal'
      ELSE 'unknown'
    END AS state_class
  FROM {table("telemetry_observations")}
  WHERE NOT is_invalid
    AND field_name IN ('DetailedChargeState', 'ChargeState')
    AND string_value IS NOT NULL
), stateful_charge_events AS (
  SELECT
    *,
    LAST_VALUE(IF(field_name = 'DetailedChargeState' AND state_class != 'unknown',
      state_class, NULL) IGNORE NULLS) OVER event_window AS detailed_class,
    LAST_VALUE(IF(field_name = 'DetailedChargeState' AND state_class != 'unknown',
      source_timestamp, NULL) IGNORE NULLS) OVER event_window AS detailed_at,
    LAST_VALUE(IF(field_name = 'DetailedChargeState' AND state_class != 'unknown',
      pubsub_message_id, NULL) IGNORE NULLS) OVER event_window AS detailed_message_id,
    LAST_VALUE(IF(field_name = 'ChargeState' AND state_class != 'unknown',
      state_class, NULL) IGNORE NULLS) OVER event_window AS coarse_class,
    LAST_VALUE(IF(field_name = 'ChargeState' AND state_class != 'unknown',
      source_timestamp, NULL) IGNORE NULLS) OVER event_window AS coarse_at
  FROM raw_charge_events
  WINDOW event_window AS (
    PARTITION BY vehicle_id ORDER BY source_timestamp, pubsub_message_id
    ROWS UNBOUNDED PRECEDING
  )
), charge_events AS (
  SELECT
    *,
    CASE
      WHEN detailed_class = 'active' THEN TRUE
      WHEN detailed_class IN ('terminal', 'starting')
        AND NOT (coarse_class = 'active'
          AND coarse_at > TIMESTAMP_ADD(detailed_at, INTERVAL 15 MINUTE)) THEN FALSE
      WHEN coarse_class = 'active' THEN TRUE
      WHEN coarse_class = 'terminal' THEN FALSE
      ELSE NULL
    END AS is_charging,
    CASE
      WHEN detailed_class = 'active' THEN 'detailed_charge_state'
      WHEN detailed_class IN ('terminal', 'starting')
        AND NOT (coarse_class = 'active'
          AND coarse_at > TIMESTAMP_ADD(detailed_at, INTERVAL 15 MINUTE))
        THEN 'detailed_charge_state'
      ELSE 'coarse_charge_state_fallback'
    END AS state_source
  FROM stateful_charge_events
), ordered_charge AS (
  SELECT
    *,
    LAG(is_charging, 1, FALSE) OVER event_window AS was_charging
  FROM charge_events
  WHERE is_charging IS NOT NULL
  WINDOW event_window AS (
    PARTITION BY vehicle_id ORDER BY source_timestamp, pubsub_message_id
  )
), marked_charge AS (
  SELECT
    *,
    IF(is_charging AND NOT was_charging, 1, 0) AS begins_charge
  FROM ordered_charge
), sessionized_charge AS (
  SELECT
    *,
    SUM(begins_charge) OVER (
      PARTITION BY vehicle_id ORDER BY source_timestamp, pubsub_message_id
      ROWS UNBOUNDED PRECEDING
    ) AS charge_number
  FROM marked_charge
), segments AS (
  SELECT
    vehicle_id,
    charge_number,
    MIN(IF(is_charging, source_timestamp, NULL)) AS started_at,
    ARRAY_AGG(IF(is_charging, detailed_message_id, NULL) IGNORE NULLS
      ORDER BY source_timestamp LIMIT 1)[SAFE_OFFSET(0)] AS start_state_message_id,
    MIN(IF(NOT is_charging, source_timestamp, NULL)) AS ended_at,
    ARRAY_AGG(IF(NOT is_charging, detailed_message_id, NULL) IGNORE NULLS
      ORDER BY source_timestamp LIMIT 1)[SAFE_OFFSET(0)] AS end_state_message_id,
    ARRAY_AGG(state_source ORDER BY source_timestamp LIMIT 1)[SAFE_OFFSET(0)]
      AS state_source
  FROM sessionized_charge
  WHERE charge_number > 0
  GROUP BY vehicle_id, charge_number
  HAVING started_at IS NOT NULL
), identified AS (
  SELECT
    TO_HEX(SHA256(CONCAT(vehicle_id, '|', CAST(started_at AS STRING))))
      AS charge_session_id,
    vehicle_id,
    started_at,
    ended_at,
    start_state_message_id,
    end_state_message_id,
    state_source,
    LAG(ended_at) OVER (PARTITION BY vehicle_id ORDER BY started_at)
      AS previous_charge_ended_at,
    LEAD(started_at) OVER (PARTITION BY vehicle_id ORDER BY started_at)
      AS next_charge_started_at
  FROM segments
), boundary_specs AS (
  SELECT
    session.*,
    boundary_name,
    field_name,
    IF(boundary_name = 'start', started_at, ended_at) AS boundary_at,
    IF(boundary_name = 'start', start_state_message_id, end_state_message_id)
      AS boundary_message_id,
    IF(
      boundary_name = 'start',
      IF(previous_charge_ended_at IS NULL,
        TIMESTAMP_SUB(started_at, INTERVAL 10 MINUTE),
        GREATEST(TIMESTAMP_SUB(started_at, INTERVAL 10 MINUTE), previous_charge_ended_at)),
      GREATEST(TIMESTAMP_SUB(ended_at, INTERVAL 5 MINUTE), started_at)
    ) AS candidate_window_start,
    IF(
      boundary_name = 'start',
      LEAST(TIMESTAMP_ADD(started_at, INTERVAL 2 MINUTE),
        COALESCE(ended_at, TIMESTAMP_ADD(started_at, INTERVAL 2 MINUTE))),
      IF(next_charge_started_at IS NULL,
        TIMESTAMP_ADD(ended_at, INTERVAL 2 MINUTE),
        LEAST(TIMESTAMP_ADD(ended_at, INTERVAL 2 MINUTE), next_charge_started_at))
    ) AS candidate_window_end
  FROM identified AS session
  CROSS JOIN UNNEST([
    STRUCT('start' AS boundary_name, 'Soc' AS field_name),
    STRUCT('start', 'EnergyRemaining'),
    STRUCT('start', 'Odometer'),
    STRUCT('start', 'Location'),
    STRUCT('start', 'ACChargingEnergyIn'),
    STRUCT('start', 'DCChargingEnergyIn'),
    STRUCT('end', 'Soc'),
    STRUCT('end', 'EnergyRemaining'),
    STRUCT('end', 'Odometer'),
    STRUCT('end', 'Location'),
    STRUCT('end', 'ACChargingEnergyIn'),
    STRUCT('end', 'DCChargingEnergyIn')
  ])
  WHERE boundary_name = 'start' OR ended_at IS NOT NULL
), boundary_candidates AS (
  SELECT
    boundary.*,
    observation.source_timestamp AS selected_observation_at,
    observation.numeric_value AS selected_numeric_value,
    observation.latitude AS selected_latitude,
    observation.longitude AS selected_longitude,
    observation.pubsub_message_id AS observation_message_id,
    CASE
      WHEN observation.source_timestamp IS NULL THEN 'unavailable'
      WHEN observation.pubsub_message_id = boundary.boundary_message_id
        THEN 'exact_synchronized_boundary'
      WHEN boundary.boundary_name = 'start'
        AND observation.source_timestamp <= boundary.boundary_at
        THEN 'pre_boundary_observation'
      WHEN boundary.boundary_name = 'end'
        AND observation.source_timestamp >= boundary.boundary_at
        THEN 'post_boundary_observation'
      ELSE 'inside_session_fallback'
    END AS inference_method,
    ROW_NUMBER() OVER (
      PARTITION BY boundary.charge_session_id, boundary.boundary_name, boundary.field_name
      ORDER BY
        CASE
          WHEN observation.pubsub_message_id = boundary.boundary_message_id THEN 0
          WHEN boundary.boundary_name = 'start'
            AND observation.source_timestamp <= boundary.boundary_at THEN 1
          WHEN boundary.boundary_name = 'end'
            AND observation.source_timestamp >= boundary.boundary_at THEN 1
          ELSE 2
        END,
        ABS(TIMESTAMP_DIFF(observation.source_timestamp, boundary.boundary_at, MILLISECOND)),
        observation.source_timestamp
    ) AS candidate_rank
  FROM boundary_specs AS boundary
  LEFT JOIN {table("telemetry_observations")} AS observation
    ON observation.vehicle_id = boundary.vehicle_id
    AND NOT observation.is_invalid
    AND observation.field_name = boundary.field_name
    AND observation.source_timestamp BETWEEN boundary.candidate_window_start
      AND boundary.candidate_window_end
), selected_boundaries AS (
  SELECT * EXCEPT(candidate_rank)
  FROM boundary_candidates
  WHERE candidate_rank = 1
), joined AS (
  SELECT
    session.*,
    observation.source_timestamp AS observation_timestamp,
    observation.field_name,
    observation.numeric_value,
    observation.latitude,
    observation.longitude,
    observation.telemetry_config_hash
  FROM identified AS session
  LEFT JOIN {table("telemetry_observations")} AS observation
    ON observation.vehicle_id = session.vehicle_id
    AND NOT observation.is_invalid
    AND observation.source_timestamp >= session.started_at
    AND observation.source_timestamp <= COALESCE(session.ended_at, CURRENT_TIMESTAMP())
), aggregated AS (
  SELECT
    charge_session_id,
    vehicle_id,
    started_at,
    ended_at,
    MAX(IF(field_name = 'ACChargingPower', numeric_value, NULL)) AS maximum_ac_power_kw,
    MAX(IF(field_name = 'DCChargingPower', numeric_value, NULL)) AS maximum_dc_power_kw,
    MAX(IF(field_name = 'ChargerVoltage', numeric_value, NULL)) AS maximum_voltage,
    COUNT(observation_timestamp) AS observation_count,
    ARRAY_AGG(telemetry_config_hash IGNORE NULLS
      ORDER BY observation_timestamp DESC LIMIT 1)[SAFE_OFFSET(0)] AS telemetry_config_hash
  FROM joined
  GROUP BY charge_session_id, vehicle_id, started_at, ended_at
), boundary_values AS (
  SELECT
    charge_session_id,
    MAX(state_source) AS state_source,
    MAX(IF(boundary_name = 'start' AND field_name = 'Soc',
      selected_numeric_value, NULL)) AS start_soc_percent,
    MAX(IF(boundary_name = 'end' AND field_name = 'Soc',
      selected_numeric_value, NULL)) AS end_soc_percent,
    MAX(IF(boundary_name = 'start' AND field_name = 'EnergyRemaining',
      selected_numeric_value, NULL)) AS start_energy_remaining_kwh,
    MAX(IF(boundary_name = 'end' AND field_name = 'EnergyRemaining',
      selected_numeric_value, NULL)) AS end_energy_remaining_kwh,
    MAX(IF(boundary_name = 'start' AND field_name = 'Odometer',
      selected_numeric_value, NULL)) AS start_odometer_miles,
    MAX(IF(boundary_name = 'end' AND field_name = 'Odometer',
      selected_numeric_value, NULL)) AS end_odometer_miles,
    MAX(IF(boundary_name = 'start' AND field_name = 'Location',
      selected_latitude, NULL)) AS start_latitude,
    MAX(IF(boundary_name = 'start' AND field_name = 'Location',
      selected_longitude, NULL)) AS start_longitude,
    MAX(IF(boundary_name = 'end' AND field_name = 'Location',
      selected_latitude, NULL)) AS end_latitude,
    MAX(IF(boundary_name = 'end' AND field_name = 'Location',
      selected_longitude, NULL)) AS end_longitude,
    MAX(IF(boundary_name = 'start' AND field_name = 'ACChargingEnergyIn'
      AND inference_method = 'exact_synchronized_boundary', selected_numeric_value, 0))
      AS start_ac_energy_kwh,
    MAX(IF(boundary_name = 'end' AND field_name = 'ACChargingEnergyIn',
      selected_numeric_value, NULL)) AS observed_end_ac_energy_kwh,
    MAX(IF(boundary_name = 'end' AND field_name = 'ACChargingEnergyIn',
      selected_observation_at, NULL)) AS end_ac_observed_at,
    MAX(IF(boundary_name = 'end' AND field_name = 'ACChargingEnergyIn',
      inference_method, NULL)) AS ac_energy_method,
    MAX(IF(boundary_name = 'start' AND field_name = 'DCChargingEnergyIn'
      AND inference_method = 'exact_synchronized_boundary', selected_numeric_value, 0))
      AS start_dc_energy_kwh,
    MAX(IF(boundary_name = 'end' AND field_name = 'DCChargingEnergyIn',
      selected_numeric_value, NULL)) AS end_dc_energy_kwh,
    MAX(IF(boundary_name = 'end' AND field_name = 'DCChargingEnergyIn',
      inference_method, NULL)) AS dc_energy_method,
    MAX(IF(boundary_name = 'start' AND field_name = 'Odometer',
      inference_method, NULL)) AS odometer_boundary_method,
    MAX(IF(boundary_name = 'start' AND field_name = 'Soc',
      inference_method, NULL)) AS soc_boundary_method
  FROM selected_boundaries
  GROUP BY charge_session_id
), terminal_power AS (
  SELECT
    session.charge_session_id,
    ARRAY_AGG(STRUCT(observation.source_timestamp, observation.numeric_value)
      ORDER BY observation.source_timestamp DESC LIMIT 1)[SAFE_OFFSET(0)] AS sample
  FROM identified AS session
  JOIN {table("telemetry_observations")} AS observation
    ON observation.vehicle_id = session.vehicle_id
    AND observation.field_name = 'ACChargingPower'
    AND NOT observation.is_invalid
    AND observation.source_timestamp BETWEEN TIMESTAMP_SUB(session.ended_at, INTERVAL 5 MINUTE)
      AND session.ended_at
  WHERE session.ended_at IS NOT NULL
  GROUP BY session.charge_session_id
), corrected AS (
  SELECT
    aggregate.*,
    boundary.* EXCEPT(charge_session_id, ac_energy_method),
    CASE
      WHEN boundary.observed_end_ac_energy_kwh IS NULL THEN NULL
      WHEN boundary.end_ac_observed_at < aggregate.ended_at
        AND TIMESTAMP_DIFF(aggregate.ended_at, boundary.end_ac_observed_at, SECOND)
          BETWEEN 1 AND 120
        AND power.sample.source_timestamp <= aggregate.ended_at
        AND power.sample.numeric_value > 0
      THEN boundary.observed_end_ac_energy_kwh
        + power.sample.numeric_value
          * TIMESTAMP_DIFF(aggregate.ended_at, boundary.end_ac_observed_at, MILLISECOND)
          / 3600000.0
      ELSE boundary.observed_end_ac_energy_kwh
    END AS end_ac_energy_kwh,
    CASE
      WHEN boundary.observed_end_ac_energy_kwh IS NOT NULL
        AND boundary.end_ac_observed_at < aggregate.ended_at
        AND TIMESTAMP_DIFF(aggregate.ended_at, boundary.end_ac_observed_at, SECOND)
          BETWEEN 1 AND 120
        AND power.sample.numeric_value > 0 THEN 'power_integrated'
      ELSE boundary.ac_energy_method
    END AS ac_energy_method
  FROM aggregated AS aggregate
  JOIN boundary_values AS boundary USING (charge_session_id)
  LEFT JOIN terminal_power AS power USING (charge_session_id)
), with_previous_charge AS (
  SELECT
    *,
    LAG(end_odometer_miles) OVER (PARTITION BY vehicle_id ORDER BY started_at)
      AS previous_charge_end_odometer_miles
  FROM corrected
)
SELECT
  charge_session_id,
  vehicle_id,
  started_at,
  ended_at,
  ended_at IS NULL AS is_ongoing,
  TIMESTAMP_DIFF(COALESCE(ended_at, CURRENT_TIMESTAMP()), started_at, SECOND)
    AS duration_seconds,
  start_soc_percent,
  end_soc_percent,
  GREATEST(end_soc_percent - start_soc_percent, 0) AS soc_added_percent,
  GREATEST(end_ac_energy_kwh - start_ac_energy_kwh, 0) AS ac_energy_added_kwh,
  GREATEST(end_dc_energy_kwh - start_dc_energy_kwh, 0) AS dc_energy_added_kwh,
  GREATEST(end_dc_energy_kwh - start_dc_energy_kwh, 0) AS battery_energy_added_kwh,
  SAFE_MULTIPLY(100, SAFE_DIVIDE(
    GREATEST(end_dc_energy_kwh - start_dc_energy_kwh, 0),
    NULLIF(GREATEST(end_ac_energy_kwh - start_ac_energy_kwh, 0), 0)
  )) AS charging_efficiency_percent,
  start_energy_remaining_kwh,
  end_energy_remaining_kwh,
  start_odometer_miles,
  end_odometer_miles,
  GREATEST(start_odometer_miles - previous_charge_end_odometer_miles, 0)
    AS distance_since_previous_charge_miles,
  maximum_ac_power_kw,
  maximum_dc_power_kw,
  maximum_voltage,
  start_latitude,
  start_longitude,
  end_latitude,
  end_longitude,
  observation_count,
  state_source,
  soc_boundary_method,
  odometer_boundary_method,
  ac_energy_method,
  dc_energy_method,
  telemetry_config_hash
FROM with_previous_charge
""".strip()

    drive_path_points = f"""
WITH drive_events AS (
  SELECT
    drive.drive_id,
    drive.vehicle_id,
    drive.started_at,
    drive.ended_at,
    drive.distance_miles,
    observation.source_timestamp,
    MAX(IF(observation.field_name = 'Location', observation.latitude, NULL)) AS latitude,
    MAX(IF(observation.field_name = 'Location', observation.longitude, NULL)) AS longitude,
    MAX(IF(observation.field_name = 'VehicleSpeed', observation.numeric_value, NULL))
      AS speed_update,
    MAX(IF(observation.field_name = 'Soc', observation.numeric_value, NULL)) AS soc_update
  FROM {table("drives")} AS drive
  JOIN {table("telemetry_observations")} AS observation
    ON observation.vehicle_id = drive.vehicle_id
    AND NOT observation.is_invalid
    AND observation.field_name IN ('Location', 'VehicleSpeed', 'Soc')
    AND observation.source_timestamp BETWEEN drive.started_at
      AND COALESCE(drive.ended_at, CURRENT_TIMESTAMP())
  GROUP BY drive.drive_id, drive.vehicle_id, drive.started_at, drive.ended_at,
    drive.distance_miles, observation.source_timestamp
), stateful AS (
  SELECT
    *,
    LAST_VALUE(speed_update IGNORE NULLS) OVER drive_window AS speed_mph,
    LAST_VALUE(soc_update IGNORE NULLS) OVER drive_window AS soc_percent
  FROM drive_events
  WINDOW drive_window AS (
    PARTITION BY drive_id ORDER BY source_timestamp ROWS UNBOUNDED PRECEDING
  )
), location_points AS (
  SELECT
    *,
    ST_GEOGPOINT(longitude, latitude) AS point,
    LAG(ST_GEOGPOINT(longitude, latitude)) OVER (
      PARTITION BY drive_id ORDER BY source_timestamp
    ) AS previous_point
  FROM stateful
  WHERE latitude IS NOT NULL AND longitude IS NOT NULL
), route_distances AS (
  SELECT
    *,
    COALESCE(ST_DISTANCE(previous_point, point) / 1609.344, 0) AS segment_distance_miles
  FROM location_points
), cumulative AS (
  SELECT
    *,
    SUM(segment_distance_miles) OVER (
      PARTITION BY drive_id ORDER BY source_timestamp ROWS UNBOUNDED PRECEDING
    ) AS raw_distance_into_drive_miles
  FROM route_distances
), scaled AS (
  SELECT
    *,
    MAX(raw_distance_into_drive_miles) OVER (PARTITION BY drive_id)
      AS raw_route_distance_miles
  FROM cumulative
)
SELECT
  drive_id,
  vehicle_id,
  source_timestamp,
  latitude,
  longitude,
  speed_mph,
  soc_percent,
  CASE
    WHEN raw_route_distance_miles > 0 AND distance_miles IS NOT NULL
      THEN raw_distance_into_drive_miles * distance_miles / raw_route_distance_miles
    ELSE raw_distance_into_drive_miles
  END AS distance_into_drive_miles,
  segment_distance_miles,
  raw_route_distance_miles,
  distance_miles AS boundary_distance_miles
FROM scaled
""".strip()

    drive_fsd_segments = f"""
WITH counter_updates AS (
  SELECT
    vehicle_id,
    source_timestamp,
    pubsub_message_id,
    MAX(IF(field_name = 'MilesSinceReset', numeric_value, NULL)) AS total_update,
    MAX(IF(field_name = 'SelfDrivingMilesSinceReset', numeric_value, NULL)) AS fsd_update,
    COUNTIF(field_name = 'MilesSinceReset') > 0
      AND COUNTIF(field_name = 'SelfDrivingMilesSinceReset') > 0 AS synchronized_pair
  FROM {table("telemetry_observations")}
  WHERE NOT is_invalid
    AND field_name IN ('MilesSinceReset', 'SelfDrivingMilesSinceReset')
  GROUP BY vehicle_id, source_timestamp, pubsub_message_id
), counter_state AS (
  SELECT
    *,
    LAST_VALUE(total_update IGNORE NULLS) OVER counter_window AS total_miles,
    LAST_VALUE(fsd_update IGNORE NULLS) OVER counter_window AS fsd_miles,
    LAST_VALUE(IF(total_update IS NOT NULL, source_timestamp, NULL) IGNORE NULLS)
      OVER counter_window AS total_observed_at,
    LAST_VALUE(IF(fsd_update IS NOT NULL, source_timestamp, NULL) IGNORE NULLS)
      OVER counter_window AS fsd_observed_at
  FROM counter_updates
  WINDOW counter_window AS (
    PARTITION BY vehicle_id ORDER BY source_timestamp, pubsub_message_id
    ROWS UNBOUNDED PRECEDING
  )
), paired_changes AS (
  SELECT
    *,
    ABS(TIMESTAMP_DIFF(total_observed_at, fsd_observed_at, SECOND)) AS pairing_gap_seconds
  FROM counter_state
  WHERE total_miles IS NOT NULL AND fsd_miles IS NOT NULL
  QUALIFY LAG(TO_JSON_STRING(STRUCT(total_miles, fsd_miles))) OVER (
    PARTITION BY vehicle_id ORDER BY source_timestamp, pubsub_message_id
  ) IS DISTINCT FROM TO_JSON_STRING(STRUCT(total_miles, fsd_miles))
), drive_candidates AS (
  SELECT
    drive.drive_id,
    drive.vehicle_id,
    drive.started_at,
    drive.ended_at,
    drive.distance_miles,
    counter.* EXCEPT(vehicle_id),
    IF(counter.source_timestamp < drive.started_at,
      ROW_NUMBER() OVER (
        PARTITION BY drive.drive_id, counter.source_timestamp < drive.started_at
        ORDER BY counter.source_timestamp DESC
      ), 1) AS before_rank
  FROM {table("drives")} AS drive
  JOIN paired_changes AS counter
    ON counter.vehicle_id = drive.vehicle_id
    AND counter.source_timestamp BETWEEN TIMESTAMP_SUB(drive.started_at, INTERVAL 15 MINUTE)
      AND COALESCE(drive.ended_at, CURRENT_TIMESTAMP())
), selected_points AS (
  SELECT *
  FROM drive_candidates
  WHERE source_timestamp >= started_at OR before_rank = 1
), mapped_candidates AS (
  SELECT
    selected.*,
    path.distance_into_drive_miles AS nearest_path_distance_miles,
    ROW_NUMBER() OVER (
      PARTITION BY selected.drive_id, selected.source_timestamp,
        selected.pubsub_message_id
      ORDER BY ABS(TIMESTAMP_DIFF(path.source_timestamp, selected.source_timestamp,
        MILLISECOND)), path.source_timestamp
    ) AS path_rank
  FROM selected_points AS selected
  LEFT JOIN {table("drive_path_points")} AS path
    ON path.drive_id = selected.drive_id
    AND selected.source_timestamp > selected.started_at
    AND (selected.ended_at IS NULL OR selected.source_timestamp < selected.ended_at)
), mapped_points AS (
  SELECT
    * EXCEPT(nearest_path_distance_miles, path_rank),
    CASE
      WHEN source_timestamp <= started_at THEN 0
      WHEN ended_at IS NOT NULL AND source_timestamp >= ended_at THEN distance_miles
      ELSE nearest_path_distance_miles
    END AS distance_into_drive_miles
  FROM mapped_candidates
  WHERE path_rank = 1
), deltas AS (
  SELECT
    *,
    LAG(source_timestamp) OVER point_window AS previous_counter_at,
    LAG(distance_into_drive_miles) OVER point_window AS previous_distance_miles,
    LAG(total_miles) OVER point_window AS previous_total_miles,
    LAG(fsd_miles) OVER point_window AS previous_fsd_miles
  FROM mapped_points
  WINDOW point_window AS (
    PARTITION BY drive_id ORDER BY source_timestamp, pubsub_message_id
  )
), buckets AS (
  SELECT
    *,
    distance_into_drive_miles - previous_distance_miles AS bucket_distance_miles,
    total_miles - previous_total_miles AS counter_total_delta_miles,
    fsd_miles - previous_fsd_miles AS counter_fsd_delta_miles,
    (total_miles - previous_total_miles) - (fsd_miles - previous_fsd_miles)
      AS counter_manual_delta_miles
  FROM deltas
  WHERE previous_distance_miles IS NOT NULL
    AND distance_into_drive_miles > previous_distance_miles
), valid_buckets AS (
  SELECT
    *,
    CASE
      WHEN counter_total_delta_miles > 0 THEN bucket_distance_miles * LEAST(
        1.0, GREATEST(SAFE_DIVIDE(counter_fsd_delta_miles,
          counter_total_delta_miles), 0.0)
      )
      ELSE LEAST(bucket_distance_miles, GREATEST(counter_fsd_delta_miles, 0))
    END AS inferred_fsd_miles,
    CASE
      WHEN synchronized_pair AND pairing_gap_seconds = 0 THEN 0.9
      WHEN pairing_gap_seconds <= 30 THEN 0.75
      ELSE 0.55
    END AS base_confidence,
    CASE
      WHEN synchronized_pair AND pairing_gap_seconds = 0
        THEN 'synchronized_counter_bucket'
      ELSE 'counter_milestone_inferred'
    END AS base_method
  FROM buckets
  WHERE counter_total_delta_miles >= 0
    AND counter_fsd_delta_miles >= 0
    AND counter_manual_delta_miles >= -0.01
), bucket_segment_arrays AS (
  SELECT
    drive_id,
    vehicle_id,
    started_at AS drive_started_at,
    ended_at AS drive_ended_at,
    distance_miles AS drive_distance_miles,
    ARRAY_CONCAT(
      IF(bucket_distance_miles - inferred_fsd_miles > 0.001, [STRUCT(
        previous_distance_miles AS start_distance_miles,
        distance_into_drive_miles - inferred_fsd_miles AS end_distance_miles,
        'manual' AS state,
        IF(inferred_fsd_miles > 0, base_confidence * 0.8, base_confidence)
          AS confidence,
        base_method AS inference_method,
        previous_distance_miles AS transition_lower_bound_miles,
        distance_into_drive_miles AS transition_upper_bound_miles
      )], []),
      IF(inferred_fsd_miles > 0.001, [STRUCT(
        distance_into_drive_miles - inferred_fsd_miles AS start_distance_miles,
        distance_into_drive_miles AS end_distance_miles,
        'fsd' AS state,
        IF(inferred_fsd_miles < bucket_distance_miles,
          base_confidence * 0.8, base_confidence) AS confidence,
        base_method AS inference_method,
        previous_distance_miles AS transition_lower_bound_miles,
        distance_into_drive_miles AS transition_upper_bound_miles
      )], [])
    ) AS segments
  FROM valid_buckets
), bucket_segments AS (
  SELECT
    bucket.drive_id,
    bucket.vehicle_id,
    bucket.drive_started_at,
    bucket.drive_ended_at,
    bucket.drive_distance_miles,
    segment.*
  FROM bucket_segment_arrays AS bucket
  CROSS JOIN UNNEST(bucket.segments) AS segment
), ranked_bucket_segments AS (
  SELECT
    *,
    ROW_NUMBER() OVER (
      PARTITION BY drive_id ORDER BY end_distance_miles DESC, start_distance_miles DESC
    ) AS reverse_segment_rank
  FROM bucket_segments
), inferred_segments AS (
  SELECT
    ranked.drive_id,
    segment.*
  FROM ranked_bucket_segments AS ranked
  CROSS JOIN UNNEST(ARRAY_CONCAT(
    [STRUCT(
      ranked.start_distance_miles AS start_distance_miles,
      ranked.end_distance_miles AS end_distance_miles,
      ranked.state AS state,
      ranked.confidence AS confidence,
      ranked.inference_method AS inference_method,
      ranked.transition_lower_bound_miles AS transition_lower_bound_miles,
      ranked.transition_upper_bound_miles AS transition_upper_bound_miles
    )],
    IF(
      ranked.reverse_segment_rank = 1
        AND ranked.drive_distance_miles - ranked.end_distance_miles > 0.001,
      [STRUCT(
        ranked.end_distance_miles AS start_distance_miles,
        ranked.drive_distance_miles AS end_distance_miles,
        ranked.state AS state,
        ranked.confidence * 0.6 AS confidence,
        'counter_state_carried_to_drive_boundary' AS inference_method,
        ranked.end_distance_miles AS transition_lower_bound_miles,
        ranked.drive_distance_miles AS transition_upper_bound_miles
      )],
      []
    )
  )) AS segment
), all_segments AS (
  SELECT
    drive.drive_id,
    drive.vehicle_id,
    drive.started_at AS drive_started_at,
    drive.ended_at AS drive_ended_at,
    drive.distance_miles AS drive_distance_miles,
    COALESCE(segment.start_distance_miles, 0.0) AS start_distance_miles,
    COALESCE(segment.end_distance_miles, drive.distance_miles) AS end_distance_miles,
    COALESCE(segment.state, 'uncertain') AS state,
    COALESCE(segment.confidence, 0.0) AS confidence,
    COALESCE(segment.inference_method, 'insufficient_counter_evidence')
      AS inference_method,
    COALESCE(segment.transition_lower_bound_miles, 0.0)
      AS transition_lower_bound_miles,
    COALESCE(segment.transition_upper_bound_miles, drive.distance_miles)
      AS transition_upper_bound_miles
  FROM {table("drives")} AS drive
  LEFT JOIN inferred_segments AS segment USING (drive_id)
), located AS (
  SELECT
    segment.*,
    ARRAY_AGG(
      IF(path.drive_id IS NULL, NULL,
        STRUCT(path.source_timestamp, path.latitude, path.longitude))
      IGNORE NULLS
      ORDER BY ABS(path.distance_into_drive_miles - segment.start_distance_miles),
        path.source_timestamp
      LIMIT 1
    )[SAFE_OFFSET(0)] AS start_point,
    ARRAY_AGG(
      IF(path.drive_id IS NULL, NULL,
        STRUCT(path.source_timestamp, path.latitude, path.longitude))
      IGNORE NULLS
      ORDER BY ABS(path.distance_into_drive_miles - segment.end_distance_miles),
        path.source_timestamp
      LIMIT 1
    )[SAFE_OFFSET(0)] AS end_point
  FROM all_segments AS segment
  LEFT JOIN {table("drive_path_points")} AS path USING (drive_id)
  GROUP BY
    drive_id,
    vehicle_id,
    drive_started_at,
    drive_ended_at,
    drive_distance_miles,
    start_distance_miles,
    end_distance_miles,
    state,
    confidence,
    inference_method,
    transition_lower_bound_miles,
    transition_upper_bound_miles
)
SELECT
  drive_id,
  ROW_NUMBER() OVER (
    PARTITION BY drive_id ORDER BY start_distance_miles, end_distance_miles, state
  ) AS segment_index,
  COALESCE(start_point.source_timestamp, drive_started_at) AS started_at,
  COALESCE(end_point.source_timestamp, drive_ended_at) AS ended_at,
  start_point.latitude AS start_latitude,
  start_point.longitude AS start_longitude,
  end_point.latitude AS end_latitude,
  end_point.longitude AS end_longitude,
  start_distance_miles,
  end_distance_miles,
  GREATEST(end_distance_miles - start_distance_miles, 0) AS distance_miles,
  state,
  confidence,
  inference_method,
  transition_lower_bound_miles,
  transition_upper_bound_miles
FROM located
WHERE end_distance_miles > start_distance_miles
""".strip()

    drive_fsd_summary = f"""
SELECT
  drive.drive_id,
  drive.vehicle_id,
  drive.started_at,
  drive.ended_at,
  drive.distance_miles AS total_distance_miles,
  SUM(IF(segment.state = 'fsd', segment.distance_miles, 0)) AS fsd_distance_miles,
  SUM(IF(segment.state = 'manual', segment.distance_miles, 0)) AS manual_distance_miles,
  SUM(IF(segment.state = 'uncertain', segment.distance_miles, 0)) AS uncertain_distance_miles,
  SAFE_MULTIPLY(100, SAFE_DIVIDE(
    SUM(IF(segment.state = 'fsd', segment.distance_miles, 0)),
    NULLIF(drive.distance_miles, 0)
  )) AS fsd_percent,
  MIN(segment.confidence) AS minimum_confidence,
  COUNT(*) AS segment_count
FROM {table("drives")} AS drive
LEFT JOIN {table("drive_fsd_segments")} AS segment USING (drive_id)
GROUP BY drive.drive_id, drive.vehicle_id, drive.started_at, drive.ended_at,
  drive.distance_miles
""".strip()

    drive_path = f"""
SELECT
  path.drive_id,
  path.vehicle_id,
  path.source_timestamp,
  path.latitude,
  path.longitude,
  path.speed_mph,
  path.soc_percent,
  path.distance_into_drive_miles,
  COALESCE(segment.state, 'uncertain') AS fsd_state,
  COALESCE(segment.confidence, 0) AS fsd_confidence,
  COALESCE(segment.inference_method, 'insufficient_counter_evidence')
    AS fsd_inference_method
FROM {table("drive_path_points")} AS path
LEFT JOIN {table("drive_fsd_segments")} AS segment
  ON segment.drive_id = path.drive_id
  AND path.distance_into_drive_miles >= segment.start_distance_miles
  AND path.distance_into_drive_miles <= segment.end_distance_miles
QUALIFY ROW_NUMBER() OVER (
  PARTITION BY path.drive_id, path.source_timestamp
  ORDER BY segment.start_distance_miles DESC, segment.segment_index DESC
) = 1
""".strip()

    telemetry_capability_diagnostics = f"""
WITH message_fields AS (
  SELECT
    vehicle_id,
    source_timestamp,
    pubsub_message_id,
    MAX(telemetry_client_version) AS telemetry_client_version,
    MAX(telemetry_receiver_version) AS telemetry_receiver_version,
    MAX(telemetry_config_version) AS telemetry_config_version,
    MAX(telemetry_config_hash) AS telemetry_config_hash,
    ARRAY_AGG(DISTINCT field_name ORDER BY field_name) AS fields
  FROM {table("telemetry_observations")}
  GROUP BY vehicle_id, source_timestamp, pubsub_message_id
), latest AS (
  SELECT *
  FROM message_fields
  QUALIFY ROW_NUMBER() OVER (
    PARTITION BY vehicle_id ORDER BY source_timestamp DESC, pubsub_message_id DESC
  ) = 1
), version_spans AS (
  SELECT
    vehicle_id,
    telemetry_client_version,
    MIN(source_timestamp) AS first_seen_at,
    MAX(source_timestamp) AS last_seen_at
  FROM message_fields
  WHERE telemetry_client_version IS NOT NULL
  GROUP BY vehicle_id, telemetry_client_version
), version_history AS (
  SELECT
    vehicle_id,
    ARRAY_AGG(telemetry_client_version ORDER BY first_seen_at) AS observed_client_versions
  FROM version_spans
  GROUP BY vehicle_id
), firmware AS (
  SELECT
    vehicle_id,
    ARRAY_AGG(string_value IGNORE NULLS ORDER BY source_timestamp DESC LIMIT 1)[SAFE_OFFSET(0)]
      AS vehicle_firmware
  FROM {table("telemetry_observations")}
  WHERE field_name = 'Version' AND NOT is_invalid
  GROUP BY vehicle_id
), anchors AS (
  SELECT
    vehicle_id,
    COUNTIF('Gear' IN UNNEST(fields)) AS gear_anchor_messages,
    COUNTIF('Gear' IN UNNEST(fields) AND (
      'Odometer' IN UNNEST(fields) OR 'EnergyRemaining' IN UNNEST(fields)
      OR 'Soc' IN UNNEST(fields) OR 'Location' IN UNNEST(fields)
    )) AS synchronized_gear_messages,
    COUNTIF('DetailedChargeState' IN UNNEST(fields)) AS charge_anchor_messages,
    COUNTIF('DetailedChargeState' IN UNNEST(fields) AND (
      'ACChargingEnergyIn' IN UNNEST(fields) OR 'DCChargingEnergyIn' IN UNNEST(fields)
      OR 'Soc' IN UNNEST(fields) OR 'Odometer' IN UNNEST(fields)
    )) AS synchronized_charge_messages,
    COUNTIF('MilesSinceReset' IN UNNEST(fields)
      OR 'SelfDrivingMilesSinceReset' IN UNNEST(fields)) AS fsd_counter_messages,
    COUNTIF('MilesSinceReset' IN UNNEST(fields)
      AND 'SelfDrivingMilesSinceReset' IN UNNEST(fields)) AS synchronized_fsd_messages
  FROM message_fields
  WHERE source_timestamp >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 7 DAY)
  GROUP BY vehicle_id
)
SELECT
  latest.vehicle_id,
  firmware.vehicle_firmware,
  latest.telemetry_client_version,
  history.observed_client_versions,
  span.first_seen_at AS current_client_first_seen_at,
  span.last_seen_at AS current_client_last_seen_at,
  latest.telemetry_receiver_version,
  latest.telemetry_config_version AS desired_profile_version,
  latest.telemetry_config_hash AS desired_profile_hash,
  '1.3.0' AS minimum_client_for_include_fields,
  latest.telemetry_config_version = 'broad-v4'
    AND (
      SAFE_CAST(SPLIT(latest.telemetry_client_version, '.')[SAFE_OFFSET(0)] AS INT64) > 1
      OR (
        SAFE_CAST(SPLIT(latest.telemetry_client_version, '.')[SAFE_OFFSET(0)] AS INT64) = 1
        AND SAFE_CAST(SPLIT(latest.telemetry_client_version, '.')[SAFE_OFFSET(1)] AS INT64) >= 3
      )
    )
    AS include_fields_requested,
  COALESCE(anchors.synchronized_gear_messages, 0) > 0
    OR COALESCE(anchors.synchronized_charge_messages, 0) > 0
    OR COALESCE(anchors.synchronized_fsd_messages, 0) > 0
    AS include_fields_observed_recently,
  COALESCE(anchors.gear_anchor_messages, 0) AS gear_anchor_messages,
  COALESCE(anchors.synchronized_gear_messages, 0) AS synchronized_gear_messages,
  COALESCE(anchors.charge_anchor_messages, 0) AS charge_anchor_messages,
  COALESCE(anchors.synchronized_charge_messages, 0) AS synchronized_charge_messages,
  COALESCE(anchors.fsd_counter_messages, 0) AS fsd_counter_messages,
  COALESCE(anchors.synchronized_fsd_messages, 0) AS synchronized_fsd_messages,
  CASE
    WHEN latest.telemetry_client_version IS NULL THEN 'client_version_unavailable'
    WHEN SAFE_CAST(SPLIT(latest.telemetry_client_version, '.')[SAFE_OFFSET(0)] AS INT64) < 1
      OR (SAFE_CAST(SPLIT(latest.telemetry_client_version, '.')[SAFE_OFFSET(0)] AS INT64) = 1
        AND SAFE_CAST(SPLIT(latest.telemetry_client_version, '.')[SAFE_OFFSET(1)] AS INT64) < 3)
      THEN 'client_capability_limited'
    WHEN latest.telemetry_config_version != 'broad-v4' THEN 'profile_upgrade_required'
    WHEN COALESCE(anchors.gear_anchor_messages, 0)
      + COALESCE(anchors.charge_anchor_messages, 0)
      + COALESCE(anchors.fsd_counter_messages, 0) = 0 THEN 'insufficient_recent_evidence'
    WHEN COALESCE(anchors.synchronized_gear_messages, 0)
      + COALESCE(anchors.synchronized_charge_messages, 0)
      + COALESCE(anchors.synchronized_fsd_messages, 0) = 0 THEN 'include_fields_not_observed'
    ELSE 'healthy'
  END AS capability_status,
  latest.source_timestamp AS latest_telemetry_at
FROM latest
LEFT JOIN version_spans AS span
  ON span.vehicle_id = latest.vehicle_id
  AND span.telemetry_client_version = latest.telemetry_client_version
LEFT JOIN version_history AS history ON history.vehicle_id = latest.vehicle_id
LEFT JOIN firmware ON firmware.vehicle_id = latest.vehicle_id
LEFT JOIN anchors ON anchors.vehicle_id = latest.vehicle_id
""".strip()

    media_history = f"""
WITH media_updates AS (
  SELECT
    vehicle_id,
    source_timestamp,
    MAX(IF(field_name = 'MediaNowPlayingTitle', string_value, NULL)) AS title_update,
    MAX(IF(field_name = 'MediaNowPlayingArtist', string_value, NULL)) AS artist_update,
    MAX(IF(field_name = 'MediaNowPlayingAlbum', string_value, NULL)) AS album_update,
    MAX(IF(field_name = 'MediaNowPlayingStation', string_value, NULL)) AS station_update,
    MAX(IF(field_name = 'MediaPlaybackSource', string_value, NULL)) AS source_update,
    MAX(IF(field_name = 'MediaPlaybackStatus', string_value, NULL)) AS status_update,
    MAX(IF(field_name = 'MediaNowPlayingDuration', numeric_value, NULL)) AS duration_update,
    MAX(IF(field_name = 'MediaNowPlayingElapsed', numeric_value, NULL)) AS elapsed_update,
    MAX(IF(field_name = 'MediaAudioVolume', numeric_value, NULL)) AS volume_update
  FROM {table("telemetry_observations")}
  WHERE NOT is_invalid AND STARTS_WITH(field_name, 'Media')
  GROUP BY vehicle_id, source_timestamp
), stateful AS (
  SELECT
    vehicle_id,
    source_timestamp,
    LAST_VALUE(title_update IGNORE NULLS) OVER media_window AS title,
    LAST_VALUE(artist_update IGNORE NULLS) OVER media_window AS artist,
    LAST_VALUE(album_update IGNORE NULLS) OVER media_window AS album,
    LAST_VALUE(station_update IGNORE NULLS) OVER media_window AS station,
    LAST_VALUE(source_update IGNORE NULLS) OVER media_window AS playback_source,
    LAST_VALUE(status_update IGNORE NULLS) OVER media_window AS playback_status,
    LAST_VALUE(duration_update IGNORE NULLS) OVER media_window AS duration_ms,
    LAST_VALUE(elapsed_update IGNORE NULLS) OVER media_window AS elapsed_ms,
    LAST_VALUE(volume_update IGNORE NULLS) OVER media_window AS audio_volume
  FROM media_updates
  WINDOW media_window AS (
    PARTITION BY vehicle_id ORDER BY source_timestamp ROWS UNBOUNDED PRECEDING
  )
), signatures AS (
  SELECT
    *,
    TO_JSON_STRING(STRUCT(
      title, artist, album, station, playback_source, playback_status
    )) AS media_signature
  FROM stateful
), marked AS (
  SELECT
    *,
    IF(
      LAG(media_signature) OVER (PARTITION BY vehicle_id ORDER BY source_timestamp)
        IS DISTINCT FROM media_signature,
      1,
      0
    ) AS begins_interval
  FROM signatures
), grouped AS (
  SELECT
    *,
    SUM(begins_interval) OVER (
      PARTITION BY vehicle_id ORDER BY source_timestamp ROWS UNBOUNDED PRECEDING
    ) AS interval_number
  FROM marked
), intervals AS (
  SELECT
    vehicle_id,
    interval_number,
    MIN(source_timestamp) AS started_at,
    ANY_VALUE(title HAVING MIN source_timestamp) AS title,
    ANY_VALUE(artist HAVING MIN source_timestamp) AS artist,
    ANY_VALUE(album HAVING MIN source_timestamp) AS album,
    ANY_VALUE(station HAVING MIN source_timestamp) AS station,
    ANY_VALUE(playback_source HAVING MIN source_timestamp) AS playback_source,
    ANY_VALUE(playback_status HAVING MIN source_timestamp) AS playback_status,
    CAST(ANY_VALUE(duration_ms HAVING MAX source_timestamp) AS INT64) AS duration_ms,
    CAST(ANY_VALUE(elapsed_ms HAVING MIN source_timestamp) AS INT64) AS start_elapsed_ms,
    CAST(ANY_VALUE(elapsed_ms HAVING MAX source_timestamp) AS INT64) AS end_elapsed_ms,
    MAX(audio_volume) AS maximum_audio_volume,
    COUNT(*) AS observation_count
  FROM grouped
  GROUP BY vehicle_id, interval_number
)
SELECT
  TO_HEX(SHA256(CONCAT(vehicle_id, '|', CAST(started_at AS STRING)))) AS media_interval_id,
  vehicle_id,
  started_at,
  LEAD(started_at) OVER (PARTITION BY vehicle_id ORDER BY started_at) AS ended_at,
  title,
  artist,
  album,
  station,
  playback_source,
  playback_status,
  duration_ms,
  start_elapsed_ms,
  end_elapsed_ms,
  maximum_audio_volume,
  observation_count
FROM intervals
WHERE COALESCE(title, artist, album, station, playback_source, playback_status) IS NOT NULL
""".strip()

    daily_summary = f"""
WITH observation_daily AS (
  SELECT
    DATE(source_timestamp) AS summary_date,
    vehicle_id,
    MIN(IF(field_name = 'Soc' AND NOT is_invalid, numeric_value, NULL))
      AS minimum_soc_percent,
    MAX(IF(field_name = 'Soc' AND NOT is_invalid, numeric_value, NULL))
      AS maximum_soc_percent,
    AVG(IF(field_name = 'OutsideTemp' AND NOT is_invalid, numeric_value, NULL))
      AS average_outside_temp,
    MAX(IF(field_name = 'VehicleSpeed' AND NOT is_invalid, numeric_value, NULL))
      AS maximum_speed_mph
  FROM {table("telemetry_observations")}
  GROUP BY summary_date, vehicle_id
), drive_daily AS (
  SELECT
    DATE(started_at) AS summary_date,
    vehicle_id,
    SUM(distance_miles) AS distance_miles,
    SUM(energy_used_kwh) AS drive_energy_used_kwh,
    COUNT(*) AS drive_count,
    SUM(duration_seconds) AS driving_seconds
  FROM {table("drives")}
  GROUP BY summary_date, vehicle_id
), charge_daily AS (
  SELECT
    DATE(started_at) AS summary_date,
    vehicle_id,
    COUNT(*) AS charge_session_count,
    SUM(ac_energy_added_kwh) AS ac_energy_added_kwh,
    SUM(dc_energy_added_kwh) AS dc_energy_added_kwh
  FROM {table("charge_sessions")}
  GROUP BY summary_date, vehicle_id
), daily_keys AS (
  SELECT summary_date, vehicle_id FROM observation_daily
  UNION DISTINCT
  SELECT summary_date, vehicle_id FROM drive_daily
  UNION DISTINCT
  SELECT summary_date, vehicle_id FROM charge_daily
)
SELECT
  key.summary_date,
  key.vehicle_id,
  drive.distance_miles,
  drive.drive_energy_used_kwh,
  SAFE_DIVIDE(drive.drive_energy_used_kwh * 1000, NULLIF(drive.distance_miles, 0))
    AS efficiency_wh_per_mile,
  COALESCE(drive.drive_count, 0) AS drive_count,
  COALESCE(drive.driving_seconds, 0) AS driving_seconds,
  COALESCE(charge.charge_session_count, 0) AS charge_session_count,
  charge.ac_energy_added_kwh,
  charge.dc_energy_added_kwh,
  observation.minimum_soc_percent,
  observation.maximum_soc_percent,
  observation.average_outside_temp,
  observation.maximum_speed_mph
FROM daily_keys AS key
LEFT JOIN observation_daily AS observation USING (summary_date, vehicle_id)
LEFT JOIN drive_daily AS drive USING (summary_date, vehicle_id)
LEFT JOIN charge_daily AS charge USING (summary_date, vehicle_id)
""".strip()

    field_catalog = _field_catalog_sql()
    category_samples = tuple(
        AnalyticsView(
            spec.view_name,
            (
                f"Sparse exact-emission {spec.category.lower()} samples. NULL values were not "
                "validly emitted in that message; inspect observed_fields and invalid_fields."
            ),
            _category_sample_sql(table("telemetry_observations"), index),
        )
        for index, spec in enumerate(category_sample_specs())
    )

    return (
        AnalyticsView(
            "telemetry_field_catalog",
            "Pinned Tesla field taxonomy and reviewed Woodhouse collection policy.",
            field_catalog,
        ),
        AnalyticsView(
            "telemetry_observations",
            "Typed Fleet Telemetry datum layer with exact Pub/Sub retry de-duplication.",
            observations,
        ),
        *category_samples,
        AnalyticsView(
            "vehicle_state_changes",
            "Successive valid typed field transitions.",
            state_changes,
        ),
        AnalyticsView(
            "drive_metric_boundaries",
            "Inspectable drive-boundary state selections with inference provenance.",
            drive_metric_boundaries,
        ),
        AnalyticsView("drives", "Rebuildable drive sessions.", drives),
        AnalyticsView("charge_sessions", "Rebuildable charging sessions.", charge_sessions),
        AnalyticsView(
            "drive_path_points",
            "Distance-scaled route points with carried speed and SOC context.",
            drive_path_points,
        ),
        AnalyticsView(
            "drive_fsd_segments",
            "Manual, FSD, and uncertain route segments inferred from cumulative counters.",
            drive_fsd_segments,
        ),
        AnalyticsView(
            "drive_fsd_summary",
            "Aggregate FSD/manual/uncertain mileage and share for each drive.",
            drive_fsd_summary,
        ),
        AnalyticsView(
            "drive_path",
            "Route-friendly points annotated with inferred FSD state and confidence.",
            drive_path,
        ),
        AnalyticsView(
            "telemetry_capability_diagnostics",
            "Recent receiver/client/profile and synchronized-field evidence by vehicle.",
            telemetry_capability_diagnostics,
        ),
        AnalyticsView("media_history", "Rebuildable media playback intervals.", media_history),
        AnalyticsView(
            "daily_vehicle_summary",
            "Rebuildable UTC daily distance, energy, charging, and state summary.",
            daily_summary,
        ),
    )
