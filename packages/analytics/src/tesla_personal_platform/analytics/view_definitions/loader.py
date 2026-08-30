"""Strict loader and validator for declarative BigQuery logical views."""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass
from importlib.resources import files
from typing import Final, Literal, cast

from jinja2 import Environment, PackageLoader, StrictUndefined, select_autoescape
from sqlglot import exp, parse_one

from ..telemetry_fields import category_sample_specs
from .generators import category_sample_context, field_catalog_rows


@dataclass(frozen=True, slots=True)
class AnalyticsView:
    """One dependency-ordered, rebuildable BigQuery view definition."""

    name: str
    description: str
    sql: str


@dataclass(frozen=True, slots=True)
class _ViewSpec:
    name: str
    file: str
    description: str
    depends_on: tuple[str, ...]
    sources: tuple[str, ...]
    kind: Literal["static", "field_catalog", "category_sample"]
    category: str | None


VIEW_LABELS: Final = {
    "application": "tesla-personal-platform",
    "data_class": "restricted-user-telemetry",
    "managed_by": "add-user",
    "layer": "analytics",
}

_SAFE_IDENTIFIER = re.compile(r"^[A-Za-z0-9_-]+$")
_MANIFEST = files(__package__).joinpath("manifest.toml")
_ENVIRONMENT = Environment(
    loader=PackageLoader(__package__, "sql"),
    undefined=StrictUndefined,
    autoescape=select_autoescape(default=False),
    keep_trailing_newline=False,
)


def _identifier(value: str) -> str:
    if not value or _SAFE_IDENTIFIER.fullmatch(value) is None:
        raise ValueError("BigQuery project and dataset identifiers must be opaque safe identifiers")
    return value


def _specs() -> tuple[_ViewSpec, ...]:
    document = tomllib.loads(_MANIFEST.read_text(encoding="utf-8"))
    raw_views = cast(list[dict[str, object]], document.get("view", []))
    specs: list[_ViewSpec] = []
    for raw in raw_views:
        kind = cast(str, raw.get("kind", "static"))
        if kind not in {"static", "field_catalog", "category_sample"}:
            raise ValueError(f"Unsupported analytics view kind: {kind}")
        specs.append(
            _ViewSpec(
                name=cast(str, raw["name"]),
                file=cast(str, raw["file"]),
                description=cast(str, raw["description"]),
                depends_on=tuple(cast(list[str], raw.get("depends_on", []))),
                sources=tuple(cast(list[str], raw.get("sources", []))),
                kind=cast(Literal["static", "field_catalog", "category_sample"], kind),
                category=cast(str | None, raw.get("category")),
            )
        )
    result = tuple(specs)
    _validate_manifest(result)
    return result


def _validate_manifest(specs: tuple[_ViewSpec, ...]) -> None:
    names = {spec.name for spec in specs}
    if len(names) != len(specs):
        raise ValueError("Analytics view names must be unique")
    seen: set[str] = set()
    for spec in specs:
        unknown = set(spec.depends_on) - names
        if unknown:
            raise ValueError(f"{spec.name} has unknown dependencies: {sorted(unknown)}")
        late = set(spec.depends_on) - seen
        if late:
            raise ValueError(f"{spec.name} is declared before dependencies: {sorted(late)}")
        seen.add(spec.name)


def analytics_views(project_id: str, dataset_id: str) -> tuple[AnalyticsView, ...]:
    """Render and validate dependency-ordered views for one trusted user dataset."""
    project = _identifier(project_id)
    dataset = _identifier(dataset_id)
    specs = _specs()
    known_views = frozenset(spec.name for spec in specs)
    samples = {spec.category: spec for spec in category_sample_specs()}

    def table(name: str) -> str:
        return f"`{project}.{dataset}.{name}`"

    def ref(name: str) -> str:
        if name not in known_views:
            raise ValueError(f"Unknown analytics view reference: {name}")
        return table(name)

    rendered: list[AnalyticsView] = []
    for spec in specs:
        context: dict[str, object] = {"ref": ref, "source": table}
        if spec.kind == "field_catalog":
            context["field_catalog_rows"] = field_catalog_rows()
        elif spec.kind == "category_sample":
            if spec.category not in samples:
                raise ValueError(f"Unknown telemetry sample category: {spec.category}")
            context.update(category_sample_context(samples[spec.category]))
        sql = _ENVIRONMENT.get_template(spec.file).render(context).strip()
        _validate_rendered_sql(sql, spec, project, dataset)
        rendered.append(AnalyticsView(spec.name, spec.description, sql))
    return tuple(rendered)


def _validate_rendered_sql(
    sql: str,
    spec: _ViewSpec,
    project: str,
    dataset: str,
) -> None:
    expression = parse_one(sql, read="bigquery")
    if not isinstance(expression, exp.Query):
        raise ValueError(f"Analytics view {spec.name} must render one BigQuery query")
    cte_names = {cte.alias_or_name for cte in expression.find_all(exp.CTE)}
    expected = set(spec.depends_on) | set(spec.sources)
    actual: set[str] = set()
    for table in expression.find_all(exp.Table):
        if not table.db and not table.catalog and table.name in cte_names:
            continue
        if table.catalog != project or table.db != dataset:
            raise ValueError(
                f"Analytics view {spec.name} contains an external or unqualified reference"
            )
        actual.add(table.name)
    if actual != expected:
        raise ValueError(
            f"Analytics view {spec.name} references {sorted(actual)}; "
            f"manifest declares {sorted(expected)}"
        )
