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
    assert 'httpRequest.requestUrl=~"/(auth|oauth)/callback\\\\?"' in monitoring


def test_pubsub_oidc_audience_is_path_scoped_and_accepted_by_cloud_run() -> None:
    pubsub = (TERRAFORM_ROOT / "pubsub.tf").read_text(encoding="utf-8")
    cloud_run = (TERRAFORM_ROOT / "cloud_run.tf").read_text(encoding="utf-8")

    assert "push_endpoint = local.telemetry_processor_push_endpoint" in pubsub
    assert "audience              = local.telemetry_processor_push_audience" in pubsub
    assert 'telemetry_processor_push_audience = "https://' in pubsub
    assert '/pubsub/push"' in pubsub
    assert "custom_audiences" in cloud_run
    assert "PUBSUB_PUSH_AUDIENCE" in cloud_run
    assert "= local.telemetry_processor_push_audience" in cloud_run


def test_mcp_external_route_is_fail_closed_until_oidc_is_configured() -> None:
    variables = (TERRAFORM_ROOT / "variables.tf").read_text(encoding="utf-8")
    cloud_run = (TERRAFORM_ROOT / "cloud_run.tf").read_text(encoding="utf-8")

    external_access = variable_block(variables, "enable_mcp_external_access")
    assert "default     = false" in external_access
    assert "var.enable_platform_oidc ||" in cloud_run
    assert "var.oidc_audience == null ? false" in cloud_run
    assert 'member   = "allUsers"' in cloud_run


def test_platform_oidc_is_opt_in_and_browser_secret_is_secret_manager_only() -> None:
    variables = (TERRAFORM_ROOT / "variables.tf").read_text(encoding="utf-8")
    cloud_run = (TERRAFORM_ROOT / "cloud_run.tf").read_text(encoding="utf-8")
    secrets = (TERRAFORM_ROOT / "secrets.tf").read_text(encoding="utf-8")

    platform_oidc = variable_block(variables, "enable_platform_oidc")
    assert "default     = false" in platform_oidc
    assert "PLATFORM_OIDC_CLIENT_SECRET" in cloud_run
    assert (
        'secret  = google_secret_manager_secret.platform["platform_oidc_client_secret"].secret_id'
        in cloud_run
    )
    assert 'platform_oidc_client_secret  = "platform-oidc-client-secret"' in secrets
    assert 'var.enable_platform_oidc ? ["platform_oidc_client_secret"] : []' in secrets
    assert "google_secret_manager_secret_version" not in secrets


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


def test_command_proxy_is_digest_pinned_non_ingress_and_kept_off_telemetry_edge() -> None:
    variables = (TERRAFORM_ROOT / "variables.tf").read_text(encoding="utf-8")
    cloud_run = (TERRAFORM_ROOT / "cloud_run.tf").read_text(encoding="utf-8")
    secrets = (TERRAFORM_ROOT / "secrets.tf").read_text(encoding="utf-8")
    compute = (TERRAFORM_ROOT / "compute.tf").read_text(encoding="utf-8")

    assert "enable_tesla_command_proxy" in variables
    assert '"^tesla/vehicle-command@sha256:[0-9a-f]{64}$"' in variables
    assert 'value = "0.0.0.0"' in cloud_run
    assert 'value = "4443"' in cloud_run
    assert "container_port = 8080" in cloud_run
    assert "container_port = 4443" not in cloud_run
    assert 'name  = "tesla-command-proxy"' in cloud_run
    assert 'name       = "tesla-command-key"' in cloud_run
    assert 'name       = "command-proxy-ca"' in cloud_run
    assert "var.enable_tesla_command_proxy ? toset" in secrets
    assert "tesla-command-private-key" not in compute


def test_telemetry_trust_profile_is_shared_without_giving_renewer_tesla_secrets() -> None:
    variables = (TERRAFORM_ROOT / "variables.tf").read_text(encoding="utf-8")
    cloud_run = (TERRAFORM_ROOT / "cloud_run.tf").read_text(encoding="utf-8")
    renewal = (TERRAFORM_ROOT / "certificate_renewal.tf").read_text(encoding="utf-8")
    secrets = (TERRAFORM_ROOT / "secrets.tf").read_text(encoding="utf-8")

    assert "enable_fleet_telemetry_control" in variables
    assert "telemetry_trust_profile_id" in variables
    assert 'telemetry_server_ca_profile  = "telemetry-server-ca-profile"' in secrets
    assert 'telemetry_trust_readiness    = "telemetry-trust-readiness"' in secrets
    assert 'TELEMETRY_SERVER_CA_PEM = "telemetry_server_ca_profile"' in cloud_run
    assert 'name  = "TELEMETRY_TRUST_PROFILE_SECRET"' in renewal
    assert 'name  = "TELEMETRY_TRUST_READINESS_SECRET"' in renewal
    renewer_start = secrets.index(
        'resource "google_secret_manager_secret_iam_member" "certificate_renewer_accessor"'
    )
    renewer_end = secrets.index(
        'resource "google_secret_manager_secret_iam_member" "certificate_renewer_version_adder"'
    )
    renewer_access = secrets[renewer_start:renewer_end]
    assert "telemetry_server_ca_profile" in renewer_access
    assert "telemetry_trust_readiness" in renewer_access
    assert "tesla_client_secret" not in renewer_access
    assert "tesla_command_private_key" not in renewer_access


