"""Versioned BigQuery logical views derived entirely from permanent raw history."""

# ruff: noqa: S608 -- all interpolated identifiers pass _identifier before SQL construction.

from dataclasses import dataclass
from typing import Final


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
  raw.telemetry_client_version
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

    drives = f"""
WITH gear_events AS (
  SELECT
    source_timestamp,
    vehicle_id,
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
    MIN(IF(NOT is_driving, source_timestamp, NULL)) AS ended_at
  FROM sessionized_gears
  WHERE drive_number > 0
  GROUP BY vehicle_id, drive_number
  HAVING started_at IS NOT NULL
), identified AS (
  SELECT
    TO_HEX(SHA256(CONCAT(vehicle_id, '|', CAST(started_at AS STRING)))) AS drive_id,
    vehicle_id,
    started_at,
    ended_at
  FROM segments
), joined AS (
  SELECT
    drive.*,
    observation.source_timestamp AS observation_timestamp,
    observation.field_name,
    observation.numeric_value,
    observation.latitude,
    observation.longitude,
    observation.telemetry_config_hash,
    TIMESTAMP_DIFF(
      observation.source_timestamp,
      LAG(observation.source_timestamp) OVER (
        PARTITION BY drive.drive_id ORDER BY observation.source_timestamp
      ),
      SECOND
    ) AS sample_gap_seconds
  FROM identified AS drive
  LEFT JOIN {table("telemetry_observations")} AS observation
    ON observation.vehicle_id = drive.vehicle_id
    AND NOT observation.is_invalid
    AND observation.source_timestamp >= drive.started_at
    AND observation.source_timestamp <= COALESCE(drive.ended_at, CURRENT_TIMESTAMP())
), aggregated AS (
  SELECT
    drive_id,
    vehicle_id,
    started_at,
    ended_at,
    ARRAY_AGG(IF(field_name = 'Odometer', numeric_value, NULL) IGNORE NULLS
      ORDER BY observation_timestamp LIMIT 1)[SAFE_OFFSET(0)] AS start_odometer_miles,
    ARRAY_AGG(IF(field_name = 'Odometer', numeric_value, NULL) IGNORE NULLS
      ORDER BY observation_timestamp DESC LIMIT 1)[SAFE_OFFSET(0)] AS end_odometer_miles,
    ARRAY_AGG(IF(field_name = 'EnergyRemaining', numeric_value, NULL) IGNORE NULLS
      ORDER BY observation_timestamp LIMIT 1)[SAFE_OFFSET(0)] AS start_energy_kwh,
    ARRAY_AGG(IF(field_name = 'EnergyRemaining', numeric_value, NULL) IGNORE NULLS
      ORDER BY observation_timestamp DESC LIMIT 1)[SAFE_OFFSET(0)] AS end_energy_kwh,
    MAX(IF(field_name = 'VehicleSpeed', numeric_value, NULL)) AS maximum_speed_mph,
    ARRAY_AGG(IF(field_name = 'Location', latitude, NULL) IGNORE NULLS
      ORDER BY observation_timestamp LIMIT 1)[SAFE_OFFSET(0)] AS start_latitude,
    ARRAY_AGG(IF(field_name = 'Location', longitude, NULL) IGNORE NULLS
      ORDER BY observation_timestamp LIMIT 1)[SAFE_OFFSET(0)] AS start_longitude,
    ARRAY_AGG(IF(field_name = 'Location', latitude, NULL) IGNORE NULLS
      ORDER BY observation_timestamp DESC LIMIT 1)[SAFE_OFFSET(0)] AS end_latitude,
    ARRAY_AGG(IF(field_name = 'Location', longitude, NULL) IGNORE NULLS
      ORDER BY observation_timestamp DESC LIMIT 1)[SAFE_OFFSET(0)] AS end_longitude,
    COUNT(observation_timestamp) AS observation_count,
    MAX(sample_gap_seconds) AS largest_sample_gap_seconds,
    ARRAY_AGG(telemetry_config_hash IGNORE NULLS
      ORDER BY observation_timestamp DESC LIMIT 1)[SAFE_OFFSET(0)] AS telemetry_config_hash
  FROM joined
  GROUP BY drive_id, vehicle_id, started_at, ended_at
), metrics AS (
  SELECT
    *,
    GREATEST(end_odometer_miles - start_odometer_miles, 0) AS distance_miles,
    GREATEST(start_energy_kwh - end_energy_kwh, 0) AS energy_used_kwh
  FROM aggregated
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
  SAFE_DIVIDE(energy_used_kwh * 1000, NULLIF(distance_miles, 0))
    AS efficiency_wh_per_mile,
  maximum_speed_mph,
  start_latitude,
  start_longitude,
  end_latitude,
  end_longitude,
  observation_count,
  largest_sample_gap_seconds,
  telemetry_config_hash
FROM metrics
""".strip()

    charge_sessions = f"""
WITH charge_candidates AS (
  SELECT
    source_timestamp,
    vehicle_id,
    field_name,
    string_value AS charge_state
  FROM {table("telemetry_observations")}
  WHERE NOT is_invalid
    AND field_name IN ('DetailedChargeState', 'ChargeState')
    AND string_value IS NOT NULL
  QUALIFY ROW_NUMBER() OVER (
    PARTITION BY vehicle_id, source_timestamp
    ORDER BY IF(field_name = 'DetailedChargeState', 0, 1), pubsub_message_id
  ) = 1
), charge_events AS (
  SELECT
    *,
    charge_state IN ('DetailedChargeStateCharging', 'ChargeStateCharging', 'Charging')
      AS is_charging
  FROM charge_candidates
), ordered_charge AS (
  SELECT
    *,
    LAG(is_charging, 1, FALSE) OVER (
      PARTITION BY vehicle_id ORDER BY source_timestamp
    ) AS was_charging
  FROM charge_events
), marked_charge AS (
  SELECT
    *,
    IF(is_charging AND NOT was_charging, 1, 0) AS begins_charge
  FROM ordered_charge
), sessionized_charge AS (
  SELECT
    *,
    SUM(begins_charge) OVER (
      PARTITION BY vehicle_id ORDER BY source_timestamp ROWS UNBOUNDED PRECEDING
    ) AS charge_number
  FROM marked_charge
), segments AS (
  SELECT
    vehicle_id,
    charge_number,
    MIN(IF(is_charging, source_timestamp, NULL)) AS started_at,
    MIN(IF(NOT is_charging, source_timestamp, NULL)) AS ended_at
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
    ended_at
  FROM segments
), joined AS (
  SELECT
    session.*,
    observation.source_timestamp AS observation_timestamp,
    observation.field_name,
    observation.numeric_value,
    observation.latitude,
    observation.longitude
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
    ARRAY_AGG(IF(field_name = 'Soc', numeric_value, NULL) IGNORE NULLS
      ORDER BY observation_timestamp LIMIT 1)[SAFE_OFFSET(0)] AS start_soc_percent,
    ARRAY_AGG(IF(field_name = 'Soc', numeric_value, NULL) IGNORE NULLS
      ORDER BY observation_timestamp DESC LIMIT 1)[SAFE_OFFSET(0)] AS end_soc_percent,
    ARRAY_AGG(IF(field_name = 'ACChargingEnergyIn', numeric_value, NULL) IGNORE NULLS
      ORDER BY observation_timestamp LIMIT 1)[SAFE_OFFSET(0)] AS start_ac_energy_kwh,
    ARRAY_AGG(IF(field_name = 'ACChargingEnergyIn', numeric_value, NULL) IGNORE NULLS
      ORDER BY observation_timestamp DESC LIMIT 1)[SAFE_OFFSET(0)] AS end_ac_energy_kwh,
    ARRAY_AGG(IF(field_name = 'DCChargingEnergyIn', numeric_value, NULL) IGNORE NULLS
      ORDER BY observation_timestamp LIMIT 1)[SAFE_OFFSET(0)] AS start_dc_energy_kwh,
    ARRAY_AGG(IF(field_name = 'DCChargingEnergyIn', numeric_value, NULL) IGNORE NULLS
      ORDER BY observation_timestamp DESC LIMIT 1)[SAFE_OFFSET(0)] AS end_dc_energy_kwh,
    MAX(IF(field_name = 'ACChargingPower', numeric_value, NULL)) AS maximum_ac_power_kw,
    MAX(IF(field_name = 'DCChargingPower', numeric_value, NULL)) AS maximum_dc_power_kw,
    MAX(IF(field_name = 'ChargerVoltage', numeric_value, NULL)) AS maximum_voltage,
    ARRAY_AGG(IF(field_name = 'Location', latitude, NULL) IGNORE NULLS
      ORDER BY observation_timestamp LIMIT 1)[SAFE_OFFSET(0)] AS start_latitude,
    ARRAY_AGG(IF(field_name = 'Location', longitude, NULL) IGNORE NULLS
      ORDER BY observation_timestamp LIMIT 1)[SAFE_OFFSET(0)] AS start_longitude,
    COUNT(observation_timestamp) AS observation_count
  FROM joined
  GROUP BY charge_session_id, vehicle_id, started_at, ended_at
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
  maximum_ac_power_kw,
  maximum_dc_power_kw,
  maximum_voltage,
  start_latitude,
  start_longitude,
  observation_count
FROM aggregated
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

    return (
        AnalyticsView(
            "telemetry_observations",
            "Typed Fleet Telemetry datum layer with exact Pub/Sub retry de-duplication.",
            observations,
        ),
        AnalyticsView(
            "vehicle_state_changes",
            "Successive valid typed field transitions.",
            state_changes,
        ),
        AnalyticsView("drives", "Rebuildable drive sessions.", drives),
        AnalyticsView("charge_sessions", "Rebuildable charging sessions.", charge_sessions),
        AnalyticsView("media_history", "Rebuildable media playback intervals.", media_history),
        AnalyticsView(
            "daily_vehicle_summary",
            "Rebuildable UTC daily distance, energy, charging, and state summary.",
            daily_summary,
        ),
    )
