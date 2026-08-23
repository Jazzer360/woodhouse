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

resource "google_firestore_field" "platform_login_state_expiry" {
  project    = var.project_id
  database   = google_firestore_database.default.name
  collection = "platform_login_states"
  field      = "expires_at"

  ttl_config {}
}

resource "google_firestore_field" "platform_web_session_expiry" {
  project    = var.project_id
  database   = google_firestore_database.default.name
  collection = "platform_web_sessions"
  field      = "expires_at"

  ttl_config {}
}

resource "google_firestore_field" "telemetry_fixture_expiry" {
  project    = var.project_id
  database   = google_firestore_database.default.name
  collection = "telemetry_pipeline_fixtures"
  field      = "expires_at"

  ttl_config {}
}
