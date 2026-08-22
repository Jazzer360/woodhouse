locals {
  cloud_run_services = {
    mcp_gateway = {
      name          = "mcp-gateway"
      ingress       = "INGRESS_TRAFFIC_ALL"
      max_instances = 2
    }
    telemetry_processor = {
      name = "telemetry-processor"
      # Same-project Pub/Sub push subscriptions are accepted by Cloud Run's
      # internal ingress setting; IAM and the OIDC token still gate invocation.
      ingress       = "INGRESS_TRAFFIC_INTERNAL_ONLY"
      max_instances = 3
    }
  }

  tesla_gateway_environment = {
    TESLA_CLIENT_ID          = var.tesla_client_id
    TESLA_APP_DOMAIN         = var.tesla_app_domain
    TESLA_OAUTH_REDIRECT_URI = var.tesla_oauth_redirect_uri
    TESLA_INITIAL_AUDIENCE   = var.tesla_initial_audience
    TESLA_ONBOARDING_ENABLED = "true"
  }

  tesla_gateway_secret_environment = {
    TESLA_CLIENT_SECRET        = "tesla_client_secret"
    TESLA_PUBLIC_KEY_PEM       = "tesla_command_public_key"
    TESLA_TOKEN_ENCRYPTION_KEY = "tesla_token_encryption_key"
  }
}

resource "google_cloud_run_v2_service" "platform" {
  for_each = local.cloud_run_services

  project             = var.project_id
  name                = each.value.name
  location            = var.region
  ingress             = each.value.ingress
  deletion_protection = true

  template {
    service_account                  = google_service_account.platform[each.key].email
    timeout                          = "300s"
    max_instance_request_concurrency = 20

    scaling {
      max_instance_count = each.value.max_instances
    }

    containers {
      image = var.cloud_run_placeholder_image

      ports {
        container_port = 8080
      }

      resources {
        cpu_idle = true
        limits = {
          cpu    = "1"
          memory = "256Mi"
        }
      }

      env {
        name  = "APP_ENV"
        value = each.key == "mcp_gateway" ? "phase-3-platform-auth" : "phase-2-placeholder"
      }

      env {
        name  = "GOOGLE_CLOUD_PROJECT"
        value = var.project_id
      }

      dynamic "env" {
        for_each = each.key == "mcp_gateway" && var.oidc_audience != null ? [var.oidc_audience] : []
        content {
          name  = "OIDC_AUDIENCE"
          value = env.value
        }
      }

      dynamic "env" {
        for_each = each.key == "mcp_gateway" && var.enable_tesla_onboarding ? local.tesla_gateway_environment : {}
        content {
          name  = env.key
          value = env.value
        }
      }

      dynamic "env" {
        for_each = each.key == "mcp_gateway" && var.enable_tesla_onboarding ? local.tesla_gateway_secret_environment : {}
        content {
          name = env.key
          value_source {
            secret_key_ref {
              secret  = google_secret_manager_secret.platform[env.value].secret_id
              version = "latest"
            }
          }
        }
      }
    }
  }

  traffic {
    type    = "TRAFFIC_TARGET_ALLOCATION_TYPE_LATEST"
    percent = 100
  }

  lifecycle {
    # Application delivery owns the commit-addressed image after initial creation.
    # The API also reports an unset service-level scaling block as explicit
    # zero values. Ignore that computed normalization while continuing to
    # manage revision scaling under template.scaling above.
    ignore_changes = [
      client,
      client_version,
      template[0].containers[0].image,
      scaling,
    ]


    precondition {
      condition = !var.enable_tesla_onboarding || alltrue([
        var.tesla_client_id != null && length(trimspace(var.tesla_client_id)) > 0,
        var.tesla_app_domain != null && length(trimspace(var.tesla_app_domain)) > 0,
        var.tesla_oauth_redirect_uri != null && startswith(var.tesla_oauth_redirect_uri, "https://"),
      ])
      error_message = "enable_tesla_onboarding requires tesla_client_id, tesla_app_domain, and an HTTPS tesla_oauth_redirect_uri."
    }
  }

  depends_on = [google_project_service.required]
}

resource "google_cloud_run_v2_service_iam_member" "mcp_external_invoker" {
  count = var.enable_mcp_external_access ? 1 : 0

  project  = var.project_id
  location = google_cloud_run_v2_service.platform["mcp_gateway"].location
  name     = google_cloud_run_v2_service.platform["mcp_gateway"].name
  role     = "roles/run.invoker"
  member   = "allUsers"

  lifecycle {
    precondition {
      condition     = var.oidc_audience == null ? false : length(trimspace(var.oidc_audience)) > 0
      error_message = "enable_mcp_external_access requires a configured oidc_audience."
    }
  }
}

resource "google_cloud_run_v2_service_iam_member" "pubsub_processor_invoker" {
  project  = var.project_id
  location = google_cloud_run_v2_service.platform["telemetry_processor"].location
  name     = google_cloud_run_v2_service.platform["telemetry_processor"].name
  role     = "roles/run.invoker"
  member   = "serviceAccount:${google_service_account.platform["pubsub_push"].email}"
}
