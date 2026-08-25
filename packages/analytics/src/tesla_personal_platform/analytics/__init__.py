"""Versioned BigQuery analytics and authenticated query boundary."""

from .catalog import ALLOWED_ANALYTICS_OBJECTS, ANALYTICS_OBJECTS, EXAMPLE_QUERIES
from .query import (
    MAX_BYTES_BILLED,
    MAX_RESULT_BYTES,
    MAX_RESULT_ROWS,
    AnalyticsContext,
    AnalyticsQueryError,
    BigQueryAnalyticsService,
    ValidatedQuery,
    validate_analytics_query,
)
from .views import AnalyticsView, analytics_views

COMPONENT = "analytics"

__all__ = [
    "ALLOWED_ANALYTICS_OBJECTS",
    "ANALYTICS_OBJECTS",
    "COMPONENT",
    "EXAMPLE_QUERIES",
    "MAX_BYTES_BILLED",
    "MAX_RESULT_BYTES",
    "MAX_RESULT_ROWS",
    "AnalyticsContext",
    "AnalyticsQueryError",
    "AnalyticsView",
    "BigQueryAnalyticsService",
    "ValidatedQuery",
    "analytics_views",
    "validate_analytics_query",
]
