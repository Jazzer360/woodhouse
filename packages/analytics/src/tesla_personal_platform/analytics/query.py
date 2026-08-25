"""Authenticated, bounded BigQuery analytical query service."""

from __future__ import annotations

import base64
import json
import logging
import time
from dataclasses import asdict
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Final, Protocol

from google.cloud import bigquery
from sqlglot import exp, parse
from sqlglot.errors import ParseError
from sqlglot.optimizer.scope import build_scope

from .catalog import ALLOWED_ANALYTICS_OBJECTS, ANALYTICS_OBJECTS, EXAMPLE_QUERIES

LOGGER = logging.getLogger(__name__)

MAX_QUERY_CHARACTERS: Final = 32_768
MAX_BYTES_BILLED: Final = 1_073_741_824
MAX_RESULT_ROWS: Final = 1_000
MAX_RESULT_BYTES: Final = 1_048_576
DRY_RUN_TIMEOUT_SECONDS: Final = 15.0
QUERY_TIMEOUT_SECONDS: Final = 30.0
SAFE_ANONYMOUS_FUNCTIONS: Final = frozenset(
    {
        # SQLGlot 30.17 models these BigQuery geography constructors as generic
        # calls. They are deterministic built-ins and cannot access connections.
        "ST_GEOGFROMGEOJSON",
        "ST_GEOGFROMTEXT",
        "ST_GEOGPOINT",
        "ST_MAKELINE",
    }
)


class AnalyticsContext(Protocol):
    """Minimum trusted identity context required by the analytics boundary."""

    @property
    def user_id(self) -> str: ...

    @property
    def dataset_id(self) -> str: ...


class AnalyticsQueryError(Exception):
    """Safe error category and operator-facing message for an analytical request."""

    def __init__(self, category: str, message: str) -> None:
        super().__init__(message)
        self.category = category


class ValidatedQuery:
    """Canonical SQL and trusted in-scope physical object references."""

    def __init__(self, sql: str, referenced_objects: frozenset[str]) -> None:
        self.sql = sql
        self.referenced_objects = referenced_objects


def validate_analytics_query(sql: str) -> ValidatedQuery:
    """Parse one read-only BigQuery query and enforce the private object allowlist."""
    if not isinstance(sql, str) or not sql.strip():
        raise AnalyticsQueryError("invalid_sql", "sql must be a non-empty string")
    if len(sql) > MAX_QUERY_CHARACTERS:
        raise AnalyticsQueryError("invalid_sql", "sql exceeds the supported length")
    try:
        statements = parse(sql, read="bigquery")
    except (ParseError, ValueError) as error:
        raise AnalyticsQueryError(
            "invalid_sql", "SQL could not be parsed as BigQuery SQL"
        ) from error
    if len(statements) != 1 or statements[0] is None:
        raise AnalyticsQueryError("invalid_sql", "Exactly one SQL statement is required")
    statement = statements[0]
    if not isinstance(statement, exp.Query):
        raise AnalyticsQueryError("read_only_required", "Only SELECT or WITH queries are allowed")

    # SQLGlot represents unrecognized and user-defined functions as Anonymous. Rejecting
    # them fails closed against remote/persistent UDFs and EXTERNAL_QUERY escape paths.
    unsafe_function = next(
        (
            function
            for function in statement.find_all(exp.Anonymous)
            if function.name.upper() not in SAFE_ANONYMOUS_FUNCTIONS
        ),
        None,
    )
    if unsafe_function is not None:
        raise AnalyticsQueryError(
            "unsupported_function",
            "Unrecognized, external, remote, or user-defined functions are not allowed",
        )

    root_scope = build_scope(statement)
    if root_scope is None:
        raise AnalyticsQueryError("invalid_sql", "SQL query scope could not be resolved")

    referenced: set[str] = set()
    for scope in root_scope.traverse():
        for alias, source in scope.sources.items():
            del alias
            if not isinstance(source, exp.Table):
                continue
            if source.catalog or source.db:
                raise AnalyticsQueryError(
                    "dataset_boundary",
                    "Qualified project or dataset references are not allowed",
                )
            name = source.name
            if name not in ALLOWED_ANALYTICS_OBJECTS:
                raise AnalyticsQueryError(
                    "object_not_allowed",
                    "Query references an object outside the authenticated analytics catalog",
                )
            referenced.add(name)

    # Execute the canonical AST rendering rather than the caller's original text. This
    # closes parser/engine differentials and strips comments without relying on regex.
    canonical = statement.sql(dialect="bigquery", pretty=False)
    return ValidatedQuery(canonical, frozenset(referenced))


