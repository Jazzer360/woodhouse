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
            "Whether the full broad-v4 profile requests this field; not proof of emission.",
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
        ("telemetry_receiver_version", "STRING", "Pinned receiver build version."),
        ("receiver_record_version", "INTEGER", "Receiver-decoded record protocol version."),
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

DRIVE_METRIC_BOUNDARIES = AnalyticsObject(
    "drive_metric_boundaries",
    "view",
    "Selected start/end state for each Gear-defined drive with inspectable provenance.",
    _columns(
        ("drive_id", "STRING", "Deterministic vehicle/start-time drive identifier."),
        ("vehicle_id", "STRING", "Opaque internal vehicle identifier."),
        ("started_at", "TIMESTAMP", "Driving Gear transition."),
        ("ended_at", "TIMESTAMP", "Subsequent non-driving Gear transition."),
        ("boundary_name", "STRING", "start or end."),
        ("field_name", "STRING", "Boundary metric: odometer, energy, SOC, or location."),
        ("boundary_at", "TIMESTAMP", "Actual Gear boundary timestamp."),
        ("selected_observation_at", "TIMESTAMP", "Chosen metric observation timestamp."),
        ("observation_offset_milliseconds", "INTEGER", "Signed observation age at boundary."),
        ("selected_numeric_value", "FLOAT64", "Chosen numeric boundary value."),
        ("selected_latitude", "FLOAT64", "Chosen boundary latitude."),
        ("selected_longitude", "FLOAT64", "Chosen boundary longitude."),
        (
            "inference_method",
            "STRING",
            "Exact, as-of state, stationary, or sparse fallback method.",
        ),
        ("boundary_message_id", "STRING", "Pub/Sub delivery containing the Gear transition."),
        ("observation_message_id", "STRING", "Pub/Sub delivery containing the chosen value."),
    ),
    join_keys=("drive_id", "vehicle_id"),
    partition_hint="Filter started_at for bounded boundary diagnostics.",
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
        ("start_odometer_miles", "FLOAT64", "Odometer selected at the Gear start boundary."),
        ("end_odometer_miles", "FLOAT64", "Odometer selected at the Gear end boundary."),
        ("distance_miles", "FLOAT64", "Non-negative odometer delta."),
        ("route_distance_miles", "FLOAT64", "Plausibility-filtered GPS polyline distance."),
        ("route_point_count", "INTEGER", "Location points available for route distance."),
        (
            "route_rejected_segment_count",
            "INTEGER",
            "GPS segments rejected above the 200 mph plausibility ceiling.",
        ),
        ("route_quality", "STRING", "Usable, insufficient, zero, or rejected route evidence."),
        (
            "best_available_distance_miles",
            "FLOAT64",
            "Odometer distance, otherwise positive GPS route distance.",
        ),
        ("distance_method", "STRING", "Odometer, GPS fallback, or unavailable."),
        ("start_energy_kwh", "FLOAT64", "EnergyRemaining selected at the start boundary."),
        ("end_energy_kwh", "FLOAT64", "EnergyRemaining selected at the end boundary."),
        ("energy_used_kwh", "FLOAT64", "Non-negative EnergyRemaining decrease."),
        ("start_soc_percent", "FLOAT64", "Usable SOC selected at the start boundary."),
        ("end_soc_percent", "FLOAT64", "Usable SOC selected at the end boundary."),
        ("efficiency_wh_per_mile", "FLOAT64", "Energy used divided by distance."),
        ("maximum_speed_mph", "FLOAT64", "Maximum observed VehicleSpeed."),
        ("start_latitude", "FLOAT64", "First observed session latitude."),
        ("start_longitude", "FLOAT64", "First observed session longitude."),
        ("end_latitude", "FLOAT64", "Last observed session latitude."),
        ("end_longitude", "FLOAT64", "Last observed session longitude."),
        ("observation_count", "INTEGER", "Typed observations assigned to the session."),
        ("largest_sample_gap_seconds", "INTEGER", "Largest source-time gap in the session."),
        ("telemetry_config_hash", "STRING", "Most recent profile hash in the session."),
        ("boundary_quality", "STRING", "Exact, stationary inference, or sparse fallback."),
    ),
    partition_hint="Filter started_at for trip/date questions.",
)

