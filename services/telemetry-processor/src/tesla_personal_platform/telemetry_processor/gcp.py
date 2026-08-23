"""Narrow Google Cloud adapters for telemetry ingestion."""

import re
from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta
from hashlib import sha256

from google.api_core.exceptions import AlreadyExists
from google.auth.transport import requests as google_requests
from google.cloud import bigquery, firestore
from google.cloud.firestore_v1 import Client as FirestoreClient
from google.cloud.firestore_v1.base_query import FieldFilter
from google.oauth2 import id_token
from tesla_personal_platform.shared_models import RAW_TELEMETRY_TABLE, RawTelemetryEvent
from tesla_personal_platform.telemetry_processor.processor import (
    RetryableProcessingError,
    VehicleRoute,
)

VIN_INDEX = "vehicle_vin_index"
VEHICLES = "vehicles"
ALLOWED_USERS = "allowed_users"
FIXTURE_RETRIES = "telemetry_pipeline_fixtures"
DATASET_ID = re.compile(r"^tesla_u_[a-z0-9_]{1,80}$")
BIGQUERY_DIAGNOSTIC = re.compile(r"^[A-Za-z0-9_.-]{1,80}$")


class GooglePushTokenVerifier:
    """Verify the exact Pub/Sub push audience and service-account identity."""

    def __init__(self, audience: str, expected_email: str) -> None:
        self._audience = audience
        self._expected_email = expected_email
        self._request = google_requests.Request()

    def verify(self, authorization: str | None) -> None:
        if authorization is None or not authorization.startswith("Bearer "):
            raise ValueError("missing_bearer_token")
        token = authorization.removeprefix("Bearer ").strip()
        if not token:
            raise ValueError("missing_bearer_token")
        claims = id_token.verify_oauth2_token(  # type: ignore[no-untyped-call]
            token, self._request, audience=self._audience
        )
        if claims.get("iss") not in {"accounts.google.com", "https://accounts.google.com"}:
            raise ValueError("invalid_token_issuer")
        if claims.get("email") != self._expected_email or claims.get("email_verified") is not True:
            raise ValueError("unexpected_push_identity")


class FirestoreVehicleRegistry:
    """Resolve VIN through both trusted index and authoritative vehicle/user records."""

    def __init__(self, client: FirestoreClient) -> None:
        self._client = client

    def resolve(self, vin: str) -> VehicleRoute | None:
        index_id = sha256(vin.encode()).hexdigest()
        index = self._client.collection(VIN_INDEX).document(index_id).get()
        if not index.exists:
            return None
        index_data = index.to_dict()
        if not isinstance(index_data, dict):
            return None
        vehicle_id = index_data.get("vehicle_id")
        owner_user_id = index_data.get("owner_user_id")
        if not isinstance(vehicle_id, str) or not isinstance(owner_user_id, str):
            return None

        vehicle = self._client.collection(VEHICLES).document(vehicle_id).get()
        if not vehicle.exists:
            return None
        vehicle_data = vehicle.to_dict()
        if not isinstance(vehicle_data, dict) or any(
            (
                vehicle_data.get("vin") != vin,
                vehicle_data.get("owner_user_id") != owner_user_id,
                index_data.get("owner_user_id") != vehicle_data.get("owner_user_id"),
            )
        ):
            return None

        users = list(
            self._client.collection(ALLOWED_USERS)
            .where(filter=FieldFilter("user_id", "==", owner_user_id))
            .limit(2)
            .stream()
        )
        if len(users) != 1:
            return None
        user_data = users[0].to_dict()
        if not isinstance(user_data, dict):
            return None
        dataset_id = user_data.get("dataset_id")
        if (
            user_data.get("status") != "active"
            or not isinstance(dataset_id, str)
            or DATASET_ID.fullmatch(dataset_id) is None
        ):
            return None

        return VehicleRoute(
            user_id=owner_user_id,
            dataset_id=dataset_id,
            vehicle_id=vehicle_id,
            tesla_vehicle_id=_optional_string(vehicle_data.get("tesla_vehicle_id")),
            telemetry_config_version=_optional_string(vehicle_data.get("telemetry_config_version")),
            telemetry_config_hash=_optional_string(vehicle_data.get("telemetry_config_hash")),
        )


class BigQueryTelemetrySink:
    """Append deliveries without insert IDs so raw transport retries remain visible."""

    def __init__(
        self,
        client: bigquery.Client,
        project_id: str,
        quarantine_table: str,
        synthetic_table: str,
    ) -> None:
        self._client = client
        self._project_id = project_id
        self._quarantine_table = quarantine_table
        self._synthetic_table = synthetic_table

    def append_user(self, dataset_id: str, event: RawTelemetryEvent) -> None:
        self._append(f"{self._project_id}.{dataset_id}.{RAW_TELEMETRY_TABLE}", event.bigquery_row())

    def append_quarantine(self, event: RawTelemetryEvent, reason: str) -> None:
        row = event.bigquery_row()
        row["quarantine_reason"] = reason
        self._append(self._quarantine_table, row)

    def append_synthetic(
        self,
        event: RawTelemetryEvent,
        fixture_id: str,
        *,
        first_failure_recorded: bool,
    ) -> None:
        row = event.bigquery_row()
        row["fixture_id"] = fixture_id
        row["first_failure_recorded"] = first_failure_recorded
        self._append(self._synthetic_table, row)

    def _append(self, table: str, row: dict[str, object]) -> None:
        try:
            errors = self._client.insert_rows_json(table, [row], row_ids=[None], timeout=30)
        except Exception as exc:
            raise RetryableProcessingError("bigquery_append_failed") from exc
        if errors:
            raise RetryableProcessingError(_bigquery_rejection_category(errors))


class FirestoreFixtureRetryGate:
    """Cause one deliberate retry only for isolated Phase 7 synthetic fixtures."""

    def __init__(self, client: FirestoreClient) -> None:
        self._client = client

    def should_fail_first(self, fixture_id: str, *, now: datetime) -> bool:
        reference = self._client.collection(FIXTURE_RETRIES).document(
            sha256(fixture_id.encode()).hexdigest()
        )
        try:
            reference.create(
                {
                    "fixture_id": fixture_id,
                    "first_failure_at": now,
                    "expires_at": now + timedelta(days=1),
                    "created_at": firestore.SERVER_TIMESTAMP,
                }
            )
        except AlreadyExists:
            return False
        return True


def _optional_string(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _bigquery_rejection_category(errors: Sequence[Mapping[str, object]]) -> str:
    """Return safe schema/reason codes without logging rejected row values."""
    diagnostics: set[str] = set()
    for row_error in errors:
        details = row_error.get("errors")
        if not isinstance(details, list):
            continue
        for detail in details:
            if not isinstance(detail, dict):
                continue
            reason = detail.get("reason")
            location = detail.get("location")
            if not isinstance(reason, str) or BIGQUERY_DIAGNOSTIC.fullmatch(reason) is None:
                continue
            diagnostic = reason
            if isinstance(location, str) and BIGQUERY_DIAGNOSTIC.fullmatch(location) is not None:
                diagnostic = f"{reason}@{location}"
            diagnostics.add(diagnostic)
    suffix = ",".join(sorted(diagnostics)) or "unknown"
    return f"bigquery_append_rejected:{suffix}"
