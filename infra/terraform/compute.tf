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
    enable-oslogin            = "TRUE"
    google-logging-enabled    = "true"
    google-monitoring-enabled = "true"
  }

  service_account {
    email  = google_service_account.platform["telemetry_edge"].email
    scopes = ["https://www.googleapis.com/auth/cloud-platform"]
  }

  scheduling {
    automatic_restart   = true
    on_host_maintenance = "MIGRATE"
    preemptible         = false
    provisioning_model  = "STANDARD"
  }

  shielded_instance_config {
    enable_integrity_monitoring = true
    enable_secure_boot          = true
    enable_vtpm                 = true
  }

  depends_on = [google_project_service.required]
}
