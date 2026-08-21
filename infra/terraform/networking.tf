resource "google_compute_network" "platform" {
  project                 = var.project_id
  name                    = "tpp-network"
  auto_create_subnetworks = false
  routing_mode            = "REGIONAL"

  depends_on = [google_project_service.required]
}

resource "google_compute_subnetwork" "platform" {
  project                  = var.project_id
  name                     = "tpp-${var.region}"
  region                   = var.region
  network                  = google_compute_network.platform.id
  ip_cidr_range            = "10.42.0.0/28"
  private_ip_google_access = true
  stack_type               = "IPV4_ONLY"
}

resource "google_compute_address" "telemetry_edge" {
  project      = var.project_id
  name         = "tpp-telemetry-edge"
  region       = var.region
  address_type = "EXTERNAL"
  network_tier = "PREMIUM"

  depends_on = [google_project_service.required]
}

resource "google_compute_firewall" "fleet_telemetry" {
  project     = var.project_id
  name        = "tpp-allow-fleet-telemetry"
  network     = google_compute_network.platform.name
  description = "Public ingress only to the configured Fleet Telemetry receiver port."
  direction   = "INGRESS"
  priority    = 1000

  source_ranges           = ["0.0.0.0/0"]
  target_service_accounts = [google_service_account.platform["telemetry_edge"].email]

  allow {
    protocol = "tcp"
    ports    = [tostring(var.fleet_telemetry_port)]
  }

  log_config {
    metadata = "INCLUDE_ALL_METADATA"
  }
}

resource "google_compute_firewall" "iap_ssh" {
  project     = var.project_id
  name        = "tpp-allow-iap-ssh"
  network     = google_compute_network.platform.name
  description = "SSH administration through Google IAP TCP forwarding only."
  direction   = "INGRESS"
  priority    = 1000

  source_ranges           = ["35.235.240.0/20"]
  target_service_accounts = [google_service_account.platform["telemetry_edge"].email]

  allow {
    protocol = "tcp"
    ports    = ["22"]
  }

  log_config {
    metadata = "INCLUDE_ALL_METADATA"
  }
}