CHARGE_SESSIONS = AnalyticsObject(
    "charge_sessions",
    "view",
    "Charging sessions from authoritative DetailedChargeState with bounded ChargeState fallback.",
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
        ("start_soc_observed_at", "TIMESTAMP", "Source time of selected start SOC."),
        ("end_soc_observed_at", "TIMESTAMP", "Source time of selected end SOC."),
        (
            "start_soc_observation_offset_milliseconds",
            "INTEGER",
            "Signed selected-SOC offset from charge start.",
        ),
        (
            "end_soc_observation_offset_milliseconds",
            "INTEGER",
            "Signed selected-SOC offset from charge end.",
        ),
        ("ac_energy_counter_kwh", "FLOAT64", "Measured AC session-counter increase."),
        ("ac_energy_tail_kwh", "FLOAT64", "Bounded post-counter AC power integral."),
        (
            "ac_energy_added_kwh",
            "FLOAT64",
            "Measured AC counter increase plus any defensible bounded tail.",
        ),
        ("ac_energy_upper_bound_kwh", "FLOAT64", "Power/duration plausibility bound."),
        ("dc_energy_counter_kwh", "FLOAT64", "Measured battery-side counter increase."),
        ("dc_energy_tail_kwh", "FLOAT64", "Bounded post-counter DC power integral."),
        (
            "dc_energy_added_kwh",
            "FLOAT64",
            "Measured battery-side counter increase plus any defensible DC tail.",
        ),
        ("dc_energy_upper_bound_kwh", "FLOAT64", "Power/input plausibility bound."),
        ("battery_energy_added_kwh", "FLOAT64", "Battery-side energy added for AC or DC."),
        ("charging_efficiency_percent", "FLOAT64", "Battery energy divided by AC wall energy."),
        ("start_energy_remaining_kwh", "FLOAT64", "Battery energy at session start."),
        ("end_energy_remaining_kwh", "FLOAT64", "Battery energy at session end."),
        ("start_odometer_miles", "FLOAT64", "Odometer at charge start."),
        ("end_odometer_miles", "FLOAT64", "Odometer at charge end."),
        (
            "distance_since_previous_charge_miles",
            "FLOAT64",
            "Odometer distance since the previous charge ended.",
        ),
        ("maximum_ac_power_kw", "FLOAT64", "Maximum observed AC charging power."),
        ("maximum_dc_power_kw", "FLOAT64", "Maximum observed DC charging power."),
        ("maximum_voltage", "FLOAT64", "Maximum observed charger voltage."),
        ("start_latitude", "FLOAT64", "First observed charging latitude."),
        ("start_longitude", "FLOAT64", "First observed charging longitude."),
        ("end_latitude", "FLOAT64", "Charge-end latitude when available."),
        ("end_longitude", "FLOAT64", "Charge-end longitude when available."),
        ("observation_count", "INTEGER", "Typed observations assigned to the session."),
        ("state_source", "STRING", "Detailed state or coarse fallback session source."),
        ("soc_boundary_method", "STRING", "Start SOC boundary inference provenance."),
        ("end_soc_boundary_method", "STRING", "End SOC boundary inference provenance."),
        ("odometer_boundary_method", "STRING", "Start odometer inference provenance."),
        (
            "end_odometer_boundary_method",
            "STRING",
            "End odometer inference provenance.",
        ),
        ("location_boundary_method", "STRING", "Start location inference provenance."),
        (
            "end_location_boundary_method",
            "STRING",
            "End location inference provenance.",
        ),
        ("ac_energy_method", "STRING", "Exact, observed, or bounded power integration."),
        ("dc_energy_method", "STRING", "Battery counter boundary inference provenance."),
        ("start_ac_energy_method", "STRING", "Selected AC counter baseline provenance."),
        ("start_dc_energy_method", "STRING", "Selected battery counter baseline provenance."),
        (
            "energy_quality",
            "STRING",
            (
                "Validated, uncertain baseline/reset, unavailable/unbounded, anomalous, "
                "or implausible."
            ),
        ),
        ("telemetry_config_hash", "STRING", "Most recent profile hash in the session."),
    ),
    partition_hint="Filter started_at for billing or charging-history questions.",
)

DRIVE_PATH_POINTS = AnalyticsObject(
    "drive_path_points",
    "view",
    "Route points with GPS distance scaled to the drive odometer boundary distance.",
    _columns(
        ("drive_id", "STRING", "Drive identifier."),
        ("vehicle_id", "STRING", "Opaque internal vehicle identifier."),
        ("source_timestamp", "TIMESTAMP", "Location observation time."),
        ("latitude", "FLOAT64", "Route latitude."),
        ("longitude", "FLOAT64", "Route longitude."),
        ("speed_mph", "FLOAT64", "Most recent speed at this point."),
        ("soc_percent", "FLOAT64", "Most recent SOC at this point."),
        ("distance_into_drive_miles", "FLOAT64", "Scaled distance from drive start."),
        ("segment_distance_miles", "FLOAT64", "Raw GPS distance from prior point."),
        ("raw_route_distance_miles", "FLOAT64", "Total unscaled GPS polyline distance."),
        ("boundary_distance_miles", "FLOAT64", "Odometer-derived drive distance."),
        ("best_available_distance_miles", "FLOAT64", "Drive distance used for scaling."),
        ("distance_method", "STRING", "Odometer, GPS fallback, or unavailable."),
        ("route_quality", "STRING", "Quality of the GPS route used for point scaling."),
    ),
    join_keys=("drive_id", "vehicle_id", "source_timestamp"),
    partition_hint="Filter source_timestamp or join through a bounded drive window.",
)

