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
