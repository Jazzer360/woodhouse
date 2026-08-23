resource "google_logging_project_exclusion" "mcp_oauth_callback_request_urls" {
  project     = var.project_id
  name        = "tpp-mcp-oauth-callback-request-urls"
  description = "Exclude Cloud Run request logs containing platform or Tesla OAuth callback query parameters. Application logs retain only query-free paths."
  filter      = <<-EOT
    resource.type="cloud_run_revision"
    resource.labels.service_name="${google_cloud_run_v2_service.platform["mcp_gateway"].name}"
    log_id("run.googleapis.com/requests")
    httpRequest.requestUrl=~"/(auth|oauth)/callback\\?"
  EOT

  depends_on = [google_project_service.required]
}

resource "google_logging_metric" "unknown_vehicle_telemetry" {
  project     = var.project_id
  name        = "tpp/unknown_vehicle_telemetry"
  description = "Telemetry records that could not be mapped to a trusted vehicle owner."
  filter      = <<-EOT
    resource.type="cloud_run_revision"
    resource.labels.service_name="${google_cloud_run_v2_service.platform["telemetry_processor"].name}"
    jsonPayload.event="unknown_vehicle_telemetry"
  EOT

  metric_descriptor {
    metric_kind  = "DELTA"
    value_type   = "INT64"
    unit         = "1"
    display_name = "Unknown vehicle telemetry records"
  }

  depends_on = [google_project_service.required]
}

resource "google_logging_metric" "telemetry_certificate_check_success" {
  count = var.enable_telemetry_certificate_automation ? 1 : 0

  project     = var.project_id
  name        = "tpp/telemetry_certificate_check_success"
  description = "Successful scheduled validation or renewal of the Fleet Telemetry certificate."
  filter      = <<-EOT
    resource.type="cloud_run_job"
    resource.labels.job_name="tpp-telemetry-cert-renewer"
    jsonPayload.event="telemetry_certificate_check"
    (jsonPayload.status="healthy" OR jsonPayload.status="renewed")
  EOT

  metric_descriptor {
    metric_kind  = "DELTA"
    value_type   = "INT64"
    unit         = "1"
    display_name = "Telemetry certificate successful checks"
  }

  depends_on = [google_project_service.required]
}

resource "google_logging_metric" "telemetry_certificate_check_failure" {
  count = var.enable_telemetry_certificate_automation ? 1 : 0

  project     = var.project_id
  name        = "tpp/telemetry_certificate_check_failure"
  description = "Failed scheduled validation, renewal, or deployment of the Fleet Telemetry certificate."
  filter      = <<-EOT
    resource.type="cloud_run_job"
    resource.labels.job_name="tpp-telemetry-cert-renewer"
    jsonPayload.event="telemetry_certificate_check"
    jsonPayload.status="failed"
  EOT

  metric_descriptor {
    metric_kind  = "DELTA"
    value_type   = "INT64"
    unit         = "1"
    display_name = "Telemetry certificate failed checks"
  }

  depends_on = [google_project_service.required]
}

resource "google_monitoring_alert_policy" "telemetry_certificate_check_missing" {
  count = var.enable_telemetry_certificate_automation ? 1 : 0

  project      = var.project_id
  display_name = "TPP telemetry certificate check is missing"
  combiner     = "OR"
  enabled      = true

  documentation {
    content   = "No successful Fleet Telemetry certificate check has been observed for 48 hours. Inspect the Scheduler and Cloud Run Job before the active certificate approaches expiry."
    mime_type = "text/markdown"
  }

  conditions {
    display_name = "No successful certificate check for 48 hours"

    condition_absent {
      filter   = "resource.type = \"cloud_run_job\" AND metric.type = \"logging.googleapis.com/user/${google_logging_metric.telemetry_certificate_check_success[0].name}\""
      duration = "172800s"

      aggregations {
        alignment_period   = "86400s"
        per_series_aligner = "ALIGN_SUM"
      }
    }
  }

  notification_channels = var.monitoring_notification_channels
}

resource "google_monitoring_alert_policy" "telemetry_certificate_check_failed" {
  count = var.enable_telemetry_certificate_automation ? 1 : 0

  project      = var.project_id
  display_name = "TPP telemetry certificate renewal failed"
  combiner     = "OR"
  enabled      = true

  documentation {
    content   = "The unattended Fleet Telemetry certificate job failed validation, renewal, or deployment. The previous certificate remains active; investigate before its expiry window closes."
    mime_type = "text/markdown"
  }

  conditions {
    display_name = "Certificate job reported a failure"

    condition_threshold {
      filter          = "resource.type = \"cloud_run_job\" AND metric.type = \"logging.googleapis.com/user/${google_logging_metric.telemetry_certificate_check_failure[0].name}\""
      comparison      = "COMPARISON_GT"
      duration        = "0s"
      threshold_value = 0

      aggregations {
        alignment_period   = "300s"
        per_series_aligner = "ALIGN_SUM"
      }
    }
  }

  notification_channels = var.monitoring_notification_channels
}

resource "google_monitoring_alert_policy" "raw_telemetry_backlog" {
  project      = var.project_id
  display_name = "TPP raw telemetry backlog is stale"
  combiner     = "OR"
  enabled      = true

  documentation {
    content   = "The oldest unacknowledged raw telemetry message is over ten minutes old. Investigate the telemetry processor without discarding or sampling records."
    mime_type = "text/markdown"
  }

  conditions {
    display_name = "Oldest unacknowledged message exceeds ten minutes"

    condition_threshold {
      filter          = <<-EOT
        resource.type = "pubsub_subscription"
        AND resource.labels.subscription_id = "${google_pubsub_subscription.telemetry_processor.name}"
        AND metric.type = "pubsub.googleapis.com/subscription/oldest_unacked_message_age"
      EOT
      comparison      = "COMPARISON_GT"
      duration        = "300s"
      threshold_value = 600

      aggregations {
        alignment_period   = "300s"
        per_series_aligner = "ALIGN_MAX"
      }
    }
  }

  notification_channels = var.monitoring_notification_channels

  depends_on = [google_project_service.required]
}

resource "google_monitoring_alert_policy" "fleet_raw_telemetry_backlog" {
  for_each = google_pubsub_subscription.fleet_telemetry_processor

  project      = var.project_id
  display_name = "TPP ${each.key} telemetry backlog is stale"
  combiner     = "OR"
  enabled      = true

  documentation {
    content   = "The oldest unacknowledged ${each.key} telemetry message is over ten minutes old. Investigate without discarding or sampling records."
    mime_type = "text/markdown"
  }

  conditions {
    display_name = "Oldest unacknowledged ${each.key} message exceeds ten minutes"

    condition_threshold {
      filter          = <<-EOT
        resource.type = "pubsub_subscription"
        AND resource.labels.subscription_id = "${each.value.name}"
        AND metric.type = "pubsub.googleapis.com/subscription/oldest_unacked_message_age"
      EOT
      comparison      = "COMPARISON_GT"
      duration        = "300s"
      threshold_value = 600

      aggregations {
        alignment_period   = "300s"
        per_series_aligner = "ALIGN_MAX"
      }
    }
  }

  notification_channels = var.monitoring_notification_channels

  depends_on = [google_project_service.required]
}
