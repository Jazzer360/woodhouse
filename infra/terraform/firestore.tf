resource "google_firestore_database" "default" {
  project                     = var.project_id
  name                        = "(default)"
  location_id                 = var.region
  type                        = "FIRESTORE_NATIVE"
  concurrency_mode            = "PESSIMISTIC"
  app_engine_integration_mode = "DISABLED"
  delete_protection_state     = "DELETE_PROTECTION_ENABLED"
  deletion_policy             = "ABANDON"

  depends_on = [google_project_service.required]
}

resource "google_firestore_field" "tesla_oauth_state_expiry" {
  project    = var.project_id
  database   = google_firestore_database.default.name
  collection = "tesla_oauth_states"
  field      = "expires_at"

  ttl_config {}
}