class BigQueryAnalyticsService:
    """Expose trusted schema metadata and execute bounded per-user queries."""

    def __init__(
        self,
        client: bigquery.Client,
        project_id: str,
        location: str,
        *,
        maximum_bytes_billed: int = MAX_BYTES_BILLED,
    ) -> None:
        self._client = client
        self._project_id = project_id
        self._location = location
        self._maximum_bytes_billed = maximum_bytes_billed

    def get_schema(self, context: AnalyticsContext, *, correlation_id: str) -> dict[str, Any]:
        """Return available trusted objects without revealing their physical namespace."""
        default_dataset = bigquery.DatasetReference(self._project_id, context.dataset_id)
        try:
            available_names = {
                table.table_id
                for table in self._client.list_tables(default_dataset, max_results=100)
                if table.table_id in ALLOWED_ANALYTICS_OBJECTS
            }
        except Exception as error:
            LOGGER.warning(
                "analytics_schema_failed",
                extra={
                    "correlation_id": correlation_id,
                    "user_id": context.user_id,
                    "error_type": type(error).__name__,
                },
            )
            raise AnalyticsQueryError(
                "schema_unavailable",
                "The authenticated analytics catalog could not be inspected",
            ) from error
        return {
            "dataset_scope": "authenticated user's private default dataset",
            "objects": [
                {
                    **asdict(item),
                    "columns": [asdict(column) for column in item.columns],
                }
                for item in ANALYTICS_OBJECTS
                if item.name in available_names
            ],
            "unavailable_catalog_objects": sorted(ALLOWED_ANALYTICS_OBJECTS - available_names),
            "examples": list(EXAMPLE_QUERIES),
            "query_limits": {
                "maximum_bytes_billed": self._maximum_bytes_billed,
                "maximum_result_rows": MAX_RESULT_ROWS,
                "maximum_result_bytes": MAX_RESULT_BYTES,
                "timeout_seconds": QUERY_TIMEOUT_SECONDS,
            },
            "notes": [
                "Use unqualified table/view names only.",
                "Unavailable catalog objects require the idempotent add-user repair workflow.",
                "Filter source_timestamp, started_at, or summary_date for bounded scans.",
                "Raw telemetry is permanent truth; views are rebuildable interpretations.",
            ],
        }

    def run_query(
        self,
        context: AnalyticsContext,
        sql: str,
        *,
        correlation_id: str,
    ) -> dict[str, Any]:
        """Dry-run, execute, bound, and metadata-log one authenticated query."""
        validated = validate_analytics_query(sql)
        default_dataset = bigquery.DatasetReference(self._project_id, context.dataset_id)
        dry_config = bigquery.QueryJobConfig(
            default_dataset=default_dataset,
            dry_run=True,
            use_query_cache=False,
            use_legacy_sql=False,
            maximum_bytes_billed=self._maximum_bytes_billed,
        )
        started = time.monotonic()
        try:
            dry_job = self._client.query(
                validated.sql,
                job_config=dry_config,
                location=self._location,
                timeout=DRY_RUN_TIMEOUT_SECONDS,
            )
            estimated_bytes = int(dry_job.total_bytes_processed or 0)
            if estimated_bytes > self._maximum_bytes_billed:
                raise AnalyticsQueryError(
                    "query_too_expensive",
                    "Dry run exceeds the query byte safety ceiling; narrow the time range",
                )

            execute_config = bigquery.QueryJobConfig(
                default_dataset=default_dataset,
                dry_run=False,
                use_query_cache=True,
                use_legacy_sql=False,
                maximum_bytes_billed=self._maximum_bytes_billed,
                job_timeout_ms=int(QUERY_TIMEOUT_SECONDS * 1000),
            )
            query_job = self._client.query(
                validated.sql,
                job_config=execute_config,
                location=self._location,
                timeout=DRY_RUN_TIMEOUT_SECONDS,
            )
            iterator = query_job.result(
                timeout=QUERY_TIMEOUT_SECONDS,
                max_results=MAX_RESULT_ROWS + 1,
            )
            rows, result_bytes, truncated = _bounded_rows(iterator)
        except AnalyticsQueryError:
            raise
        except Exception as error:
            LOGGER.warning(
                "analytics_query_failed",
                extra={
                    "correlation_id": correlation_id,
                    "user_id": context.user_id,
                    "referenced_objects": sorted(validated.referenced_objects),
                    "error_type": type(error).__name__,
                },
            )
            raise AnalyticsQueryError(
                "query_failed",
                "BigQuery rejected or could not complete the analytical query",
            ) from error

        duration_ms = round((time.monotonic() - started) * 1000)
        bytes_processed = int(query_job.total_bytes_processed or estimated_bytes)
        bytes_billed = int(query_job.total_bytes_billed or 0)
        LOGGER.info(
            "analytics_query_completed",
            extra={
                "correlation_id": correlation_id,
                "user_id": context.user_id,
                "job_id": query_job.job_id,
                "bytes_processed": bytes_processed,
                "bytes_billed": bytes_billed,
                "duration_ms": duration_ms,
                "referenced_objects": sorted(validated.referenced_objects),
                "returned_rows": len(rows),
                "result_bytes": result_bytes,
                "truncated": truncated,
            },
        )
        return {
            "columns": [
                {"name": field.name, "type": field.field_type} for field in (query_job.schema or ())
            ],
            "rows": rows,
            "row_count": len(rows),
            "truncated": truncated,
            "bytes_processed": bytes_processed,
            "bytes_billed": bytes_billed,
            "duration_ms": duration_ms,
            "job_id": query_job.job_id,
            "referenced_objects": sorted(validated.referenced_objects),
        }


def _bounded_rows(iterator: Any) -> tuple[list[dict[str, Any]], int, bool]:
    rows: list[dict[str, Any]] = []
    total_bytes = 2
    truncated = False
    for item in iterator:
        if len(rows) >= MAX_RESULT_ROWS:
            truncated = True
            break
        serialized = {str(key): _json_value(value) for key, value in item.items()}
        encoded = json.dumps(serialized, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        if total_bytes + len(encoded) > MAX_RESULT_BYTES:
            truncated = True
            break
        rows.append(serialized)
        total_bytes += len(encoded) + 1
    return rows, total_bytes, truncated


def _json_value(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, bytes):
        return base64.b64encode(value).decode("ascii")
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    return str(value)
