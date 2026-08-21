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
