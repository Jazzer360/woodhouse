resource "google_bigquery_dataset" "quarantine" {
  project                    = var.project_id
  dataset_id                 = "tesla_system_quarantine"
  friendly_name              = "Tesla telemetry quarantine"
  description                = "Restricted telemetry whose vehicle ownership cannot be resolved."
  location                   = var.region
  delete_contents_on_destroy = false
  max_time_travel_hours      = 168

  labels = {
    application = "tesla-personal-platform"
    data_class  = "restricted-quarantine"
    managed_by  = "terraform"
  }

  depends_on = [google_project_service.required]
}

resource "google_bigquery_table" "quarantine_raw_telemetry" {
  project                  = var.project_id
  dataset_id               = google_bigquery_dataset.quarantine.dataset_id
  table_id                 = "raw_unknown_telemetry"
  description              = "Append-only diagnostic destination for telemetry with no trusted owner mapping."
  deletion_protection      = true
  require_partition_filter = true
  schema                   = file("${path.module}/schemas/quarantine_raw_telemetry.json")

  time_partitioning {
    type  = "DAY"
    field = "source_timestamp"
  }

  clustering = ["record_type"]

  labels = {
    application = "tesla-personal-platform"
    data_class  = "restricted-quarantine"
    managed_by  = "terraform"
  }
}

resource "google_bigquery_dataset_iam_member" "quarantine_writer" {
  project    = var.project_id
  dataset_id = google_bigquery_dataset.quarantine.dataset_id
  role       = "roles/bigquery.dataEditor"
  member     = "serviceAccount:${google_service_account.platform["telemetry_processor"].email}"
}
