locals {
  telemetry_processor_push_endpoint = "${google_cloud_run_v2_service.platform["telemetry_processor"].uri}/pubsub/push"
  # Cloud Run validates the token audience before the request reaches the
  # application. Use a stable custom audience so the authenticated Pub/Sub
  # token can remain path-scoped without creating a self-reference to the
  # service's provider-generated run.app URL.
  telemetry_processor_push_audience = "https://telemetry-processor.woodhouse.derekjass.com/pubsub/push"
  fleet_telemetry_record_types      = toset(["V", "alerts", "connectivity", "errors"])
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

resource "google_pubsub_topic" "fleet_raw_telemetry" {
  for_each = local.fleet_telemetry_record_types

  project                    = var.project_id
  name                       = "tpp-raw-telemetry_${each.value}"
  message_retention_duration = "2678400s"

  labels = {
    application = "tesla-personal-platform"
    data_class  = "sensitive-telemetry"
    managed_by  = "terraform"
    record_type = lower(each.value)
  }

  depends_on = [google_project_service.required]
}

resource "google_pubsub_topic_iam_member" "telemetry_edge_publisher" {
  for_each = google_pubsub_topic.fleet_raw_telemetry

  project = var.project_id
  topic   = each.value.name
  role    = "roles/pubsub.publisher"
  member  = "serviceAccount:${google_service_account.platform["telemetry_edge"].email}"
}

resource "google_pubsub_topic_iam_member" "telemetry_operator_fixture_publisher" {
  for_each = var.admin_principals

  project = var.project_id
  topic   = google_pubsub_topic.raw_telemetry.name
  role    = "roles/pubsub.publisher"
  member  = each.value
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
      audience              = local.telemetry_processor_push_audience
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

resource "google_pubsub_subscription" "fleet_telemetry_processor" {
  for_each = google_pubsub_topic.fleet_raw_telemetry

  project                    = var.project_id
  name                       = "tpp-raw-telemetry-${lower(each.key)}-processor"
  topic                      = each.value.name
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
      audience              = local.telemetry_processor_push_audience
    }
  }

  labels = {
    application = "tesla-personal-platform"
    data_class  = "sensitive-telemetry"
    managed_by  = "terraform"
    record_type = lower(each.key)
  }

  depends_on = [
    google_cloud_run_v2_service_iam_member.pubsub_processor_invoker,
    google_service_account_iam_member.pubsub_push_token_creator,
  ]
}