DRIVE_FSD_SEGMENTS = AnalyticsObject(
    "drive_fsd_segments",
    "view",
    "Distance buckets inferred as manual, FSD, or uncertain from cumulative counters.",
    _columns(
        ("drive_id", "STRING", "Drive identifier."),
        ("segment_index", "INTEGER", "Distance-ordered segment number."),
        ("started_at", "TIMESTAMP", "Nearest route time to inferred segment start."),
        ("ended_at", "TIMESTAMP", "Nearest route time to inferred segment end."),
        ("start_latitude", "FLOAT64", "Nearest route latitude at segment start."),
        ("start_longitude", "FLOAT64", "Nearest route longitude at segment start."),
        ("end_latitude", "FLOAT64", "Nearest route latitude at segment end."),
        ("end_longitude", "FLOAT64", "Nearest route longitude at segment end."),
        ("start_distance_miles", "FLOAT64", "Distance from drive start."),
        ("end_distance_miles", "FLOAT64", "Distance from drive start."),
        ("distance_miles", "FLOAT64", "Inferred segment distance."),
        ("state", "STRING", "manual, fsd, or uncertain."),
        ("confidence", "FLOAT64", "Inference confidence from zero to one."),
        ("inference_method", "STRING", "Counter pairing/allocation method."),
        ("transition_lower_bound_miles", "FLOAT64", "Lower distance bound for transition."),
        ("transition_upper_bound_miles", "FLOAT64", "Upper distance bound for transition."),
    ),
    join_keys=("drive_id",),
    partition_hint="Filter started_at or select bounded drive IDs.",
)

DRIVE_FSD_SUMMARY = AnalyticsObject(
    "drive_fsd_summary",
    "view",
    "Per-drive aggregate FSD/manual/uncertain distance with explicit confidence.",
    _columns(
        ("drive_id", "STRING", "Drive identifier."),
        ("vehicle_id", "STRING", "Opaque internal vehicle identifier."),
        ("started_at", "TIMESTAMP", "Drive start."),
        ("ended_at", "TIMESTAMP", "Drive end."),
        ("total_distance_miles", "FLOAT64", "Best available drive distance."),
        ("fsd_distance_miles", "FLOAT64", "Distance inferred as FSD."),
        ("manual_distance_miles", "FLOAT64", "Distance inferred as manual."),
        ("uncertain_distance_miles", "FLOAT64", "Distance lacking counter evidence."),
        ("fsd_percent", "FLOAT64", "FSD distance divided by drive distance."),
        ("minimum_confidence", "FLOAT64", "Lowest segment confidence."),
        ("segment_count", "INTEGER", "Number of inferred segments."),
        ("classified_distance_miles", "FLOAT64", "Sum of all classification states."),
        ("unclassified_distance_miles", "FLOAT64", "Residual distance after classification."),
        ("classification_complete", "BOOLEAN", "Classification reconciles within 0.001 mile."),
    ),
    join_keys=("drive_id", "vehicle_id"),
    partition_hint="Filter started_at for trip/date analysis.",
)

DRIVE_PATH = AnalyticsObject(
    "drive_path",
    "view",
    "Route-friendly points annotated with inferred FSD state and confidence.",
    _columns(
        ("drive_id", "STRING", "Drive identifier."),
        ("vehicle_id", "STRING", "Opaque internal vehicle identifier."),
        ("source_timestamp", "TIMESTAMP", "Route point time."),
        ("latitude", "FLOAT64", "Route latitude."),
        ("longitude", "FLOAT64", "Route longitude."),
        ("speed_mph", "FLOAT64", "Carried speed context."),
        ("soc_percent", "FLOAT64", "Carried SOC context."),
        ("distance_into_drive_miles", "FLOAT64", "Distance from drive start."),
        ("fsd_state", "STRING", "manual, fsd, or uncertain."),
        ("fsd_confidence", "FLOAT64", "Segment inference confidence."),
        ("fsd_inference_method", "STRING", "Segment inference provenance."),
    ),
    join_keys=("drive_id", "vehicle_id", "source_timestamp"),
    partition_hint="Filter source_timestamp or select bounded drive IDs.",
)

