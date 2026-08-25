"""Static, user-safe description of the rebuildable analytical namespace."""

from dataclasses import dataclass
from typing import Final, Literal

from tesla_personal_platform.shared_models import RAW_TELEMETRY_SCHEMA, RAW_TELEMETRY_TABLE

from .telemetry_fields import CategorySampleSpec, category_sample_specs

ObjectKind = Literal["table", "view"]


@dataclass(frozen=True, slots=True)
class AnalyticsColumn:
    """One documented analytical column."""

    name: str
    field_type: str
    description: str


@dataclass(frozen=True, slots=True)
class AnalyticsObject:
    """One table or logical view visible through the analytics MCP."""

    name: str
    kind: ObjectKind
    description: str
    columns: tuple[AnalyticsColumn, ...]
    join_keys: tuple[str, ...] = ("vehicle_id",)
    partition_hint: str | None = None


def _columns(*values: tuple[str, str, str]) -> tuple[AnalyticsColumn, ...]:
    return tuple(AnalyticsColumn(*value) for value in values)


RAW_OBJECT = AnalyticsObject(
    RAW_TELEMETRY_TABLE,
    "table",
    "Permanent append-only decoded Fleet Telemetry deliveries; raw truth is authoritative.",
    tuple(
        AnalyticsColumn(field.name, field.field_type, field.description)
        for field in RAW_TELEMETRY_SCHEMA
    ),
    partition_hint="Filter source_timestamp to prune the daily partition.",
)

TELEMETRY_FIELD_CATALOG = AnalyticsObject(
    "telemetry_field_catalog",
    "view",
    "Pinned Tesla field taxonomy and reviewed Woodhouse collection policy.",
    _columns(
        ("field_name", "STRING", "Tesla Fleet Telemetry field name."),
        ("category", "STRING", "Tesla's documented field category."),
        ("value_type", "STRING", "Tesla's documented value type."),
        ("description", "STRING", "Tesla's documented field meaning."),
        (
            "configured",
            "BOOLEAN",
            "Whether the full broad-v2 profile requests this field; not proof of emission.",
        ),
        ("interval_seconds", "INTEGER", "Requested minimum emission interval."),
        ("minimum_delta", "FLOAT64", "Requested change threshold when configured."),
        ("include_fields", "ARRAY<STRING>", "Fields requested in synchronized delivery."),
        ("exclusion_reason", "STRING", "Reviewed reason an unconfigured field is omitted."),
        ("profile_version", "STRING", "Woodhouse field profile version."),
        ("schema_version", "STRING", "Pinned Tesla field schema snapshot."),
        (
            "target_client_version",
            "STRING",
            "Fleet Telemetry client capability target used to expand the profile.",
        ),
    ),
    join_keys=("field_name",),
)

TELEMETRY_OBSERVATIONS = AnalyticsObject(
    "telemetry_observations",
    "view",
    (
        "One typed row per Fleet Telemetry datum after exact Pub/Sub redelivery "
        "de-duplication; Tesla resend observations remain visible."
    ),
    _columns(
        ("source_timestamp", "TIMESTAMP", "Vehicle observation time."),
        ("ingested_at", "TIMESTAMP", "Telemetry-edge acceptance time."),
        ("processed_at", "TIMESTAMP", "Processor handling time."),
        ("vehicle_id", "STRING", "Opaque internal vehicle identifier."),
        ("record_type", "STRING", "Tesla record type."),
        ("field_name", "STRING", "Fleet Telemetry field name."),
        ("is_invalid", "BOOLEAN", "Tesla marked this field value invalid."),
        ("numeric_value", "FLOAT64", "Numeric representation when available."),
        ("string_value", "STRING", "String or enum representation when available."),
        ("boolean_value", "BOOLEAN", "Boolean representation when available."),
        ("latitude", "FLOAT64", "Latitude for a Location value."),
        ("longitude", "FLOAT64", "Longitude for a Location value."),
        ("value_json", "JSON", "Complete typed protobuf value object."),
        ("telemetry_config_version", "STRING", "Trusted source profile version."),
        ("telemetry_config_hash", "STRING", "Trusted source profile hash."),
        ("transport_message_id", "STRING", "Tesla transaction identifier."),
        ("pubsub_message_id", "STRING", "Google Pub/Sub message identifier."),
        ("telemetry_client_version", "STRING", "Vehicle telemetry client version."),
    ),
    partition_hint="Always filter source_timestamp for bounded historical scans.",
)

