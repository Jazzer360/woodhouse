resource "google_cloud_run_v2_job" "telemetry_certificate_renewer" {
  count = var.enable_telemetry_certificate_automation ? 1 : 0

  project             = var.project_id
  name                = "tpp-telemetry-cert-renewer"
  location            = var.region
  deletion_protection = true

  labels = {
    application = "tesla-personal-platform"
    component   = "certificate-renewer"
    managed_by  = "terraform"
  }

  template {
    task_count  = 1
    parallelism = 1

    template {
      service_account       = google_service_account.platform["certificate_renewer"].email
      timeout               = "1200s"
      max_retries           = 0
      execution_environment = "EXECUTION_ENVIRONMENT_GEN2"

      containers {
        name  = "application"
        image = var.cloud_run_placeholder_image

        resources {
          limits = {
            cpu    = "1"
            memory = "512Mi"
          }
        }

        env {
          name  = "GOOGLE_CLOUD_PROJECT"
          value = var.project_id
        }
        env {
          name  = "TELEMETRY_EDGE_ZONE"
          value = var.zone
        }
        env {
          name  = "TELEMETRY_EDGE_INSTANCE"
          value = google_compute_instance.telemetry_edge.name
        }
        env {
          name  = "TELEMETRY_HOSTNAME"
          value = var.telemetry_hostname
        }
        env {
          name  = "ACME_EMAIL"
          value = var.telemetry_certificate_acme_email
        }
        env {
          name  = "TLS_CERT_SECRET"
          value = google_secret_manager_secret.platform["telemetry_edge_tls_cert"].secret_id
        }
        env {
          name  = "TLS_KEY_SECRET"
          value = google_secret_manager_secret.platform["telemetry_edge_tls_key"].secret_id
        }
        env {
          name  = "ACME_STATE_SECRET"
          value = google_secret_manager_secret.platform["telemetry_acme_state"].secret_id
        }
        env {
          name  = "TLS_RELEASE_SECRET"
          value = google_secret_manager_secret.platform["telemetry_edge_tls_release"].secret_id
        }
        env {
          name = "CLOUDFLARE_API_TOKEN"
          value_source {
            secret_key_ref {
              secret  = google_secret_manager_secret.platform["cloudflare_dns_api_token"].secret_id
              version = "latest"
            }
          }
        }
      }
    }
  }

  lifecycle {
    ignore_changes = [
      client,
      client_version,
      template[0].template[0].containers[0].image,
      template[0].labels,
    ]

    precondition {
      condition = (
        var.enable_telemetry_edge_delivery &&
        var.telemetry_certificate_acme_email != null &&
        length(trimspace(var.telemetry_certificate_acme_email)) > 0
      )
      error_message = "Certificate automation requires enabled telemetry-edge delivery and telemetry_certificate_acme_email."
    }
  }

  depends_on = [
    google_project_service.required,
    google_secret_manager_secret_iam_member.certificate_renewer_accessor,
    google_secret_manager_secret_iam_member.certificate_renewer_version_adder,
  ]
}

resource "google_cloud_run_v2_job_iam_member" "certificate_scheduler_invoker" {
  count = var.enable_telemetry_certificate_automation ? 1 : 0

  project  = var.project_id
  location = var.region
  name     = google_cloud_run_v2_job.telemetry_certificate_renewer[0].name
  role     = "roles/run.invoker"
  member   = "serviceAccount:${google_service_account.platform["certificate_scheduler"].email}"
}

resource "google_cloud_scheduler_job" "telemetry_certificate_renewal" {
  count = var.enable_telemetry_certificate_automation ? 1 : 0

  project          = var.project_id
  region           = var.region
  name             = "tpp-telemetry-certificate-renewal"
  description      = "Daily unattended ACME check for the Fleet Telemetry receiver."
  schedule         = var.telemetry_certificate_renewal_schedule
  time_zone        = "Etc/UTC"
  paused           = var.telemetry_certificate_schedule_paused
  attempt_deadline = "1800s"

  retry_config {
    retry_count          = 3
    min_backoff_duration = "60s"
    max_backoff_duration = "600s"
    max_doublings        = 3
  }

  http_target {
    http_method = "POST"
    uri         = "https://${var.region}-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/${var.project_id}/jobs/${google_cloud_run_v2_job.telemetry_certificate_renewer[0].name}:run"

    oauth_token {
      service_account_email = google_service_account.platform["certificate_scheduler"].email
      scope                 = "https://www.googleapis.com/auth/cloud-platform"
    }
  }

  lifecycle {
    precondition {
      condition = (
        var.telemetry_certificate_schedule_paused ||
        length(var.monitoring_notification_channels) > 0
      )
      error_message = "Unpausing telemetry certificate renewal requires at least one monitoring notification channel."
    }
  }

  depends_on = [
    google_cloud_run_v2_job_iam_member.certificate_scheduler_invoker,
    google_project_iam_member.cloud_scheduler_service_agent,
  ]
}