TELEMETRY_CAPABILITY_DIAGNOSTICS = AnalyticsObject(
    "telemetry_capability_diagnostics",
    "view",
    "Latest client/receiver/profile metadata and seven-day include-fields evidence.",
    _columns(
        ("vehicle_id", "STRING", "Opaque internal vehicle identifier."),
        ("vehicle_firmware", "STRING", "Latest telemetry-reported firmware."),
        ("telemetry_client_version", "STRING", "Latest connection client version."),
        ("observed_client_versions", "ARRAY<STRING>", "Client versions seen in history."),
        ("current_client_first_seen_at", "TIMESTAMP", "First row using the current client."),
        ("current_client_last_seen_at", "TIMESTAMP", "Latest row using the current client."),
        ("telemetry_receiver_version", "STRING", "Pinned receiver build."),
        ("desired_profile_version", "STRING", "Trusted applied profile version."),
        ("desired_profile_hash", "STRING", "Trusted applied profile hash."),
        ("minimum_client_for_include_fields", "STRING", "Required client capability."),
        ("include_fields_requested", "BOOLEAN", "Applied profile/client imply includes."),
        ("include_fields_observed_recently", "BOOLEAN", "Recent synchronized payload evidence."),
        ("gear_anchor_messages", "INTEGER", "Recent Gear anchor messages."),
        ("synchronized_gear_messages", "INTEGER", "Gear messages carrying boundary fields."),
        ("charge_anchor_messages", "INTEGER", "Recent DetailedChargeState messages."),
        ("synchronized_charge_messages", "INTEGER", "Charge anchors carrying metrics."),
        ("fsd_counter_messages", "INTEGER", "Recent FSD/total counter messages."),
        ("synchronized_fsd_messages", "INTEGER", "Messages carrying both counters."),
        ("recent_message_count", "INTEGER", "Messages in the seven-day evidence window."),
        (
            "messages_with_profile_provenance",
            "INTEGER",
            "Recent messages carrying trusted config version and hash.",
        ),
        ("capability_status", "STRING", "Healthy, limited, missing evidence, or mismatch."),
        ("latest_telemetry_at", "TIMESTAMP", "Latest received vehicle datum time."),
    ),
    join_keys=("vehicle_id",),
    partition_hint="This diagnostic intentionally considers only recent evidence.",
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
        ("odometer_distance_miles", "FLOAT64", "Sum of odometer-derived distance."),
        ("route_fallback_distance_miles", "FLOAT64", "Distance supplied by GPS fallback."),
        (
            "distance_unavailable_drive_count",
            "INTEGER",
            "Drive sessions with neither odometer nor usable GPS distance.",
        ),
        ("drive_energy_used_kwh", "FLOAT64", "Sum of reconstructed drive energy use."),
        ("efficiency_wh_per_mile", "FLOAT64", "Daily energy divided by distance."),
        ("drive_count", "INTEGER", "Drive sessions starting on this date."),
        ("driving_seconds", "INTEGER", "Total observed drive duration."),
        ("charge_session_count", "INTEGER", "Charge sessions starting on this date."),
        ("charge_energy_issue_count", "INTEGER", "Sessions with non-validated energy."),
        ("ac_energy_added_kwh", "FLOAT64", "Summed validated AC session energy."),
        ("dc_energy_added_kwh", "FLOAT64", "Summed validated DC session energy."),
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
    DRIVE_METRIC_BOUNDARIES,
    DRIVES,
    CHARGE_SESSIONS,
    DRIVE_PATH_POINTS,
    DRIVE_FSD_SEGMENTS,
    DRIVE_FSD_SUMMARY,
    DRIVE_PATH,
    TELEMETRY_CAPABILITY_DIAGNOSTICS,
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
        "purpose": "Summarize inferred Full Self-Driving share with uncertainty",
        "sql": (
            "SELECT drive_id, total_distance_miles, fsd_distance_miles, fsd_percent, "
            "uncertain_distance_miles, minimum_confidence FROM drive_fsd_summary "
            "WHERE started_at >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 30 DAY) "
            "ORDER BY started_at"
        ),
    },
    {
        "purpose": "Diagnose Fleet Telemetry include-fields capability",
        "sql": (
            "SELECT vehicle_id, vehicle_firmware, telemetry_client_version, "
            "telemetry_receiver_version, desired_profile_version, "
            "include_fields_requested, include_fields_observed_recently, capability_status "
            "FROM telemetry_capability_diagnostics"
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