VEHICLE_STATE_CHANGES = AnalyticsObject(
    "vehicle_state_changes",
    "view",
    "Successive valid field changes with previous typed values for transition analysis.",
    _columns(
        ("source_timestamp", "TIMESTAMP", "Time of the new state."),
        ("vehicle_id", "STRING", "Opaque internal vehicle identifier."),
        ("field_name", "STRING", "Changed Fleet Telemetry field."),
        ("numeric_value", "FLOAT64", "New numeric value."),
        ("previous_numeric_value", "FLOAT64", "Previous numeric value."),
        ("string_value", "STRING", "New string or enum value."),
        ("previous_string_value", "STRING", "Previous string or enum value."),
        ("boolean_value", "BOOLEAN", "New boolean value."),
        ("previous_boolean_value", "BOOLEAN", "Previous boolean value."),
        ("latitude", "FLOAT64", "New location latitude."),
        ("longitude", "FLOAT64", "New location longitude."),
        ("previous_latitude", "FLOAT64", "Previous location latitude."),
        ("previous_longitude", "FLOAT64", "Previous location longitude."),
        ("value_json", "JSON", "Complete new typed value."),
        ("previous_value_json", "JSON", "Complete previous typed value."),
        ("pubsub_message_id", "STRING", "Source delivery identifier."),
    ),
    partition_hint="Filter source_timestamp and field_name before transition analysis.",
)

DRIVES = AnalyticsObject(
    "drives",
    "view",
    "Drive sessions reconstructed from forward/reverse Gear transitions and nearby observations.",
    _columns(
        ("drive_id", "STRING", "Deterministic vehicle/start-time drive identifier."),
        ("vehicle_id", "STRING", "Opaque internal vehicle identifier."),
        ("started_at", "TIMESTAMP", "First driving Gear transition."),
        ("ended_at", "TIMESTAMP", "First subsequent non-driving Gear transition."),
        ("is_ongoing", "BOOLEAN", "No ending Gear transition has arrived yet."),
        ("duration_seconds", "INTEGER", "Observed session duration."),
        ("start_odometer_miles", "FLOAT64", "First odometer observation in session."),
        ("end_odometer_miles", "FLOAT64", "Last odometer observation in session."),
        ("distance_miles", "FLOAT64", "Non-negative odometer delta."),
        ("start_energy_kwh", "FLOAT64", "First EnergyRemaining observation."),
        ("end_energy_kwh", "FLOAT64", "Last EnergyRemaining observation."),
        ("energy_used_kwh", "FLOAT64", "Non-negative EnergyRemaining decrease."),
        ("efficiency_wh_per_mile", "FLOAT64", "Energy used divided by distance."),
        ("maximum_speed_mph", "FLOAT64", "Maximum observed VehicleSpeed."),
        ("start_latitude", "FLOAT64", "First observed session latitude."),
        ("start_longitude", "FLOAT64", "First observed session longitude."),
        ("end_latitude", "FLOAT64", "Last observed session latitude."),
        ("end_longitude", "FLOAT64", "Last observed session longitude."),
        ("observation_count", "INTEGER", "Typed observations assigned to the session."),
        ("largest_sample_gap_seconds", "INTEGER", "Largest source-time gap in the session."),
        ("telemetry_config_hash", "STRING", "Most recent profile hash in the session."),
    ),
    partition_hint="Filter started_at for trip/date questions.",
)

