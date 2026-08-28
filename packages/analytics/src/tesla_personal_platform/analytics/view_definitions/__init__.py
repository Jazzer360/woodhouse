"""Declarative BigQuery logical-view definitions."""

from .loader import VIEW_LABELS, AnalyticsView, analytics_views

__all__ = ["AnalyticsView", "VIEW_LABELS", "analytics_views"]
