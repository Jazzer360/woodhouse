"""Static guardrails for the Phase 2 Terraform baseline."""

from pathlib import Path

ROOT = Path(__file__).parents[1]
TERRAFORM_ROOT = ROOT / "infra" / "terraform"


def terraform_source() -> str:
    """Return tracked-style Terraform configuration without generated provider files."""
    return "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted(TERRAFORM_ROOT.rglob("*.tf"))
        if ".terraform" not in path.parts
    )


def test_terraform_does_not_grant_basic_owner_or_editor_roles() -> None:
    source = terraform_source()

    assert "roles/owner" not in source
    assert "roles/editor" not in source


def test_terraform_creates_secret_containers_without_values() -> None:
    source = terraform_source()

    assert 'resource "google_secret_manager_secret"' in source
    assert "google_secret_manager_secret_version" not in source


def test_per_user_bigquery_datasets_remain_outside_shared_terraform() -> None:
    source = terraform_source()

    assert "tesla_u_" not in source


def variable_block(source: str, name: str) -> str:
    """Return a simple top-level variable block from the repository HCL."""
    marker = f'variable "{name}" {{'
    return source.split(marker, maxsplit=1)[1].split("\n}", maxsplit=1)[0]


def test_target_project_and_state_bucket_require_explicit_input() -> None:
    shared_variables = (TERRAFORM_ROOT / "variables.tf").read_text(encoding="utf-8")
    bootstrap = (TERRAFORM_ROOT / "bootstrap" / "main.tf").read_text(encoding="utf-8")

    assert "default" not in variable_block(shared_variables, "project_id")
    assert "default" not in variable_block(bootstrap, "project_id")
    assert "default" not in variable_block(bootstrap, "state_bucket_name")


def test_backend_declaration_is_isolated_for_speculative_plans() -> None:
    backend = (TERRAFORM_ROOT / "backend.tf").read_text(encoding="utf-8")
    versions = (TERRAFORM_ROOT / "versions.tf").read_text(encoding="utf-8")

    assert 'backend "gcs" {}' in backend
    assert "backend" not in versions


def test_cloud_build_speculative_plan_copies_the_complete_root() -> None:
    cloud_build = (ROOT / "cloudbuild.pr.yaml").read_text(encoding="utf-8")

    assert "cp -R infra/terraform /tmp/tpp-terraform-plan" in cloud_build
    assert "rm /tmp/tpp-terraform-plan/backend.tf" in cloud_build
    assert "sed -i" not in cloud_build
