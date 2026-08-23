locals {
  secret_containers = {
    mcp_auth_signing_key         = "mcp-auth-signing-key"
    platform_oidc_client_secret  = "platform-oidc-client-secret"
    tesla_client_secret          = "tesla-client-secret"
    tesla_command_private_key    = "tesla-command-private-key"
    tesla_command_proxy_tls_cert = "tesla-command-proxy-tls-cert"
    tesla_command_proxy_tls_key  = "tesla-command-proxy-tls-key"
    tesla_command_public_key     = "tesla-command-public-key"
    tesla_token_encryption_key   = "tesla-token-encryption-key"
    telemetry_edge_tls_cert      = "telemetry-edge-tls-cert"
    telemetry_edge_tls_key       = "telemetry-edge-tls-key"
    telemetry_edge_tls_release   = "telemetry-edge-tls-release"
    telemetry_acme_state         = "telemetry-acme-state"
    cloudflare_dns_api_token     = "cloudflare-dns-api-token"
    webhook_hmac_key             = "webhook-hmac-key"
  }
}

resource "google_secret_manager_secret_iam_member" "telemetry_edge_tls_accessor" {
  for_each = var.enable_telemetry_edge_delivery ? toset([
    "telemetry_edge_tls_cert",
    "telemetry_edge_tls_key",
    "telemetry_edge_tls_release",
  ]) : toset([])

  project   = var.project_id
  secret_id = google_secret_manager_secret.platform[each.value].secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.platform["telemetry_edge"].email}"
}

resource "google_secret_manager_secret_iam_member" "certificate_renewer_accessor" {
  for_each = var.enable_telemetry_certificate_automation ? toset([
    "cloudflare_dns_api_token",
    "telemetry_acme_state",
    "telemetry_edge_tls_release",
  ]) : toset([])

  project   = var.project_id
  secret_id = google_secret_manager_secret.platform[each.value].secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.platform["certificate_renewer"].email}"
}

resource "google_secret_manager_secret_iam_member" "certificate_renewer_version_adder" {
  for_each = var.enable_telemetry_certificate_automation ? toset([
    "telemetry_acme_state",
    "telemetry_edge_tls_cert",
    "telemetry_edge_tls_key",
    "telemetry_edge_tls_release",
  ]) : toset([])

  project   = var.project_id
  secret_id = google_secret_manager_secret.platform[each.value].secret_id
  role      = "roles/secretmanager.secretVersionAdder"
  member    = "serviceAccount:${google_service_account.platform["certificate_renewer"].email}"
}

resource "google_secret_manager_secret" "platform" {
  for_each = local.secret_containers

  project             = var.project_id
  secret_id           = each.value
  deletion_protection = true

  replication {
    auto {}
  }

  labels = {
    application = "tesla-personal-platform"
    managed_by  = "terraform"
  }

  depends_on = [google_project_service.required]
}

resource "google_secret_manager_secret_iam_member" "mcp_gateway_accessor" {
  for_each = toset(concat([
    "mcp_auth_signing_key",
    "tesla_client_secret",
    "tesla_command_public_key",
    "tesla_token_encryption_key",
  ], var.enable_platform_oidc ? ["platform_oidc_client_secret"] : []))

  project   = var.project_id
  secret_id = google_secret_manager_secret.platform[each.value].secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.platform["mcp_gateway"].email}"
}

resource "google_secret_manager_secret_iam_member" "partner_admin_accessor" {
  for_each = toset([
    "tesla_client_secret",
    "tesla_command_public_key",
  ])

  project   = var.project_id
  secret_id = google_secret_manager_secret.platform[each.value].secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.platform["partner_admin"].email}"
}

# Cloud Run uses one service identity for every container in a revision. Only
# the official proxy sidecar mounts these values; the Python application image
# has no mount or environment reference to either private key.
resource "google_secret_manager_secret_iam_member" "command_proxy_accessor" {
  for_each = var.enable_tesla_command_proxy ? toset([
    "tesla_command_private_key",
    "tesla_command_proxy_tls_cert",
    "tesla_command_proxy_tls_key",
  ]) : toset([])

  project   = var.project_id
  secret_id = google_secret_manager_secret.platform[each.value].secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.platform["mcp_gateway"].email}"
}
