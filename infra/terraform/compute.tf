resource "google_compute_instance" "telemetry_edge" {
  project                   = var.project_id
  name                      = "tpp-telemetry-edge"
  zone                      = var.zone
  machine_type              = "e2-micro"
  can_ip_forward            = false
  allow_stopping_for_update = true
  deletion_protection       = true

  labels = {
    application = "tesla-personal-platform"
    component   = "telemetry-edge"
    managed_by  = "terraform"
  }

  boot_disk {
    auto_delete = true

    initialize_params {
      image = "projects/cos-cloud/global/images/family/cos-stable"
      size  = 10
      type  = "pd-balanced"
    }
  }

  network_interface {
    subnetwork = google_compute_subnetwork.platform.id

    access_config {
      nat_ip       = google_compute_address.telemetry_edge.address
      network_tier = "PREMIUM"
    }
  }

  metadata = {
    block-project-ssh-keys    = "TRUE"
    enable-guest-attributes   = "TRUE"
    enable-oslogin            = "TRUE"
    google-logging-enabled    = "true"
    google-monitoring-enabled = "true"
    telemetry-edge-commit     = ""
    telemetry-edge-image      = ""
    telemetry-edge-config = jsonencode(merge(
      jsondecode(file("${path.module}/../../services/telemetry-edge/config.json")),
      { pubsub = { gcp_project_id = var.project_id } }
    ))
    telemetry-edge-project-id      = var.project_id
    telemetry-edge-region          = var.region
    telemetry-edge-repository      = google_artifact_registry_repository.platform.repository_id
    telemetry-edge-tls-cert-secret = google_secret_manager_secret.platform["telemetry_edge_tls_cert"].secret_id
    telemetry-edge-tls-key-secret  = google_secret_manager_secret.platform["telemetry_edge_tls_key"].secret_id
  }

  metadata_startup_script = file("${path.module}/scripts/telemetry-edge-startup.sh")

  service_account {
    email = google_service_account.platform["telemetry_edge"].email
    # Google recommends cloud-platform scope with authorization constrained by
    # IAM. Legacy granular scopes do not grant permissions and do not cover
    # every authentication protocol, so the narrow roles in iam.tf are the
    # effective least-privilege boundary.
    scopes = ["https://www.googleapis.com/auth/cloud-platform"]
  }

  scheduling {
    automatic_restart   = true
    on_host_maintenance = "MIGRATE"
    preemptible         = false
    provisioning_model  = "STANDARD"
  }

  lifecycle {
    ignore_changes = [
      metadata["telemetry-edge-commit"],
      metadata["telemetry-edge-image"],
    ]
  }

  shielded_instance_config {
    enable_integrity_monitoring = true
    enable_secure_boot          = true
    enable_vtpm                 = true
  }

  depends_on = [google_project_service.required]
}
