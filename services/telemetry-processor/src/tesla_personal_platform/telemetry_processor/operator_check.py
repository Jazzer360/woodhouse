"""Safe Phase 7 synthetic end-to-end verification."""

import argparse
import json
import re
import time
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from google.cloud import bigquery
from google.cloud.bigquery import ScalarQueryParameter
from google.cloud.pubsub_v1 import PublisherClient  # type: ignore[import-untyped]

RAW_FIXTURE_TOPIC = "tpp-raw-telemetry"
SYSTEM_DATASET = "tesla_system_quarantine"
SYNTHETIC_TABLE = "raw_synthetic_telemetry"
QUARANTINE_TABLE = "raw_unknown_telemetry"
UNKNOWN_VIN = "SYNTHETIC-NON-VEHICLE-UNKNOWN-VIN"
PROJECT_ID = re.compile(r"^[a-z][a-z0-9-]{4,28}[a-z0-9]$")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Verify the deployed telemetry path using only isolated non-vehicle fixtures"
    )
    parser.add_argument("--project-id", required=True)
    parser.add_argument("--timeout-seconds", type=int, default=600)
    parser.add_argument(
        "--confirm-non-vehicle-fixtures",
        action="store_true",
        help="Required acknowledgement that this writes only marked system fixtures",
    )
    return parser


def _payload(fixture_id: str, source_timestamp: datetime) -> bytes:
    return json.dumps(
        {
            "createdAt": source_timestamp.isoformat().replace("+00:00", "Z"),
            "fixtureId": fixture_id,
            "fixtureType": "phase7-non-vehicle",
            "vin": "SYNTHETIC-NON-VEHICLE",
            "data": [{"key": "SyntheticPipelineCheck", "value": {"booleanValue": True}}],
        },
        separators=(",", ":"),
        sort_keys=True,
    ).encode()


def _publish_synthetic(
    publisher: PublisherClient,
    topic_path: str,
    fixture_id: str,
    source_timestamp: datetime,
    *,
    fail_first: bool = False,
) -> str:
    attributes = {
        "tpp_synthetic_fixture": "phase7",
        "tpp_fixture_id": fixture_id,
        "txtype": "synthetic",
        "txid": fixture_id,
    }
    if fail_first:
        attributes["tpp_fixture_fail_first"] = "true"
    message_id = publisher.publish(
        topic_path, _payload(fixture_id, source_timestamp), **attributes
    ).result(timeout=30)
    if not isinstance(message_id, str):
        raise RuntimeError("Pub/Sub did not return a message ID")
    return message_id


