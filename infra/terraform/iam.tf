locals {
  project_iam = {
    mcp_bigquery_jobs = {
      role   = "roles/bigquery.jobUser"
      member = "serviceAccount:${google_service_account.platform["mcp_gateway"].email}"
    }
    mcp_firestore = {
      role   = "roles/datastore.user"
      member = "serviceAccount:${google_service_account.platform["mcp_gateway"].email}"
    }
    processor_firestore = {
      role   = "roles/datastore.user"
      member = "serviceAccount:${google_service_account.platform["telemetry_processor"].email}"
    }
    edge_log_writer = {
      role   = "roles/logging.logWriter"
      member = "serviceAccount:${google_service_account.platform["telemetry_edge"].email}"
    }
    edge_metric_writer = {
      role   = "roles/monitoring.metricWriter"
      member = "serviceAccount:${google_service_account.platform["telemetry_edge"].email}"
    }
    validator_log_writer = {
      role   = "roles/logging.logWriter"
      member = "serviceAccount:${google_service_account.platform["cloud_build_validator"].email}"
    }
    deployer_log_writer = {
      role   = "roles/logging.logWriter"
      member = "serviceAccount:${google_service_account.platform["cloud_build_deployer"].email}"
    }
    deployer_cloud_run = {
      role   = "roles/run.developer"
      member = "serviceAccount:${google_service_account.platform["cloud_build_deployer"].email}"
    }
    analytics_view_reconciler_log_writer = {
      role   = "roles/logging.logWriter"
      member = "serviceAccount:${google_service_account.platform["analytics_view_reconciler"].email}"
    }
    user_admin_firestore = {
      role   = "roles/datastore.user"
      member = "serviceAccount:${google_service_account.platform["user_admin"].email}"
    }
  }
}

resource "google_project_iam_member" "platform" {
  for_each = local.project_iam

  project = var.project_id
  role    = each.value.role
  member  = each.value.member
}

resource "google_project_iam_member" "cloud_scheduler_service_agent" {
  count = var.enable_telemetry_certificate_automation ? 1 : 0

  project = var.project_id
  role    = "roles/cloudscheduler.serviceAgent"
  member  = "serviceAccount:${google_project_service_identity.cloud_scheduler.email}"
}

resource "google_project_iam_custom_role" "telemetry_edge_topic_inspector" {
  project     = var.project_id
  role_id     = "tppTelemetryEdgeTopicInspector"
  title       = "TPP telemetry edge topic inspector"
  description = "Allow the official receiver to confirm Terraform-owned Pub/Sub topics exist."
  stage       = "GA"
  permissions = ["pubsub.topics.get"]
}

resource "google_project_iam_member" "telemetry_edge_topic_inspector" {
  project = var.project_id
  role    = google_project_iam_custom_role.telemetry_edge_topic_inspector.id
  member  = "serviceAccount:${google_service_account.platform["telemetry_edge"].email}"
}

resource "google_artifact_registry_repository_iam_member" "telemetry_edge_reader" {
  project    = var.project_id
  location   = google_artifact_registry_repository.platform.location
  repository = google_artifact_registry_repository.platform.name
  role       = "roles/artifactregistry.reader"
  member     = "serviceAccount:${google_service_account.platform["telemetry_edge"].email}"
}

resource "google_project_iam_custom_role" "telemetry_edge_deployer" {
  project     = var.project_id
  role_id     = "tppTelemetryEdgeDeployer"
  title       = "TPP telemetry edge deployer"
  description = "Set an exact edge image, restart its VM, and read guest deployment status."
  stage       = "GA"
  permissions = [
    "compute.instances.get",
    "compute.instances.getGuestAttributes",
    "compute.instances.reset",
    "compute.instances.setMetadata",
  ]
}

resource "google_project_iam_member" "telemetry_edge_deployer" {
  count = var.enable_telemetry_edge_delivery ? 1 : 0

  project = var.project_id
  role    = google_project_iam_custom_role.telemetry_edge_deployer.id
  member  = "serviceAccount:${google_service_account.platform["cloud_build_deployer"].email}"

  condition {
    title       = "telemetry-edge-instance-only"
    description = "Restrict VM delivery to the single telemetry edge instance."
    expression  = "resource.name == 'projects/${var.project_id}/zones/${var.zone}/instances/${google_compute_instance.telemetry_edge.name}'"
  }
}

resource "google_service_account_iam_member" "deployer_runtime_user" {
  for_each = toset(["certificate_renewer", "mcp_gateway", "telemetry_processor"])

  service_account_id = google_service_account.platform[each.value].name
  role               = "roles/iam.serviceAccountUser"
  member             = "serviceAccount:${google_service_account.platform["cloud_build_deployer"].email}"
}

resource "google_project_iam_custom_role" "certificate_renewer_edge_reloader" {
  count = var.enable_telemetry_certificate_automation ? 1 : 0

  project     = var.project_id
  role_id     = "tppCertificateEdgeReloader"
  title       = "TPP certificate edge reloader"
  description = "Restart the single telemetry edge VM and verify its certificate release status."
  stage       = "GA"
  permissions = [
    "compute.instances.getGuestAttributes",
    "compute.instances.reset",
  ]
}