def test_gateway_diagnostics_receive_the_same_pinned_receiver_version() -> None:
    cloud_run = (TERRAFORM_ROOT / "cloud_run.tf").read_text(encoding="utf-8")

    assert "TELEMETRY_RECEIVER_VERSION      = var.fleet_telemetry_receiver_version" in cloud_run


def test_telemetry_edge_has_only_receiver_topics_and_gated_tls_secrets() -> None:
    pubsub = (TERRAFORM_ROOT / "pubsub.tf").read_text(encoding="utf-8")
    secrets = (TERRAFORM_ROOT / "secrets.tf").read_text(encoding="utf-8")
    iam = (TERRAFORM_ROOT / "iam.tf").read_text(encoding="utf-8")

    assert 'toset(["V", "alerts", "connectivity", "errors"])' in pubsub
    edge_binding = pubsub[
        pubsub.index(
            'resource "google_pubsub_topic_iam_member" "telemetry_edge_publisher"'
        ) : pubsub.index(
            'resource "google_pubsub_topic_iam_member" "telemetry_operator_fixture_publisher"'
        )
    ]
    assert "google_pubsub_topic.fleet_raw_telemetry" in edge_binding
    assert "google_pubsub_topic.raw_telemetry" not in edge_binding
    assert "var.enable_telemetry_edge_delivery ? toset" in secrets
    edge_secret_binding = secrets[
        secrets.index(
            'resource "google_secret_manager_secret_iam_member" "telemetry_edge_tls_accessor"'
        ) : secrets.index('resource "google_secret_manager_secret" "platform"')
    ]
    assert '"telemetry_edge_tls_cert"' in edge_secret_binding
    assert '"telemetry_edge_tls_key"' in edge_secret_binding
    assert '"telemetry_edge_tls_release"' in edge_secret_binding
    assert "tesla_client_secret" not in edge_secret_binding
    assert 'permissions = ["pubsub.topics.get"]' in iam
    edge_act_as_start = iam.index(
        'resource "google_service_account_iam_member" "deployer_edge_runtime_user"'
    )
    edge_act_as_end = iam.index(
        'resource "google_service_account_iam_member" "cloud_build_identity_token_creator"'
    )
    edge_act_as = iam[edge_act_as_start:edge_act_as_end]
    assert "var.enable_telemetry_edge_delivery ? 1 : 0" in edge_act_as
    assert 'platform["telemetry_edge"]' in edge_act_as
    assert 'role               = "roles/iam.serviceAccountUser"' in edge_act_as
    assert 'platform["cloud_build_deployer"]' in edge_act_as


