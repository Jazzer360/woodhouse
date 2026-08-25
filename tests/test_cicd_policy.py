"""Static safety contract for PR validation and main-branch delivery."""

from pathlib import Path

ROOT = Path(__file__).parents[1]


def test_pr_build_never_deploys_production() -> None:
    source = (ROOT / "cloudbuild.pr.yaml").read_text(encoding="utf-8")

    assert "gcloud run services update" not in source
    assert "terraform apply" not in source


def test_terraform_owns_repository_triggers_without_the_github_connection() -> None:
    source = (ROOT / "infra" / "terraform" / "cloud_build.tf").read_text(encoding="utf-8")

    assert source.count('resource "google_cloudbuild_trigger"') == 6
    assert 'resource "google_cloudbuildv2_connection"' not in source
    assert 'resource "google_cloudbuildv2_repository"' not in source
    assert 'platform["cloud_build_validator"].id' in source
    assert source.count('platform["cloud_build_deployer"].id') == 4
    assert source.count('platform["analytics_view_reconciler"].id') == 1
    assert "COMMENTS_ENABLED_FOR_EXTERNAL_CONTRIBUTORS_ONLY" in source


def test_analytics_view_delivery_is_merge_triggered_and_scoped() -> None:
    build = (ROOT / "cloudbuild.analytics-views.yaml").read_text(encoding="utf-8")
    terraform = (ROOT / "infra" / "terraform" / "cloud_build.tf").read_text(encoding="utf-8")

    assert "scripts/admin/sync-analytics-views" in build
    assert "tpp-analytics-view-reconciler@$PROJECT_ID.iam.gserviceaccount.com" in build
    assert "terraform apply" not in build
    assert "gcloud run" not in build
    assert 'name               = "tpp-main-analytics-views"' in terraform
    assert '"packages/analytics/**"' in terraform
    assert '"packages/auth/**"' in terraform
    assert 'platform["analytics_view_reconciler"].id' in terraform


def test_main_delivery_uses_commit_tags_and_resolved_digests() -> None:
    source = (ROOT / "cloudbuild.main.yaml").read_text(encoding="utf-8")

    assert ":$COMMIT_SHA" in source
    assert "image_summary.digest" in source
    assert "@$$digest" in source
    assert ":latest" not in source


def test_main_delivery_allowlists_cloud_run_and_digest_pinned_edge() -> None:
    source = (ROOT / "cloudbuild.main.yaml").read_text(encoding="utf-8")

    assert source.count("Unsupported deploy service") == 2
    assert "mcp-gateway)" in source
    assert "telemetry-processor)" in source
    assert "telemetry-edge)" in source
    assert "certificate-renewer)" in source
    assert "compute instances add-metadata" in source
    assert "compute instances reset" in source
    assert "get-guest-attributes" in source
    add_metadata_start = source.index("gcloud compute instances add-metadata")
    add_metadata_end = source.index("# The metadata mutation", add_metadata_start)
    assert "--async" not in source[add_metadata_start:add_metadata_end]
    reset_start = source.index("gcloud compute instances reset")
    reset_end = source.index('reset_attempt="$$(($$reset_attempt + 1))"', reset_start)
    assert "--async" not in source[reset_start:reset_end]
    assert "terraform apply" not in source


def test_certificate_renewer_delivery_updates_a_job_by_exact_digest() -> None:
    source = (ROOT / "cloudbuild.main.yaml").read_text(encoding="utf-8")

    assert 'job="tpp-telemetry-cert-renewer"' in source
    assert 'gcloud run jobs update "$$job"' in source
    assert '--image="$$digest_image"' in source
    assert "deployed_service=certificate-renewer" in source


def test_gateway_deploy_preserves_command_proxy_sidecar() -> None:
    source = (ROOT / "cloudbuild.main.yaml").read_text(encoding="utf-8")

    gateway_start = source.index("mcp-gateway)\n            # Preserve")
    gateway_end = source.index("telemetry-processor)", gateway_start)
    gateway_deploy = source[gateway_start:gateway_end]

    assert "--container=application" in gateway_deploy
    assert "Vehicle Command Proxy sidecar" in gateway_deploy


def test_terraform_preserves_delivery_owned_revision_metadata() -> None:
    source = (ROOT / "infra" / "terraform" / "cloud_run.tf").read_text(encoding="utf-8")

    assert "template[0].containers[0].image" in source
    assert "template[0].labels" in source
    assert "tpp-deployed-commit" in source


def test_main_delivery_waits_for_readiness_and_smoke_checks_gateway() -> None:
    source = (ROOT / "cloudbuild.main.yaml").read_text(encoding="utf-8")

    assert "status.latestReadyRevisionName" in source
    assert 'item["type"] == "Ready"' in source
    assert 'item["name"] == "application"' in source
    assert 'test "$$deployed_image" = "$$digest_image"' in source
    assert "value(spec.template.spec.template.spec.containers[0].image)" in source
    assert "value(template.template.containers[0].image)" not in source
    assert '"$$service_url/health"' in source
    assert "deployed_service=${_SERVICE}" in source