CHARGE_SESSIONS = AnalyticsObject(
    "charge_sessions",
    "view",
    "Charging sessions reconstructed from DetailedChargeState/ChargeState transitions.",
    _columns(
        ("charge_session_id", "STRING", "Deterministic vehicle/start-time identifier."),
        ("vehicle_id", "STRING", "Opaque internal vehicle identifier."),
        ("started_at", "TIMESTAMP", "Observed transition into charging."),
        ("ended_at", "TIMESTAMP", "First observed transition out of charging."),
        ("is_ongoing", "BOOLEAN", "No ending transition has arrived yet."),
        ("duration_seconds", "INTEGER", "Observed session duration."),
        ("start_soc_percent", "FLOAT64", "First usable SOC observation."),
        ("end_soc_percent", "FLOAT64", "Last usable SOC observation."),
        ("soc_added_percent", "FLOAT64", "Non-negative SOC increase."),
        ("ac_energy_added_kwh", "FLOAT64", "AC charging-energy counter increase."),
        ("dc_energy_added_kwh", "FLOAT64", "DC charging-energy counter increase."),
        ("maximum_ac_power_kw", "FLOAT64", "Maximum observed AC charging power."),
        ("maximum_dc_power_kw", "FLOAT64", "Maximum observed DC charging power."),
        ("maximum_voltage", "FLOAT64", "Maximum observed charger voltage."),
        ("start_latitude", "FLOAT64", "First observed charging latitude."),
        ("start_longitude", "FLOAT64", "First observed charging longitude."),
        ("observation_count", "INTEGER", "Typed observations assigned to the session."),
    ),
    partition_hint="Filter started_at for billing or charging-history questions.",
)

MEDIA_HISTORY = AnalyticsObject(
    "media_history",
    "view",
    "Contiguous track/station and playback-state intervals reconstructed from media changes.",
    _columns(
        ("media_interval_id", "STRING", "Deterministic vehicle/start-time interval ID."),
        ("vehicle_id", "STRING", "Opaque internal vehicle identifier."),
        ("started_at", "TIMESTAMP", "First observation in the interval."),
        ("ended_at", "TIMESTAMP", "Next media identity/state boundary when observed."),
        ("title", "STRING", "Track or item title."),
        ("artist", "STRING", "Artist."),
        ("album", "STRING", "Album."),
        ("station", "STRING", "Station when available."),
        ("playback_source", "STRING", "Media source."),
        ("playback_status", "STRING", "Playing, paused, stopped, or unknown enum."),
        ("duration_ms", "INTEGER", "Reported item duration."),
        ("start_elapsed_ms", "INTEGER", "Playback position at interval start."),
        ("end_elapsed_ms", "INTEGER", "Latest playback position in the interval."),
        ("maximum_audio_volume", "FLOAT64", "Maximum observed volume in the interval."),
        ("observation_count", "INTEGER", "Media update points in the interval."),
    ),
    partition_hint="Filter started_at and join by vehicle_id plus an overlapping drive window.",
)

DAILY_VEHICLE_SUMMARY = AnalyticsObject(
    "daily_vehicle_summary",
    "view",
    "Per-vehicle UTC daily distance, energy, efficiency, driving, charging, SOC, and temperature.",
    _columns(
        ("summary_date", "DATE", "UTC source date."),
        ("vehicle_id", "STRING", "Opaque internal vehicle identifier."),
        ("distance_miles", "FLOAT64", "Sum of reconstructed drive distance."),
        ("drive_energy_used_kwh", "FLOAT64", "Sum of reconstructed drive energy use."),
        ("efficiency_wh_per_mile", "FLOAT64", "Daily energy divided by distance."),
        ("drive_count", "INTEGER", "Drive sessions starting on this date."),
        ("driving_seconds", "INTEGER", "Total observed drive duration."),
        ("charge_session_count", "INTEGER", "Charge sessions starting on this date."),
        ("ac_energy_added_kwh", "FLOAT64", "Summed AC energy counter increases."),
        ("dc_energy_added_kwh", "FLOAT64", "Summed DC energy counter increases."),
        ("minimum_soc_percent", "FLOAT64", "Minimum observed usable SOC."),
        ("maximum_soc_percent", "FLOAT64", "Maximum observed usable SOC."),
        ("average_outside_temp", "FLOAT64", "Average observed outside temperature."),
        ("maximum_speed_mph", "FLOAT64", "Maximum observed speed."),
    ),
    partition_hint="Filter summary_date for efficient dashboard-style questions.",
)

