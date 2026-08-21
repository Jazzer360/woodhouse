locals {
  secret_containers = {
    mcp_auth_signing_key      = "mcp-auth-signing-key"
    tesla_client_secret       = "tesla-client-secret"
    tesla_command_private_key = "tesla-command-private-key"
    webhook_hmac_key          = "webhook-hmac-key"
  }
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
  for_each = toset([
    "mcp_auth_signing_key",
    "tesla_client_secret",
    "tesla_command_private_key",
  ])

  project   = var.project_id
  secret_id = google_secret_manager_secret.platform[each.value].secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.platform["mcp_gateway"].email}"
}
