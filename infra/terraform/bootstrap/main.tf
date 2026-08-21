variable "project_id" {
  description = "GCP project that owns the Terraform state bucket."
  type        = string
}

variable "region" {
  description = "Regional location for Terraform state."
  type        = string
  default     = "us-central1"
}

variable "state_bucket_name" {
  description = "Globally unique GCS bucket name for shared Terraform state."
  type        = string
}

provider "google" {
  project = var.project_id
  region  = var.region
}

resource "google_project_service" "storage" {
  project            = var.project_id
  service            = "storage.googleapis.com"
  disable_on_destroy = false
}

resource "google_storage_bucket" "terraform_state" {
  project                     = var.project_id
  name                        = var.state_bucket_name
  location                    = var.region
  storage_class               = "STANDARD"
  force_destroy               = false
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"

  versioning {
    enabled = true
  }

  lifecycle_rule {
    action {
      type = "Delete"
    }

    condition {
      days_since_noncurrent_time = 90
      num_newer_versions         = 20
    }
  }

  labels = {
    application = "tesla-personal-platform"
    purpose     = "terraform-state"
    managed_by  = "terraform-bootstrap"
  }

  depends_on = [google_project_service.storage]
}

output "state_bucket_name" {
  description = "Bucket to pass to the shared root's GCS backend configuration."
  value       = google_storage_bucket.terraform_state.name
}
