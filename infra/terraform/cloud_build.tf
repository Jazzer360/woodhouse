resource "google_storage_bucket" "cloud_build_source" {
  project                     = var.project_id
  name                        = "${var.project_id}-tpp-cloudbuild-source"
  location                    = var.region
  storage_class               = "STANDARD"
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"
  force_destroy               = false

  lifecycle_rule {
    condition {
      age = 7
    }
    action {
      type = "Delete"
    }
  }

  labels = {
    application = "tesla-personal-platform"
    managed_by  = "terraform"
    purpose     = "cloud-build-source"
  }

  depends_on = [google_project_service.required]
}

resource "google_storage_bucket_iam_member" "deployer_source_reader" {
  bucket = google_storage_bucket.cloud_build_source.name
  role   = "roles/storage.objectViewer"
  member = "serviceAccount:${google_service_account.platform["cloud_build_deployer"].email}"
}
