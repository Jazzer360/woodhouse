"""BigQuery per-user ACL construction tests."""

from google.cloud import bigquery
from tesla_personal_platform.auth.bigquery_admin import restricted_service_account_access


def test_dataset_access_is_exact_minimal_and_idempotent() -> None:
    existing = [bigquery.AccessEntry("OWNER", "userByEmail", "operator@example.com")]

    once = restricted_service_account_access(
        existing, "gateway@example.iam", "processor@example.iam"
    )
    twice = restricted_service_account_access(once, "gateway@example.iam", "processor@example.iam")

    assert twice == once
    assert {(entry.role, entry.entity_type, entry.entity_id) for entry in twice} == {
        ("READER", "userByEmail", "gateway@example.iam"),
        ("WRITER", "userByEmail", "processor@example.iam"),
    }
