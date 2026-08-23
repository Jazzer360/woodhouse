"""Static contracts for Tesla's official Fleet Telemetry receiver deployment."""

import json
from pathlib import Path

ROOT = Path(__file__).parents[1]


def test_edge_uses_pinned_official_receiver_and_no_custom_protocol() -> None:
    dockerfile = (ROOT / "services" / "telemetry-edge" / "Dockerfile").read_text()

    assert (
        "FROM tesla/fleet-telemetry@sha256:"
        "28c8b9e244b842a3d7443567cfa385b4db20cf533b8dee3411ce6fe540eb67e2" in dockerfile
    )
    assert ":latest" not in dockerfile
    assert "python" not in dockerfile.lower()


def test_edge_publishes_every_receiver_record_type_without_rate_filtering() -> None:
    config = json.loads((ROOT / "services" / "telemetry-edge" / "config.json").read_text())

    assert config["transmit_decoded_records"] is True
    assert config["rate_limit"]["enabled"] is False
    assert config["records"] == {
        "V": ["pubsub"],
        "alerts": ["pubsub"],
        "errors": ["pubsub"],
        "connectivity": ["pubsub"],
    }
    assert config["reliable_ack_sources"] == {
        "V": "pubsub",
        "alerts": "pubsub",
        "errors": "pubsub",
    }
    assert "pubsub" not in config
    assert config["monitoring"]["prometheus_metrics_port"] == 9090


def test_edge_has_no_vehicle_credentials_or_business_logic() -> None:
    source = "\n".join(
        path.read_text(errors="ignore")
        for path in (ROOT / "services" / "telemetry-edge").rglob("*")
        if path.is_file()
    ).lower()

    for forbidden in (
        "tesla_client_secret",
        "refresh_token",
        "command_private_key",
        "bigquery",
        "allowlist",
        "analytics",
    ):
        assert forbidden not in source


def test_vm_delivery_requires_digest_health_check_and_rollback() -> None:
    script = (ROOT / "infra" / "terraform" / "scripts" / "telemetry-edge-startup.sh").read_text()

    assert "telemetry-edge@sha256:" in script
    assert "http://127.0.0.1:8080/status" in script
    assert 'run_receiver "$PREVIOUS_IMAGE"' in script
    assert 'report_status "success:$DESIRED_COMMIT"' in script
    assert "--read-only" in script
    assert "--cap-drop=ALL" in script
    assert "metadata telemetry-edge-config" in script
    assert "src=$CONFIG_FILE,dst=/etc/fleet-telemetry/config.json,readonly" in script


def test_terraform_injects_the_deployment_project_into_receiver_config() -> None:
    compute = (ROOT / "infra" / "terraform" / "compute.tf").read_text()
    dockerfile = (ROOT / "services" / "telemetry-edge" / "Dockerfile").read_text()

    assert "telemetry-edge-config = jsonencode(merge(" in compute
    assert "gcp_project_id = var.project_id" in compute
    assert "COPY services/telemetry-edge/config.json" not in dockerfile
