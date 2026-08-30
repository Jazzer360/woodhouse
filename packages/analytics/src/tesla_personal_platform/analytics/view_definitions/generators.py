"""Small, data-driven SQL fragments used by otherwise readable templates."""

from __future__ import annotations

import json

from ..telemetry_fields import CategorySampleSpec, telemetry_catalog_entries


def string_literal(value: str) -> str:
    """Return a BigQuery-compatible quoted string literal."""
    return json.dumps(value, ensure_ascii=True)


def field_catalog_rows() -> str:
    """Render the telemetry catalog's generated STRUCT rows."""
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
            + ", ".join(string_literal(value) for value in entry.include_fields)
            + "]"
        )
        exclusion = (
            string_literal(entry.exclusion_reason)
            if entry.exclusion_reason is not None
            else "CAST(NULL AS STRING)"
        )
        rows.append(
            "STRUCT("
            f"{string_literal(entry.field_name)} AS field_name, "
            f"{string_literal(entry.category)} AS category, "
            f"{string_literal(entry.value_type)} AS value_type, "
            f"{string_literal(entry.description)} AS description, "
            f"{'TRUE' if entry.configured else 'FALSE'} AS configured, "
            f"{interval} AS interval_seconds, "
            f"{delta} AS minimum_delta, "
            f"{includes} AS include_fields, "
            f"{exclusion} AS exclusion_reason, "
            f"{string_literal(entry.profile_version)} AS profile_version, "
            f"{string_literal(entry.schema_version)} AS schema_version, "
            f"{string_literal(entry.target_client_version)} AS target_client_version"
            ")"
        )
    return ",\n  ".join(rows)


def category_sample_context(spec: CategorySampleSpec) -> dict[str, str]:
    """Return the two generated fragments needed by the category sample template."""
    field_names = ", ".join(string_literal(field.field_name) for field in spec.fields)
    aggregates: list[str] = []
    for column in spec.columns:
        condition = f"field_name = {string_literal(column.field_name)} AND NOT is_invalid"
        value = f"IF({condition}, {column.value_expression}, NULL)"
        aggregate = "LOGICAL_OR" if column.field_type == "BOOLEAN" else "MAX"
        aggregates.append(f"  {aggregate}({value}) AS {column.name}")
    return {"field_names": field_names, "projected_columns": ",\n".join(aggregates)}
