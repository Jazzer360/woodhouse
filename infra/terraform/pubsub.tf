locals {
  telemetry_processor_push_endpoint = "${google_cloud_run_v2_service.platform["telemetry_processor"].uri}/pubsub/push"
}

resource "google_pubsub_topic" "raw_telemetry" {
  project                    = var.project_id
  name                       = "tpp-raw-telemetry"
  message_retention_duration = "2678400s"

  labels = {
    application = "tesla-personal-platform"
    data_class  = "sensitive-telemetry"
    managed_by  = "terraform"
  }

  depends_on = [google_project_service.required]
}

resource "google_pubsub_topic_iam_member" "telemetry_edge_publisher" {
  project = var.project_id
  topic   = google_pubsub_topic.raw_telemetry.name
  role    = "roles/pubsub.publisher"
  member  = "serviceAccount:${google_service_account.platform["telemetry_edge"].email}"
}

resource "google_service_account_iam_member" "pubsub_push_token_creator" {
  service_account_id = google_service_account.platform["pubsub_push"].name
  role               = "roles/iam.serviceAccountTokenCreator"
  member             = "serviceAccount:${google_project_service_identity.pubsub.email}"
}

resource "google_pubsub_subscription" "telemetry_processor" {
  project                    = var.project_id
  name                       = "tpp-raw-telemetry-processor"
  topic                      = google_pubsub_topic.raw_telemetry.name
  ack_deadline_seconds       = 60
  message_retention_duration = "2678400s"
  retain_acked_messages      = false

  expiration_policy {
    ttl = ""
  }

  retry_policy {
    minimum_backoff = "10s"
    maximum_backoff = "600s"
  }

  push_config {
    push_endpoint = local.telemetry_processor_push_endpoint

    oidc_token {
      service_account_email = google_service_account.platform["pubsub_push"].email
      audience              = local.telemetry_processor_push_endpoint
    }
  }

  labels = {
    application = "tesla-personal-platform"
    data_class  = "sensitive-telemetry"
    managed_by  = "terraform"
  }

  depends_on = [
    google_cloud_run_v2_service_iam_member.pubsub_processor_invoker,
    google_service_account_iam_member.pubsub_push_token_creator,
  ]
}
