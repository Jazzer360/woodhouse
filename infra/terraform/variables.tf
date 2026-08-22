variable "project_id" {
  description = "GCP project that hosts the Tesla Personal Platform."
  type        = string
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

variable "oidc_audience" {
  description = "Google OIDC OAuth client ID accepted by mcp-gateway; null keeps auth startup fail-closed."
  type        = string
  default     = null
  nullable    = true

  validation {
    condition     = var.oidc_audience == null ? true : length(trimspace(var.oidc_audience)) > 0
    error_message = "oidc_audience must be null or a non-empty OAuth client ID."
  }
}

variable "enable_mcp_external_access" {
  description = "Allow internet clients to reach mcp-gateway after application-level OIDC enforcement is configured."
  type        = bool
  default     = false
}

variable "enable_tesla_onboarding" {
  description = "Inject Tesla onboarding configuration and Secret Manager versions into mcp-gateway."
  type        = bool
  default     = false
}

variable "enable_tesla_command_proxy" {
  description = "Run the official Tesla Vehicle Command Proxy as a non-ingress mcp-gateway sidecar."
  type        = bool
  default     = false
}

variable "tesla_command_proxy_image" {
  description = "Official tesla/vehicle-command image pinned by sha256 digest; required when the proxy is enabled."
  type        = string
  default     = null
  nullable    = true

  validation {
    condition = var.tesla_command_proxy_image == null ? true : can(regex(
      "^tesla/vehicle-command@sha256:[0-9a-f]{64}$",
      var.tesla_command_proxy_image
    ))
    error_message = "tesla_command_proxy_image must be the official image pinned by a full sha256 digest."
  }
}

variable "tesla_client_id" {
  description = "Tesla developer application client ID; not a secret."
  type        = string
  default     = null
  nullable    = true
}

variable "tesla_app_domain" {
  description = "Bare hostname registered as the Tesla developer application domain."
  type        = string
  default     = null
  nullable    = true
}

variable "tesla_oauth_redirect_uri" {
  description = "Exact HTTPS callback URI registered with the Tesla developer application."
  type        = string
  default     = null
  nullable    = true
}

variable "tesla_initial_audience" {
  description = "Initial regional Fleet API audience used for OAuth code exchange and region discovery."
  type        = string
  default     = "https://fleet-api.prd.na.vn.cloud.tesla.com"

  validation {
    condition = contains([
      "https://fleet-api.prd.na.vn.cloud.tesla.com",
      "https://fleet-api.prd.eu.vn.cloud.tesla.com",
      "https://fleet-api.prd.cn.vn.cloud.tesla.cn",
    ], var.tesla_initial_audience)
    error_message = "tesla_initial_audience must be a documented Tesla Fleet API regional base URL."
  }
}

variable "admin_principals" {
  description = "IAM members allowed to administer telemetry-edge through IAP and OS Login."
  type        = set(string)
  default     = []
}

variable "user_admin_principals" {
  description = "IAM members allowed to impersonate the keyless manual user-administration service account."
  type        = set(string)
  default     = []
}

variable "partner_admin_principals" {
  description = "IAM members allowed to impersonate the keyless Tesla partner-registration service account."
  type        = set(string)
  default     = []
}

variable "monitoring_notification_channels" {
  description = "Existing Monitoring notification-channel resource names attached to alerts."
  type        = list(string)
  default     = []
}