resource "google_project_iam_member" "certificate_renewer_edge_reloader" {
  count = var.enable_telemetry_certificate_automation ? 1 : 0

  project = var.project_id
  role    = google_project_iam_custom_role.certificate_renewer_edge_reloader[0].id
  member  = "serviceAccount:${google_service_account.platform["certificate_renewer"].email}"

  condition {
    title       = "telemetry-edge-instance-only"
    description = "Restrict certificate reloads to the single telemetry edge instance."
    expression  = "resource.name == 'projects/${var.project_id}/zones/${var.zone}/instances/${google_compute_instance.telemetry_edge.name}'"
  }
}

resource "google_service_account_iam_member" "deployer_edge_runtime_user" {
  count = var.enable_telemetry_edge_delivery ? 1 : 0

  service_account_id = google_service_account.platform["telemetry_edge"].name
  role               = "roles/iam.serviceAccountUser"
  member             = "serviceAccount:${google_service_account.platform["cloud_build_deployer"].email}"
}

resource "google_service_account_iam_member" "cloud_build_identity_token_creator" {
  for_each = toset([
    "analytics_view_reconciler",
    "cloud_build_validator",
    "cloud_build_deployer",
  ])

  service_account_id = google_service_account.platform[each.value].name
  role               = "roles/iam.serviceAccountTokenCreator"
  member             = "serviceAccount:${google_project_service_identity.cloud_build.email}"
}

resource "google_project_iam_member" "admin_iap_tunnel" {
  for_each = var.admin_principals

  project = var.project_id
  role    = "roles/iap.tunnelResourceAccessor"
  member  = each.value
}

resource "google_project_iam_member" "admin_os_login" {
  for_each = var.admin_principals

  project = var.project_id
  role    = "roles/compute.osAdminLogin"
  member  = each.value
}

resource "google_project_iam_member" "admin_compute_viewer" {
  for_each = var.admin_principals

  project = var.project_id
  role    = "roles/compute.viewer"
  member  = each.value
}

resource "google_project_iam_member" "admin_bigquery_job_user" {
  for_each = var.admin_principals

  project = var.project_id
  role    = "roles/bigquery.jobUser"
  member  = each.value
}

resource "google_service_account_iam_member" "admin_edge_service_account_user" {
  for_each = var.admin_principals

  service_account_id = google_service_account.platform["telemetry_edge"].name
  role               = "roles/iam.serviceAccountUser"
  member             = each.value
}

resource "google_service_account_iam_member" "admin_build_service_account_user" {
  for_each = {
    for pair in setproduct(var.admin_principals, toset(["cloud_build_validator", "cloud_build_deployer"])) :
    "${pair[0]}|${pair[1]}" => {
      principal = pair[0]
      account   = pair[1]
    }
  }

  service_account_id = google_service_account.platform[each.value.account].name
  role               = "roles/iam.serviceAccountUser"
  member             = each.value.principal
}

resource "google_project_iam_custom_role" "user_dataset_provisioner" {
  project     = var.project_id
  role_id     = "tppUserDatasetProvisioner"
  title       = "TPP user dataset provisioner"
  description = "Create opaque per-user BigQuery datasets, raw tables, metadata, and ACLs."
  stage       = "GA"
  permissions = [
    "bigquery.datasets.create",
    "bigquery.datasets.get",
    "bigquery.datasets.update",
    "bigquery.tables.create",
    "bigquery.tables.get",
    "bigquery.tables.update",
  ]
}

resource "google_project_iam_member" "user_admin_dataset_provisioner" {
  project = var.project_id
  role    = google_project_iam_custom_role.user_dataset_provisioner.id
  member  = "serviceAccount:${google_service_account.platform["user_admin"].email}"
}

resource "google_project_iam_custom_role" "analytics_view_reconciler" {
  project     = var.project_id
  role_id     = "tppAnalyticsViewReconciler"
  title       = "TPP analytics view reconciler"
  description = "List active tenants and synchronize only their managed BigQuery views."
  stage       = "GA"
  permissions = [
    "bigquery.datasets.get",
    "bigquery.datasets.update",
    "bigquery.tables.create",
    "bigquery.tables.delete",
    "bigquery.tables.get",
    "bigquery.tables.list",
    "bigquery.tables.update",
    "datastore.entities.get",
    "datastore.entities.list",
  ]
}

resource "google_project_iam_member" "analytics_view_reconciler" {
  project = var.project_id
  role    = google_project_iam_custom_role.analytics_view_reconciler.id
  member  = "serviceAccount:${google_service_account.platform["analytics_view_reconciler"].email}"
}

resource "google_service_account_iam_member" "user_admin_impersonator" {
  for_each = var.user_admin_principals

  service_account_id = google_service_account.platform["user_admin"].name
  role               = "roles/iam.serviceAccountTokenCreator"
  member             = each.value
}

resource "google_service_account_iam_member" "partner_admin_impersonator" {
  for_each = var.partner_admin_principals

  service_account_id = google_service_account.platform["partner_admin"].name
  role               = "roles/iam.serviceAccountTokenCreator"
  member             = each.value
}