def test_certificate_renewal_is_isolated_scheduled_and_fail_closed() -> None:
    variables = (TERRAFORM_ROOT / "variables.tf").read_text(encoding="utf-8")
    renewal = (TERRAFORM_ROOT / "certificate_renewal.tf").read_text(encoding="utf-8")
    monitoring = (TERRAFORM_ROOT / "monitoring.tf").read_text(encoding="utf-8")
    secrets = (TERRAFORM_ROOT / "secrets.tf").read_text(encoding="utf-8")
    iam = (TERRAFORM_ROOT / "iam.tf").read_text(encoding="utf-8")

    assert "default     = false" in variable_block(
        variables, "enable_telemetry_certificate_automation"
    )
    assert "default     = true" in variable_block(
        variables, "telemetry_certificate_schedule_paused"
    )
    assert 'default     = "17 5,17 * * *"' in variable_block(
        variables, "telemetry_certificate_renewal_schedule"
    )
    assert 'resource "google_cloud_run_v2_job" "telemetry_certificate_renewer"' in renewal
    assert 'resource "google_cloud_scheduler_job" "telemetry_certificate_renewal"' in renewal
    assert "max_retries           = 0" in renewal
    assert "Twice-daily unattended ACME check" in renewal
    assert "paused           = var.telemetry_certificate_schedule_paused" in renewal
    assert "length(var.monitoring_notification_channels) > 0" in renewal
    assert (
        "Unpausing telemetry certificate renewal requires at least one monitoring "
        "notification channel."
    ) in renewal
    assert 'secret  = google_secret_manager_secret.platform["cloudflare_dns_api_token"]' in renewal
    renewer_accessor = secrets[
        secrets.index(
            'resource "google_secret_manager_secret_iam_member" "certificate_renewer_accessor"'
        ) : secrets.index(
            'resource "google_secret_manager_secret_iam_member" "certificate_renewer_version_adder"'
        )
    ]
    assert '"telemetry_edge_tls_cert"' in renewer_accessor
    assert '"telemetry_edge_tls_key"' not in renewer_accessor
    assert 'role      = "roles/secretmanager.secretVersionAdder"' in secrets
    assert 'role_id     = "tppCertificateEdgeReloader"' in iam
    assert '"compute.instances.reset"' in iam
    assert 'role    = "roles/cloudscheduler.serviceAgent"' in iam
    assert (
        "compute.instances.setMetadata"
        not in iam[
            iam.index(
                'resource "google_project_iam_custom_role" "certificate_renewer_edge_reloader"'
            ) : iam.index(
                'resource "google_project_iam_member" "certificate_renewer_edge_reloader"'
            )
        ]
    )
    success_metric = monitoring[
        monitoring.index(
            'resource "google_logging_metric" "telemetry_certificate_check_success"'
        ) : monitoring.index(
            'resource "google_logging_metric" "telemetry_certificate_check_failure"'
        )
    ]
    failure_metric = monitoring[
        monitoring.index(
            'resource "google_logging_metric" "telemetry_certificate_check_failure"'
        ) : monitoring.index(
            'resource "google_monitoring_alert_policy" "telemetry_certificate_check_missing"'
        )
    ]
    assert "count =" not in success_metric
    assert "count =" not in failure_metric
    assert 'duration = "82800s"' in monitoring
    assert 'duration = "172800s"' not in monitoring


def test_abandoned_oauth_states_and_sessions_have_firestore_ttl() -> None:
    firestore = (TERRAFORM_ROOT / "firestore.tf").read_text(encoding="utf-8")

    for collection in (
        "tesla_oauth_states",
        "platform_login_states",
        "platform_web_sessions",
    ):
        assert f'collection = "{collection}"' in firestore
    assert firestore.count('field      = "expires_at"') == 4
    assert firestore.count("ttl_config {}") == 4


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
    assert '"bigquery.tables.create"' in custom_role
    assert '"bigquery.tables.get"' in custom_role
    assert '"bigquery.tables.update"' in custom_role
    assert '"bigquery.tables.delete"' not in custom_role
    assert 'account_id   = "tpp-dataset-owner"' in service_accounts
    impersonation = iam[
        iam.index('resource "google_service_account_iam_member" "user_admin_impersonator"') :
    ]
    assert 'platform["dataset_owner"]' not in impersonation


def test_analytics_view_reconciler_has_narrow_metadata_and_allowlist_permissions() -> None:
    source = terraform_source()
    iam = (TERRAFORM_ROOT / "iam.tf").read_text(encoding="utf-8")
    service_accounts = (TERRAFORM_ROOT / "service_accounts.tf").read_text(encoding="utf-8")

    assert "google_service_account_key" not in source
    assert 'account_id   = "tpp-analytics-view-reconciler"' in service_accounts
    role_start = iam.index('resource "google_project_iam_custom_role" "analytics_view_reconciler"')
    role_end = iam.index('resource "google_project_iam_member" "analytics_view_reconciler"')
    role = iam[role_start:role_end]
    assert '"datastore.entities.get"' in role
    assert '"datastore.entities.list"' in role
    assert '"bigquery.datasets.create"' not in role
    assert '"bigquery.datasets.get"' in role
    assert '"bigquery.datasets.update"' in role
    assert '"bigquery.tables.create"' in role
    assert '"bigquery.tables.delete"' in role
    assert '"bigquery.tables.get"' in role
    assert '"bigquery.tables.list"' in role
    assert '"bigquery.tables.update"' in role
    assert "bigquery.jobs" not in role
    assert "bigquery.tables.getData" not in role
    assert (
        '"analytics_view_reconciler"'
        in iam[
            iam.index(
                'resource "google_service_account_iam_member" "cloud_build_identity_token_creator"'
            ) : iam.index('resource "google_project_iam_member" "admin_iap_tunnel"')
        ]
    )
