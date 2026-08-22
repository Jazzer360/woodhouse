output "project_id" {
  description = "GCP project hosting the shared platform baseline."
  value       = var.project_id
}

output "region" {
  description = "Primary deployment region."
  value       = var.region
}

output "artifact_registry_repository" {
  description = "Docker repository used for commit-addressed service images."
  value       = google_artifact_registry_repository.platform.name
}

output "cloud_run_service_uris" {
  description = "Cloud Run placeholder service URIs; neither service is publicly invokable."
  value       = { for key, service in google_cloud_run_v2_service.platform : key => service.uri }
}

output "raw_telemetry_topic" {
  description = "Pub/Sub topic receiving every valid decoded telemetry record."
  value       = google_pubsub_topic.raw_telemetry.id
}

output "raw_telemetry_subscription" {
  description = "Authenticated push subscription for telemetry-processor."
  value       = google_pubsub_subscription.telemetry_processor.id
}

output "telemetry_edge_public_ip" {
  description = "Reserved public IPv4 for the future Fleet Telemetry receiver."
  value       = google_compute_address.telemetry_edge.address
}

output "quarantine_table" {
  description = "Restricted table for telemetry with no trusted owner mapping."
  value       = "${var.project_id}.${google_bigquery_dataset.quarantine.dataset_id}.${google_bigquery_table.quarantine_raw_telemetry.table_id}"
}

output "runtime_service_accounts" {
  description = "Service-account emails used by later application phases and per-user dataset grants."
  value = {
    mcp_gateway         = google_service_account.platform["mcp_gateway"].email
    telemetry_processor = google_service_account.platform["telemetry_processor"].email
    telemetry_edge      = google_service_account.platform["telemetry_edge"].email
  }
}

output "cloud_build_service_accounts" {
  description = "Separate PR validation and application deployment identities."
  value = {
    validator = google_service_account.platform["cloud_build_validator"].email
    deployer  = google_service_account.platform["cloud_build_deployer"].email
  }
}

output "user_admin_service_account" {
  description = "Keyless identity used by the manual add-user and disable-user workflows."
  value       = google_service_account.platform["user_admin"].email
}

output "dataset_owner_service_account" {
  description = "Keyless, non-impersonatable identity holding BigQuery's mandatory direct dataset-owner entries."
  value       = google_service_account.platform["dataset_owner"].email
}

output "secret_containers" {
  description = "Secret Manager container IDs; Terraform creates no secret versions."
  value       = { for key, secret in google_secret_manager_secret.platform : key => secret.secret_id }
}