_SAMPLE_METADATA_COLUMNS = _columns(
    ("source_timestamp", "TIMESTAMP", "Exact source time of the emitted Tesla message."),
    ("ingested_at", "TIMESTAMP", "Latest edge acceptance time for the message."),
    ("processed_at", "TIMESTAMP", "Latest processor handling time for the message."),
    ("vehicle_id", "STRING", "Opaque internal vehicle identifier."),
    ("pubsub_message_id", "STRING", "Google Pub/Sub delivery identifier."),
    ("transport_message_id", "STRING", "Tesla transport transaction identifier."),
    ("telemetry_config_version", "STRING", "Trusted source profile version."),
    ("telemetry_config_hash", "STRING", "Trusted source profile hash."),
    ("telemetry_client_version", "STRING", "Vehicle telemetry client version."),
    ("observed_fields", "ARRAY<STRING>", "Category fields emitted in this message."),
    ("invalid_fields", "ARRAY<STRING>", "Emitted fields Tesla marked invalid."),
)


def _sample_object(spec: CategorySampleSpec) -> AnalyticsObject:
    return AnalyticsObject(
        spec.view_name,
        "view",
        (
            f"Sparse exact-emission {spec.category.lower()} samples. A metric is populated only "
            "when Tesla emitted it in that message; inspect observed_fields and invalid_fields."
        ),
        _SAMPLE_METADATA_COLUMNS
        + tuple(
            AnalyticsColumn(column.name, column.field_type, column.description)
            for column in spec.columns
        ),
        join_keys=("vehicle_id", "source_timestamp"),
        partition_hint="Filter source_timestamp for bounded dashboard and graph queries.",
    )


CATEGORY_SAMPLE_OBJECTS: Final = tuple(_sample_object(spec) for spec in category_sample_specs())

ANALYTICS_OBJECTS: Final = (
    RAW_OBJECT,
    TELEMETRY_FIELD_CATALOG,
    TELEMETRY_OBSERVATIONS,
    *CATEGORY_SAMPLE_OBJECTS,
    VEHICLE_STATE_CHANGES,
    DRIVES,
    CHARGE_SESSIONS,
    MEDIA_HISTORY,
    DAILY_VEHICLE_SUMMARY,
)
ANALYTICS_OBJECTS_BY_NAME: Final = {item.name: item for item in ANALYTICS_OBJECTS}
ALLOWED_ANALYTICS_OBJECTS: Final = frozenset(ANALYTICS_OBJECTS_BY_NAME)

EXAMPLE_QUERIES: Final = (
    {
        "purpose": "Inspect configured fields and source cadence by category",
        "sql": (
            "SELECT category, field_name, value_type, interval_seconds, minimum_delta "
            "FROM telemetry_field_catalog WHERE configured ORDER BY category, field_name"
        ),
    },
    {
        "purpose": "Graph speed and acceleration from exact driving emissions",
        "sql": (
            "SELECT source_timestamp, vehicle_id, vehicle_speed, longitudinal_acceleration "
            "FROM driving_samples WHERE source_timestamp >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), "
            "INTERVAL 1 DAY) AND (vehicle_speed IS NOT NULL OR "
            "longitudinal_acceleration IS NOT NULL) ORDER BY source_timestamp"
        ),
    },
    {
        "purpose": "Daily distance and efficiency by vehicle",
        "sql": (
            "SELECT summary_date, vehicle_id, distance_miles, efficiency_wh_per_mile "
            "FROM daily_vehicle_summary WHERE summary_date >= DATE_SUB(CURRENT_DATE(), "
            "INTERVAL 30 DAY) ORDER BY summary_date, vehicle_id"
        ),
    },
    {
        "purpose": "Compare all owned vehicles",
        "sql": (
            "SELECT vehicle_id, COUNT(*) AS drives, SUM(distance_miles) AS miles "
            "FROM drives WHERE started_at >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), "
            "INTERVAL 90 DAY) GROUP BY vehicle_id ORDER BY miles DESC"
        ),
    },
    {
        "purpose": "Reconstruct the media played during a selected drive",
        "sql": (
            "SELECT d.drive_id, m.started_at, m.title, m.artist, m.album, m.station "
            "FROM drives AS d JOIN media_history AS m ON m.vehicle_id = d.vehicle_id "
            "AND m.started_at < COALESCE(d.ended_at, CURRENT_TIMESTAMP()) "
            "AND COALESCE(m.ended_at, CURRENT_TIMESTAMP()) > d.started_at "
            "WHERE d.started_at >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 30 DAY) "
            "AND d.drive_id = 'replace_with_drive_id' "
            "AND m.playback_status = 'MediaStatusPlaying' ORDER BY m.started_at"
        ),
    },
)
