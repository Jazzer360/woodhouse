locals {
  service_accounts = {
    mcp_gateway = {
      account_id   = "tpp-mcp-gateway"
      display_name = "Tesla Personal Platform MCP gateway"
    }
    telemetry_processor = {
      account_id   = "tpp-telemetry-processor"
      display_name = "Tesla Personal Platform telemetry processor"
    }
    telemetry_edge = {
      account_id   = "tpp-telemetry-edge"
      display_name = "Tesla Personal Platform telemetry edge"
    }
    certificate_renewer = {
      account_id   = "tpp-cert-renewer"
      display_name = "Tesla Personal Platform telemetry certificate renewer"
    }
    certificate_scheduler = {
      account_id   = "tpp-cert-scheduler"
      display_name = "Tesla Personal Platform certificate renewal scheduler"
    }
    pubsub_push = {
      account_id   = "tpp-pubsub-push"
      display_name = "Tesla Personal Platform Pub/Sub push invoker"
    }
    cloud_build_validator = {
      account_id   = "tpp-build-validator"
      display_name = "Tesla Personal Platform PR validator"
    }
    cloud_build_deployer = {
      account_id   = "tpp-build-deployer"
      display_name = "Tesla Personal Platform application deployer"
    }
    analytics_view_reconciler = {
      account_id   = "tpp-analytics-view-reconciler"
      display_name = "Tesla Personal Platform analytics view reconciler"
    }
    user_admin = {
      account_id   = "tpp-user-admin"
      display_name = "Tesla Personal Platform manual user administrator"
    }
    partner_admin = {
      account_id   = "tpp-partner-admin"
      display_name = "Tesla Personal Platform Tesla partner registrar"
    }
    dataset_owner = {
      account_id   = "tpp-dataset-owner"
      display_name = "Tesla Personal Platform per-user dataset owner"
    }
  }
}

resource "google_service_account" "platform" {
  for_each = local.service_accounts

  project      = var.project_id
  account_id   = each.value.account_id
  display_name = each.value.display_name
  description  = "Managed by Terraform for the Tesla Personal Platform."

  depends_on = [google_project_service.required]
}
