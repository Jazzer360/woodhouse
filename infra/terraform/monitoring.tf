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
