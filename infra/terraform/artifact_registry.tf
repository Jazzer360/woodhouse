resource "google_artifact_registry_repository" "platform" {
  project       = var.project_id
  location      = var.region
  repository_id = "tesla-personal-platform"
  description   = "Commit-addressed Tesla Personal Platform service images."
  format        = "DOCKER"

  docker_config {
    immutable_tags = true
  }

  labels = {
    application = "tesla-personal-platform"
    managed_by  = "terraform"
  }

  depends_on = [google_project_service.required]
}

# Cloud Run's service agent pulls same-project images. The runtime service
# accounts execute application code and intentionally receive no repository IAM.
resource "google_artifact_registry_repository_iam_member" "deployer_writer" {
  project    = var.project_id
  location   = google_artifact_registry_repository.platform.location
  repository = google_artifact_registry_repository.platform.name
  role       = "roles/artifactregistry.writer"
  member     = "serviceAccount:${google_service_account.platform["cloud_build_deployer"].email}"
}
