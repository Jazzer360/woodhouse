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
    TESLA_CLIENT_ID             = var.tesla_client_id
    TESLA_APP_DOMAIN            = var.tesla_app_domain
    TESLA_OAUTH_REDIRECT_URI    = var.tesla_oauth_redirect_uri
    TESLA_INITIAL_AUDIENCE      = var.tesla_initial_audience
    TESLA_ONBOARDING_ENABLED    = "true"
    TESLA_COMMAND_PROXY_ENABLED = var.enable_tesla_command_proxy ? "true" : "false"
    TESLA_COMMAND_PROXY_ORIGIN  = "https://localhost:4443"
    TESLA_COMMAND_PROXY_CA_FILE = "/var/run/tpp-proxy-ca/tls.crt"
  }

  tesla_gateway_secret_environment = {
    TESLA_CLIENT_SECRET        = "tesla_client_secret"
    TESLA_PUBLIC_KEY_PEM       = "tesla_command_public_key"
    TESLA_TOKEN_ENCRYPTION_KEY = "tesla_token_encryption_key"
  }

  platform_oidc_environment = {
    PLATFORM_OIDC_ISSUER = (
      var.platform_oidc_issuer == null ? "" : var.platform_oidc_issuer
    )
    PLATFORM_OIDC_RESOURCE_URL = (
      var.platform_oidc_resource_url == null ? "" : var.platform_oidc_resource_url
    )
    PLATFORM_OIDC_CLIENT_ID = (
      var.platform_oidc_client_id == null ? "" : var.platform_oidc_client_id
    )
    PLATFORM_OIDC_REDIRECT_URI = (
      var.platform_oidc_redirect_uri == null ? "" : var.platform_oidc_redirect_uri
    )
  }

  telemetry_processor_environment = {
    PUBSUB_PUSH_AUDIENCE             = local.telemetry_processor_push_audience
    PUBSUB_PUSH_SERVICE_ACCOUNT      = google_service_account.platform["pubsub_push"].email
    QUARANTINE_TABLE                 = "${var.project_id}.${google_bigquery_dataset.quarantine.dataset_id}.${google_bigquery_table.quarantine_raw_telemetry.table_id}"
    SYNTHETIC_TELEMETRY_TABLE        = "${var.project_id}.${google_bigquery_dataset.quarantine.dataset_id}.${google_bigquery_table.synthetic_raw_telemetry.table_id}"
    TELEMETRY_RECEIVER_VERSION       = var.fleet_telemetry_receiver_version
    SYNTHETIC_TELEMETRY_SUBSCRIPTION = "projects/${var.project_id}/subscriptions/tpp-raw-telemetry-processor"
    FLEET_TELEMETRY_SUBSCRIPTIONS = jsonencode({
      for key in local.fleet_telemetry_record_types :
      "projects/${var.project_id}/subscriptions/tpp-raw-telemetry-${lower(key)}-processor" => key
    })
  }
}

