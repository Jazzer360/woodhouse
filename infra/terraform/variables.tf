variable "project_id" {
  description = "GCP project that hosts the Tesla Personal Platform."
  type        = string
  default     = "woodhouse-506215"
}

variable "region" {
  description = "Primary GCP region for regional platform resources."
  type        = string
  default     = "us-central1"
}

variable "zone" {
  description = "Compute Engine zone for the telemetry-edge VM."
  type        = string
  default     = "us-central1-a"
}

variable "fleet_telemetry_port" {
  description = "Public TCP port configured for the future Fleet Telemetry receiver."
  type        = number
  default     = 443

  validation {
    condition     = var.fleet_telemetry_port >= 1 && var.fleet_telemetry_port <= 65535
    error_message = "fleet_telemetry_port must be a valid TCP port."
  }
}

variable "cloud_run_placeholder_image" {
  description = "Public placeholder used only until commit-addressed project images are deployed."
  type        = string
  default     = "us-docker.pkg.dev/cloudrun/container/hello"
}

variable "admin_principals" {
  description = "IAM members allowed to administer telemetry-edge through IAP and OS Login."
  type        = set(string)
  default     = []
}

variable "monitoring_notification_channels" {
  description = "Existing Monitoring notification-channel resource names attached to alerts."
  type        = list(string)
  default     = []
}
