"""Static safety contract for PR validation and main-branch delivery."""

from pathlib import Path

ROOT = Path(__file__).parents[1]


def test_pr_build_never_deploys_production() -> None:
    source = (ROOT / "cloudbuild.pr.yaml").read_text(encoding="utf-8")

    assert "gcloud run services update" not in source
    assert "terraform apply" not in source


def test_terraform_owns_repository_triggers_without_the_github_connection() -> None:
    source = (ROOT / "infra" / "terraform" / "cloud_build.tf").read_text(encoding="utf-8")

    assert source.count('resource "google_cloudbuild_trigger"') == 3
    assert 'resource "google_cloudbuildv2_connection"' not in source
    assert 'resource "google_cloudbuildv2_repository"' not in source
    assert 'platform["cloud_build_validator"].id' in source
    assert source.count('platform["cloud_build_deployer"].id') == 2
    assert "COMMENTS_ENABLED_FOR_EXTERNAL_CONTRIBUTORS_ONLY" in source


def test_main_delivery_uses_commit_tags_and_resolved_digests() -> None:
    source = (ROOT / "cloudbuild.main.yaml").read_text(encoding="utf-8")

    assert ":$COMMIT_SHA" in source
    assert "image_summary.digest" in source
    assert "@$$digest" in source
    assert ":latest" not in source


def test_main_delivery_allowlists_only_current_cloud_run_services() -> None:
    source = (ROOT / "cloudbuild.main.yaml").read_text(encoding="utf-8")

    assert source.count("Unsupported deploy service") == 2
    assert "mcp-gateway)" in source
    assert "telemetry-processor)" in source
    assert "telemetry-edge)" not in source
    assert "terraform apply" not in source


def test_gateway_deploy_preserves_command_proxy_sidecar() -> None:
    source = (ROOT / "cloudbuild.main.yaml").read_text(encoding="utf-8")

    gateway_start = source.index("mcp-gateway)\n            # Preserve")
    gateway_end = source.index("telemetry-processor)", gateway_start)
    gateway_deploy = source[gateway_start:gateway_end]

    assert "--container=application" in gateway_deploy
    assert "Vehicle Command Proxy sidecar" in gateway_deploy


def test_main_delivery_waits_for_readiness_and_smoke_checks_gateway() -> None:
    source = (ROOT / "cloudbuild.main.yaml").read_text(encoding="utf-8")

    assert "status.latestReadyRevisionName" in source
    assert 'item["type"] == "Ready"' in source
    assert 'item["name"] == "application"' in source
    assert 'test "$$deployed_image" = "$$digest_image"' in source
    assert '"$$service_url/health"' in source
    assert "deployed_service=${_SERVICE}" in source
