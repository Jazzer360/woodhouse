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

resource "google_cloudbuild_trigger" "pr_validation" {
  count = var.cloud_build_repository == null ? 0 : 1

  project            = var.project_id
  location           = var.region
  name               = "tpp-pr-validation"
  description        = "PR validation for Tesla Personal Platform; no deployment permissions"
  filename           = "cloudbuild.pr.yaml"
  service_account    = google_service_account.platform["cloud_build_validator"].id
  include_build_logs = "INCLUDE_BUILD_LOGS_WITH_STATUS"

  repository_event_config {
    repository = var.cloud_build_repository

    pull_request {
      branch          = "main$"
      comment_control = "COMMENTS_ENABLED_FOR_EXTERNAL_CONTRIBUTORS_ONLY"
    }
  }
}

resource "google_cloudbuild_trigger" "main_mcp_gateway" {
  count = var.cloud_build_repository == null ? 0 : 1

  project            = var.project_id
  location           = var.region
  name               = "tpp-main-mcp-gateway"
  description        = "Deploy the affected MCP gateway revision after a main merge"
  filename           = "cloudbuild.main.yaml"
  service_account    = google_service_account.platform["cloud_build_deployer"].id
  include_build_logs = "INCLUDE_BUILD_LOGS_WITH_STATUS"
  included_files = [
    "cloudbuild.main.yaml",
    "services/mcp-gateway/**",
    "packages/**",
    "pyproject.toml",
    "uv.lock",
  ]
  substitutions = {
    _SERVICE = "mcp-gateway"
  }

  repository_event_config {
    repository = var.cloud_build_repository

    push {
      branch = "main$"
    }
  }
}

resource "google_cloudbuild_trigger" "main_telemetry_processor" {
  count = var.cloud_build_repository == null ? 0 : 1

  project            = var.project_id
  location           = var.region
  name               = "tpp-main-telemetry-processor"
  description        = "Deploy the affected telemetry processor revision after a main merge"
  filename           = "cloudbuild.main.yaml"
  service_account    = google_service_account.platform["cloud_build_deployer"].id
  include_build_logs = "INCLUDE_BUILD_LOGS_WITH_STATUS"
  included_files = [
    "cloudbuild.main.yaml",
    "services/telemetry-processor/**",
    "packages/**",
    "pyproject.toml",
    "uv.lock",
  ]
  substitutions = {
    _SERVICE = "telemetry-processor"
  }

  repository_event_config {
    repository = var.cloud_build_repository

    push {
      branch = "main$"
    }
  }
}