def _publish_unknown(
    publisher: PublisherClient,
    topic_path: str,
    fixture_id: str,
    source_timestamp: datetime,
) -> str:
    payload = json.dumps(
        {
            "createdAt": source_timestamp.isoformat().replace("+00:00", "Z"),
            "fixtureId": fixture_id,
            "fixtureType": "phase7-unknown-vin",
            "vin": UNKNOWN_VIN,
            "data": [],
        },
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    message_id = publisher.publish(
        topic_path,
        payload,
        vin=UNKNOWN_VIN,
        receivedat=str(int(datetime.now(UTC).timestamp() * 1000)),
        txtype="V",
        txid=fixture_id,
        version="0",
        device_client_version="phase7-synthetic",
    ).result(timeout=30)
    if not isinstance(message_id, str):
        raise RuntimeError("Pub/Sub did not return a message ID")
    return message_id


def _count_fixture(
    client: bigquery.Client,
    table: str,
    field: str,
    value: str,
    earliest: datetime,
) -> int:
    # Table and field are fixed internal identifiers assembled only after a
    # strict GCP project-ID check; query values remain parameterized.
    query = f"""
        SELECT COUNT(*) AS row_count
        FROM `{table}`
        WHERE source_timestamp >= @earliest AND {field} = @value
    """  # noqa: S608
    job = client.query(
        query,
        job_config=bigquery.QueryJobConfig(
            query_parameters=[
                ScalarQueryParameter("earliest", "TIMESTAMP", earliest),
                ScalarQueryParameter("value", "STRING", value),
            ],
            maximum_bytes_billed=10_000_000,
            use_query_cache=False,
        ),
    )
    rows = list(job.result(timeout=30, max_results=1))
    return int(rows[0].row_count) if rows else 0


def _count_retry_proof(
    client: bigquery.Client,
    table: str,
    fixture_id: str,
    earliest: datetime,
) -> int:
    # The table is a fixed system table under a strictly validated project ID.
    query = f"""
        SELECT COUNT(*) AS row_count
        FROM `{table}`
        WHERE source_timestamp >= @earliest
          AND fixture_id = @fixture_id
          AND first_failure_recorded IS TRUE
    """  # noqa: S608
    job = client.query(
        query,
        job_config=bigquery.QueryJobConfig(
            query_parameters=[
                ScalarQueryParameter("earliest", "TIMESTAMP", earliest),
                ScalarQueryParameter("fixture_id", "STRING", fixture_id),
            ],
            maximum_bytes_billed=10_000_000,
            use_query_cache=False,
        ),
    )
    rows = list(job.result(timeout=30, max_results=1))
    return int(rows[0].row_count) if rows else 0


def _wait_for_proof(
    bigquery_client: bigquery.Client,
    project_id: str,
    duplicate_fixture: str,
    retry_fixture: str,
    unknown_message_id: str,
    earliest: datetime,
    timeout_seconds: int,
) -> dict[str, object]:
    synthetic_table = f"{project_id}.{SYSTEM_DATASET}.{SYNTHETIC_TABLE}"
    quarantine_table = f"{project_id}.{SYSTEM_DATASET}.{QUARANTINE_TABLE}"
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        duplicate_count = _count_fixture(
            bigquery_client,
            synthetic_table,
            "fixture_id",
            duplicate_fixture,
            earliest,
        )
        retry_count = _count_fixture(
            bigquery_client,
            synthetic_table,
            "fixture_id",
            retry_fixture,
            earliest,
        )
        unknown_count = _count_fixture(
            bigquery_client,
            quarantine_table,
            "pubsub_message_id",
            unknown_message_id,
            earliest,
        )
        retry_proof_count = _count_retry_proof(
            bigquery_client,
            synthetic_table,
            retry_fixture,
            earliest,
        )
        if (
            duplicate_count >= 2
            and retry_count >= 1
            and unknown_count >= 1
            and retry_proof_count >= 1
        ):
            return {
                "duplicate_rows_preserved": duplicate_count,
                "retry_failure_recorded": True,
                "retry_row_persisted": retry_count,
                "unknown_vin_quarantined": unknown_count,
            }
        time.sleep(5)
    raise TimeoutError("telemetry verification evidence did not arrive before timeout")


def main() -> int:
    parser = _parser()
    arguments = parser.parse_args()
    if not arguments.confirm_non_vehicle_fixtures:
        parser.error("--confirm-non-vehicle-fixtures is required")
    if arguments.timeout_seconds < 30 or arguments.timeout_seconds > 1800:
        parser.error("--timeout-seconds must be between 30 and 1800")

    project_id = str(arguments.project_id)
    if PROJECT_ID.fullmatch(project_id) is None:
        parser.error("--project-id must be a valid lowercase GCP project ID")
    run_id = uuid4().hex[:16]
    duplicate_fixture = f"phase7_duplicate-{run_id}"
    retry_fixture = f"phase7_retry-{run_id}"
    unknown_fixture = f"phase7_unknown-{run_id}"
    source_timestamp = datetime.now(UTC)
    earliest = source_timestamp - timedelta(minutes=5)
    publisher = PublisherClient()
    topic_path = publisher.topic_path(project_id, RAW_FIXTURE_TOPIC)

    duplicate_message_ids = [
        _publish_synthetic(publisher, topic_path, duplicate_fixture, source_timestamp)
        for _ in range(2)
    ]
    retry_message_id = _publish_synthetic(
        publisher,
        topic_path,
        retry_fixture,
        source_timestamp,
        fail_first=True,
    )
    unknown_message_id = _publish_unknown(publisher, topic_path, unknown_fixture, source_timestamp)

    proof = _wait_for_proof(
        bigquery.Client(project=project_id),
        project_id,
        duplicate_fixture,
        retry_fixture,
        unknown_message_id,
        earliest,
        int(arguments.timeout_seconds),
    )
    print(
        json.dumps(
            {
                "outcome": "passed",
                "run_id": run_id,
                "system_destination": f"{project_id}.{SYSTEM_DATASET}",
                "published_message_ids": {
                    "duplicates": duplicate_message_ids,
                    "retry": retry_message_id,
                    "unknown": unknown_message_id,
                },
                **proof,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
