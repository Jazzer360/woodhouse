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

resource "google_service_account_iam_member" "deployer_runtime_user" {
  for_each = toset(["mcp_gateway", "telemetry_processor"])

  service_account_id = google_service_account.platform[each.value].name
  role               = "roles/iam.serviceAccountUser"
  member             = "serviceAccount:${google_service_account.platform["cloud_build_deployer"].email}"
}

resource "google_service_account_iam_member" "cloud_build_identity_token_creator" {
  for_each = toset(["cloud_build_validator", "cloud_build_deployer"])

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
  description = "Create opaque per-user BigQuery datasets and update their metadata and ACLs."
  stage       = "GA"
  permissions = [
    "bigquery.datasets.create",
    "bigquery.datasets.get",
    "bigquery.datasets.update",
  ]
}

resource "google_project_iam_member" "user_admin_dataset_provisioner" {
  project = var.project_id
  role    = google_project_iam_custom_role.user_dataset_provisioner.id
  member  = "serviceAccount:${google_service_account.platform["user_admin"].email}"
}

resource "google_service_account_iam_member" "user_admin_impersonator" {
  for_each = var.user_admin_principals

  service_account_id = google_service_account.platform["user_admin"].name
  role               = "roles/iam.serviceAccountTokenCreator"
  member             = each.value
}
