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
    """Return a top-level variable block while preserving nested HCL blocks."""
    marker = f'variable "{name}" {{'
    start = source.index(marker)
    depth = 0

    for index in range(start, len(source)):
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                return source[start : index + 1]

    raise ValueError(f'Unclosed variable block: "{name}"')


def test_variable_block_preserves_nested_blocks() -> None:
    source = """variable "first" {
  validation {
    condition = true
  }
}

variable "second" {
  default = "must-not-leak"
}
"""

    assert "must-not-leak" not in variable_block(source, "first")


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


def test_oauth_callback_request_urls_are_excluded_from_cloud_logging() -> None:
    monitoring = (TERRAFORM_ROOT / "monitoring.tf").read_text(encoding="utf-8")

    assert 'resource "google_logging_project_exclusion"' in monitoring
    assert 'log_id("run.googleapis.com/requests")' in monitoring
    assert 'httpRequest.requestUrl=~"/oauth/callback\\\\?"' in monitoring


def test_pubsub_oidc_audience_matches_the_exact_push_endpoint() -> None:
    pubsub = (TERRAFORM_ROOT / "pubsub.tf").read_text(encoding="utf-8")

    assert "push_endpoint = local.telemetry_processor_push_endpoint" in pubsub
    assert "audience              = local.telemetry_processor_push_endpoint" in pubsub


def test_mcp_external_route_is_fail_closed_until_oidc_is_configured() -> None:
    variables = (TERRAFORM_ROOT / "variables.tf").read_text(encoding="utf-8")
    cloud_run = (TERRAFORM_ROOT / "cloud_run.tf").read_text(encoding="utf-8")

    external_access = variable_block(variables, "enable_mcp_external_access")
    assert "default     = false" in external_access
    assert "var.oidc_audience == null ? false" in cloud_run
    assert 'member   = "allUsers"' in cloud_run


def test_tesla_onboarding_is_fail_closed_and_does_not_inject_private_key() -> None:
    variables = (TERRAFORM_ROOT / "variables.tf").read_text(encoding="utf-8")
    cloud_run = (TERRAFORM_ROOT / "cloud_run.tf").read_text(encoding="utf-8")

    onboarding = variable_block(variables, "enable_tesla_onboarding")
    assert "default     = false" in onboarding
    assert "TESLA_CLIENT_SECRET" in cloud_run
    assert "TESLA_PUBLIC_KEY_PEM" in cloud_run
    assert "TESLA_TOKEN_ENCRYPTION_KEY" in cloud_run
    assert "TESLA_COMMAND_PRIVATE_KEY" not in cloud_run
    secrets = (TERRAFORM_ROOT / "secrets.tf").read_text(encoding="utf-8")
    gateway_start = secrets.index(
        'resource "google_secret_manager_secret_iam_member" "mcp_gateway_accessor"'
    )
    gateway_end = secrets.index(
        'resource "google_secret_manager_secret_iam_member" "partner_admin_accessor"'
    )
    gateway_access = secrets[gateway_start:gateway_end]
    assert '"tesla_command_private_key"' not in gateway_access


def test_abandoned_tesla_oauth_states_have_firestore_ttl() -> None:
    firestore = (TERRAFORM_ROOT / "firestore.tf").read_text(encoding="utf-8")

    assert 'collection = "tesla_oauth_states"' in firestore
    assert 'field      = "expires_at"' in firestore
    assert "ttl_config {}" in firestore


def test_partner_admin_is_keyless_and_has_no_project_role() -> None:
    iam = (TERRAFORM_ROOT / "iam.tf").read_text(encoding="utf-8")
    secrets = (TERRAFORM_ROOT / "secrets.tf").read_text(encoding="utf-8")
    service_accounts = (TERRAFORM_ROOT / "service_accounts.tf").read_text(encoding="utf-8")

    assert 'account_id   = "tpp-partner-admin"' in service_accounts
    project_iam = iam[: iam.index('resource "google_project_iam_member" "platform"')]
    assert 'platform["partner_admin"]' not in project_iam
    assert 'resource "google_service_account_iam_member" "partner_admin_impersonator"' in iam
    assert 'resource "google_secret_manager_secret_iam_member" "partner_admin_accessor"' in secrets


def test_user_admin_has_no_key_and_only_dataset_provisioning_permissions() -> None:
    source = terraform_source()
    iam = (TERRAFORM_ROOT / "iam.tf").read_text(encoding="utf-8")
    service_accounts = (TERRAFORM_ROOT / "service_accounts.tf").read_text(encoding="utf-8")

    assert "google_service_account_key" not in source
    custom_role_start = iam.index(
        'resource "google_project_iam_custom_role" "user_dataset_provisioner"'
    )
    custom_role_end = iam.index(
        'resource "google_project_iam_member" "user_admin_dataset_provisioner"'
    )
    custom_role = iam[custom_role_start:custom_role_end]
    assert "roles/bigquery.user" not in iam
    assert '"bigquery.datasets.create"' in custom_role
    assert '"bigquery.datasets.get"' in custom_role
    assert '"bigquery.datasets.update"' in custom_role
    assert "bigquery.jobs" not in custom_role
    assert "bigquery.tables" not in custom_role
    assert 'account_id   = "tpp-dataset-owner"' in service_accounts
    impersonation = iam[
        iam.index('resource "google_service_account_iam_member" "user_admin_impersonator"') :
    ]
    assert 'platform["dataset_owner"]' not in impersonation
