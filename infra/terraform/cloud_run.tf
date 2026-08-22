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
