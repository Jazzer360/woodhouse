"""Versioned Tesla field metadata used by analytical catalog and sample views."""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from typing import Final

from tesla_personal_platform.tesla_client.telemetry import broad_profile, load_field_catalog

ANALYTICS_CLIENT_VERSION: Final = "1.3.0"
DASHBOARD_CATEGORIES: Final = {
    "Charging": "charging_samples",
    "Climate": "climate_samples",
    "Driving": "driving_samples",
    "Location": "location_samples",
    "Media": "media_samples",
}


@dataclass(frozen=True, slots=True)
class TelemetryCatalogEntry:
    """One pinned Tesla field plus its Woodhouse collection decision."""

    field_name: str
    category: str
    value_type: str
    description: str
    configured: bool
    interval_seconds: int | None
    minimum_delta: float | None
    include_fields: tuple[str, ...]
    exclusion_reason: str | None
    profile_version: str
    schema_version: str
    target_client_version: str


@dataclass(frozen=True, slots=True)
class SampleColumn:
    """One dashboard column projected from an exact telemetry emission."""

    name: str
    field_name: str
    field_type: str
    value_expression: str
    description: str


@dataclass(frozen=True, slots=True)
class CategorySampleSpec:
    """One configured telemetry category exposed as a sparse wide sample view."""

    category: str
    view_name: str
    fields: tuple[TelemetryCatalogEntry, ...]
    columns: tuple[SampleColumn, ...]


@lru_cache(maxsize=1)
def telemetry_catalog_entries() -> tuple[TelemetryCatalogEntry, ...]:
    """Return all pinned fields with broad-v4 configuration metadata."""
    profile = broad_profile(ANALYTICS_CLIENT_VERSION)
    definitions = load_field_catalog()
    entries: list[TelemetryCatalogEntry] = []
    for definition in definitions:
        config = profile.fields.get(definition.name)
        entries.append(
            TelemetryCatalogEntry(
                field_name=definition.name,
                category=definition.category,
                value_type=definition.value_type,
                description=definition.description,
                configured=config is not None,
                interval_seconds=config.interval_seconds if config else None,
                minimum_delta=(
                    float(config.minimum_delta)
                    if config is not None and config.minimum_delta is not None
                    else None
                ),
                include_fields=config.include_fields if config else (),
                exclusion_reason=profile.excluded_fields.get(definition.name),
                profile_version=profile.version,
                schema_version=profile.schema_version,
                target_client_version=ANALYTICS_CLIENT_VERSION,
            )
        )
    return tuple(entries)


@lru_cache(maxsize=1)
def category_sample_specs() -> tuple[CategorySampleSpec, ...]:
    """Return selected high-value categories and collision-free wide columns."""
    entries = telemetry_catalog_entries()
    specs: list[CategorySampleSpec] = []
    for category, view_name in DASHBOARD_CATEGORIES.items():
        fields = tuple(
            entry for entry in entries if entry.configured and entry.category == category
        )
        columns = tuple(column for entry in fields for column in _sample_columns(entry))
        names = [column.name for column in columns]
        if len(names) != len(set(names)):
            raise RuntimeError(f"Telemetry columns collide in category {category}")
        specs.append(CategorySampleSpec(category, view_name, fields, columns))
    return tuple(specs)


def _sample_columns(entry: TelemetryCatalogEntry) -> tuple[SampleColumn, ...]:
    base = _snake_case(entry.field_name)
    description = f"{entry.description} NULL means this field was not emitted in the sample."
    if entry.value_type == "Location":
        return (
            SampleColumn(
                f"{base}_latitude",
                entry.field_name,
                "FLOAT64",
                "latitude",
                f"Latitude component. {description}",
            ),
            SampleColumn(
                f"{base}_longitude",
                entry.field_name,
                "FLOAT64",
                "longitude",
                f"Longitude component. {description}",
            ),
        )
    if entry.value_type == "boolean":
        return (SampleColumn(base, entry.field_name, "BOOLEAN", "boolean_value", description),)
    if entry.value_type == "integer":
        return (
            SampleColumn(
                base,
                entry.field_name,
                "INTEGER",
                "CAST(numeric_value AS INT64)",
                description,
            ),
        )
    if entry.value_type == "real":
        return (SampleColumn(base, entry.field_name, "FLOAT64", "numeric_value", description),)
    if entry.value_type in {"enum", "string"}:
        return (SampleColumn(base, entry.field_name, "STRING", "string_value", description),)
    return (
        SampleColumn(
            base,
            entry.field_name,
            "STRING",
            "COALESCE(string_value, CAST(numeric_value AS STRING), TO_JSON_STRING(value_json))",
            (
                f"Tesla-reported {entry.value_type} representation; intentionally not normalized. "
                f"{description}"
            ),
        ),
    )


def _snake_case(value: str) -> str:
    first = re.sub(r"(.)([A-Z][a-z]+)", r"\1_\2", value)
    return re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", first).lower()
