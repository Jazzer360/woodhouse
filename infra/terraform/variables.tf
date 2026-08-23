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
  description = "Public TCP port shared by the receiver, firewall, certificate validation, and vehicle configuration."
  type        = number
  default     = 443

  validation {
    condition     = var.fleet_telemetry_port == 443
    error_message = "The deployed Phase 7 receiver configuration requires fleet_telemetry_port=443."
  }
}

variable "fleet_telemetry_receiver_version" {
  description = "Reviewed Tesla Fleet Telemetry receiver version embedded in the digest-pinned edge image."
  type        = string
  default     = "v0.9.4"

  validation {
    condition     = var.fleet_telemetry_receiver_version == "v0.9.4"
    error_message = "fleet_telemetry_receiver_version must match the reviewed v0.9.4 image digest."
  }
}

variable "telemetry_hostname" {
  description = "Public DNS hostname whose certificate is mounted into telemetry-edge."
  type        = string
  default     = "telemetry.woodhouse.derekjass.com"

  validation {
    condition     = can(regex("^[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?$", var.telemetry_hostname))
    error_message = "telemetry_hostname must be a lowercase DNS hostname."
  }
}

variable "enable_telemetry_edge_delivery" {
  description = "Enable edge TLS access and the post-merge exact-digest VM delivery trigger after the operator checkpoint."
  type        = bool
  default     = false
}

variable "cloud_run_placeholder_image" {
  description = "Public placeholder used only until commit-addressed project images are deployed."
  type        = string
  default     = "us-docker.pkg.dev/cloudrun/container/hello"
}

variable "cloud_build_repository" {
  description = "Existing regional Cloud Build v2 repository resource used by Terraform-managed triggers; the interactive GitHub connection remains an external bootstrap step."
  type        = string
  default     = null
  nullable    = true

  validation {
    condition = var.cloud_build_repository == null ? true : can(regex(
      "^projects/[^/]+/locations/[^/]+/connections/[^/]+/repositories/[^/]+$",
      var.cloud_build_repository
    ))
    error_message = "cloud_build_repository must be null or a full Cloud Build v2 repository resource name."
  }
}

variable "oidc_audience" {
  description = "Legacy direct Google ID-token audience; retained during the platform-OIDC migration."
  type        = string
  default     = null
  nullable    = true

  validation {
    condition     = var.oidc_audience == null ? true : length(trimspace(var.oidc_audience)) > 0
    error_message = "oidc_audience must be null or a non-empty OAuth client ID."
  }
}

variable "enable_platform_oidc" {
  description = "Enable standards-compliant MCP OAuth and browser onboarding through the configured OIDC provider."
  type        = bool
  default     = false
}

variable "platform_oidc_issuer" {
  description = "HTTPS issuer URL for the established OAuth/OIDC provider (Auth0 is the documented default)."
  type        = string
  default     = null
  nullable    = true
}

variable "platform_oidc_resource_url" {
  description = "Canonical protected MCP resource URL, normally https://<domain>/mcp."
  type        = string
  default     = null
  nullable    = true
}

variable "platform_oidc_client_id" {
  description = "Non-secret OIDC client ID used only by the browser onboarding flow."
  type        = string
  default     = null
  nullable    = true
}

variable "platform_oidc_redirect_uri" {
  description = "Exact HTTPS browser sign-in callback URI registered with the OIDC provider."
  type        = string
  default     = null
  nullable    = true
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