resource "google_cloud_run_v2_service" "platform" {
  for_each = local.cloud_run_services

  project             = var.project_id
  name                = each.value.name
  location            = var.region
  ingress             = each.value.ingress
  deletion_protection = true
  custom_audiences = (
    each.key == "telemetry_processor" ? [local.telemetry_processor_push_audience] : []
  )

  template {
    service_account                  = google_service_account.platform[each.key].email
    timeout                          = "300s"
    max_instance_request_concurrency = 20

    scaling {
      max_instance_count = each.value.max_instances
    }

    containers {
      name  = "application"
      image = var.cloud_run_placeholder_image
      depends_on = each.key == "mcp_gateway" && var.enable_tesla_command_proxy ? [
        "tesla-command-proxy"
      ] : []

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
        for_each = each.key == "telemetry_processor" ? local.telemetry_processor_environment : {}
        content {
          name  = env.key
          value = env.value
        }
      }

      dynamic "env" {
        for_each = each.key == "mcp_gateway" && var.oidc_audience != null ? [var.oidc_audience] : []
        content {
          name  = "OIDC_AUDIENCE"
          value = env.value
        }
      }

      dynamic "env" {
        for_each = each.key == "mcp_gateway" && var.enable_platform_oidc ? local.platform_oidc_environment : {}
        content {
          name  = env.key
          value = env.value
        }
      }

      dynamic "env" {
        for_each = each.key == "mcp_gateway" && var.enable_platform_oidc ? [1] : []
        content {
          name = "PLATFORM_OIDC_CLIENT_SECRET"
          value_source {
            secret_key_ref {
              secret  = google_secret_manager_secret.platform["platform_oidc_client_secret"].secret_id
              version = "latest"
            }
          }
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

      dynamic "volume_mounts" {
        for_each = each.key == "mcp_gateway" && var.enable_tesla_command_proxy ? [1] : []
        content {
          name       = "command-proxy-ca"
          mount_path = "/var/run/tpp-proxy-ca"
        }
      }
    }

    dynamic "containers" {
      for_each = each.key == "mcp_gateway" && var.enable_tesla_command_proxy ? [1] : []
      content {
        name  = "tesla-command-proxy"
        image = var.tesla_command_proxy_image

        resources {
          cpu_idle = true
          limits = {
            cpu    = "1"
            memory = "256Mi"
          }
        }

        env {
          name  = "TESLA_KEY_FILE"
          value = "/var/run/tpp-command/key.pem"
        }
        env {
          name  = "TESLA_HTTP_PROXY_TLS_CERT"
          value = "/var/run/tpp-proxy-cert/tls.crt"
        }
        env {
          name  = "TESLA_HTTP_PROXY_TLS_KEY"
          value = "/var/run/tpp-proxy-key/tls.key"
        }
        env {
          name = "TESLA_HTTP_PROXY_HOST"
          # Cloud Run startup probes target the container interface rather than
          # its loopback device. Only the application container is configured
          # for service ingress, so this port remains revision-internal.
          value = "0.0.0.0"
        }
        env {
          name  = "TESLA_HTTP_PROXY_PORT"
          value = "4443"
        }
        env {
          name  = "TESLA_HTTP_PROXY_TIMEOUT"
          value = "20s"
        }

        volume_mounts {
          name       = "tesla-command-key"
          mount_path = "/var/run/tpp-command"
        }
        volume_mounts {
          name       = "command-proxy-cert"
          mount_path = "/var/run/tpp-proxy-cert"
        }
        volume_mounts {
          name       = "command-proxy-key"
          mount_path = "/var/run/tpp-proxy-key"
        }

        startup_probe {
          initial_delay_seconds = 0
          timeout_seconds       = 1
          period_seconds        = 2
          failure_threshold     = 15
          tcp_socket {
            port = 4443
          }
        }
      }
    }

    dynamic "volumes" {
      for_each = each.key == "mcp_gateway" && var.enable_tesla_command_proxy ? [1] : []
      content {
        name = "tesla-command-key"
        secret {
          secret = google_secret_manager_secret.platform["tesla_command_private_key"].secret_id
          items {
            version = "latest"
            path    = "key.pem"
            mode    = 292 # 0444; volume is mounted only in the proxy container
          }
        }
      }
    }

    dynamic "volumes" {
      for_each = each.key == "mcp_gateway" && var.enable_tesla_command_proxy ? [1] : []
      content {
        name = "command-proxy-cert"
        secret {
          secret = google_secret_manager_secret.platform["tesla_command_proxy_tls_cert"].secret_id
          items {
            version = "latest"
            path    = "tls.crt"
            mode    = 292 # 0444
          }
        }
      }
    }

    dynamic "volumes" {
      for_each = each.key == "mcp_gateway" && var.enable_tesla_command_proxy ? [1] : []
      content {
        name = "command-proxy-key"
        secret {
          secret = google_secret_manager_secret.platform["tesla_command_proxy_tls_key"].secret_id
          items {
            version = "latest"
            path    = "tls.key"
            mode    = 292 # 0444; volume is mounted only in the proxy container
          }
        }
      }
    }

    dynamic "volumes" {
      for_each = each.key == "mcp_gateway" && var.enable_tesla_command_proxy ? [1] : []
      content {
        name = "command-proxy-ca"
        secret {
          secret = google_secret_manager_secret.platform["tesla_command_proxy_tls_cert"].secret_id
          items {
            version = "latest"
            path    = "tls.crt"
            mode    = 292 # 0444
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
    # Application delivery owns the commit-addressed image and revision labels
    # after initial creation. Preserve its tpp-deployed-commit label (and the
    # provider-managed revision labels returned alongside it) across later
    # infrastructure applies.
    # The API also reports an unset service-level scaling block as explicit
    # zero values. Ignore that computed normalization while continuing to
    # manage revision scaling under template.scaling above.
    ignore_changes = [
      client,
      client_version,
      template[0].containers[0].image,
      template[0].labels,
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

    precondition {
      condition = !var.enable_platform_oidc || alltrue([
        var.platform_oidc_issuer != null && startswith(var.platform_oidc_issuer, "https://"),
        var.platform_oidc_resource_url != null && startswith(var.platform_oidc_resource_url, "https://"),
        var.platform_oidc_client_id != null && length(trimspace(var.platform_oidc_client_id)) > 0,
        var.platform_oidc_redirect_uri != null && startswith(var.platform_oidc_redirect_uri, "https://"),
      ])
      error_message = "enable_platform_oidc requires HTTPS issuer/resource/redirect URLs and a client ID."
    }

    precondition {
      condition = !var.enable_tesla_command_proxy || (
        each.key != "mcp_gateway" || (
          var.enable_tesla_onboarding && var.tesla_command_proxy_image != null
        )
      )
      error_message = "enable_tesla_command_proxy requires Tesla onboarding and a digest-pinned official proxy image."
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
      condition = var.enable_platform_oidc || (
        var.oidc_audience == null ? false : length(trimspace(var.oidc_audience)) > 0
      )
      error_message = "enable_mcp_external_access requires platform OIDC or the legacy Google audience."
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
