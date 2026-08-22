# Terraform

This root defines the shared GCP baseline for project `woodhouse-506215` in
`us-central1`. Phase 6 adds optional instance-local Vehicle Command Proxy sidecar
configuration and two empty TLS secret containers. Terraform still creates no
per-user BigQuery datasets and no secret values.

Set `user_admin_principals` for operators who may impersonate
`tpp-user-admin`. Set `oidc_audience` to the Google OAuth client ID, then set
`enable_mcp_external_access = true` only after the authenticated gateway image
is deployed. See [deployment.md](../../docs/deployment.md) for the complete IAM
and manual add-user workflow.

Leave `enable_tesla_onboarding = false` until the documented Secret Manager
versions exist. Then configure the non-secret Tesla client ID, application
domain, and exact callback URI in the uncommitted tfvars file and enable it. See
the [Phase 4 operator checkpoint](../../docs/tesla-onboarding.md#12-required-operator-checkpoint--first-real-tesla-onboarding).

Enable `enable_tesla_command_proxy` only after adding the separate proxy TLS
certificate/key secret versions and selecting the official proxy image by full
digest. See [deployment notes](../../docs/deployment.md#phase-6-vehicle-command-proxy-deployment).

Set `cloud_build_repository` to the existing regional Cloud Build v2 repository
resource. Terraform manages the PR and main-branch triggers but deliberately
does not own the interactive GitHub App authorization/connection. Import live
triggers before the first plan; see the deployment notes for the exact commands.

## State bootstrap

The `bootstrap/` root creates the versioned, private GCS bucket used by the shared root. Its one-resource local state is intentionally separate because Terraform cannot create its own backend bucket while using that backend.

```bash
cp infra/terraform/bootstrap/terraform.tfvars.example \
  infra/terraform/bootstrap/terraform.tfvars
cp infra/terraform/terraform.tfvars.example \
  infra/terraform/terraform.tfvars

terraform -chdir=infra/terraform/bootstrap init
terraform -chdir=infra/terraform/bootstrap plan \
  -var-file=terraform.tfvars -out=bootstrap.tfplan
terraform -chdir=infra/terraform/bootstrap apply bootstrap.tfplan

terraform -chdir=infra/terraform init \
  -backend-config=backend.gcs.tfbackend.example
```

Keep the bootstrap state secure until the bucket is established. It is ignored by Git and can be reconstructed by importing the bucket if necessary.

## Validate without GCP resource access

PR validation disables refresh and produces a create-only speculative plan from a temporary copy with the backend declaration removed. The Google provider still requires an identity, but the Cloud Build validator has no GCP resource read or mutation roles. The checked-in Cloud Build configuration performs the temporary-copy step.

```bash
terraform -chdir=infra/terraform/bootstrap fmt -check
terraform -chdir=infra/terraform/bootstrap init -backend=false
terraform -chdir=infra/terraform/bootstrap validate
terraform -chdir=infra/terraform/bootstrap plan \
  -refresh=false -input=false -lock=false \
  -var=project_id=woodhouse-506215 \
  -var=state_bucket_name=woodhouse-506215-tpp-tfstate

terraform -chdir=infra/terraform fmt -check
terraform -chdir=infra/terraform init -backend=false
terraform -chdir=infra/terraform validate
```

A normal plan from the shared root requires the bootstrapped GCS backend and operator Application Default Credentials; use the live-plan commands below for that authoritative review.

## Live plan and apply

After authenticating with the documented bootstrap permissions and initializing the GCS backend:

```bash
terraform -chdir=infra/terraform plan \
  -var-file=terraform.tfvars -out=shared.tfplan
terraform -chdir=infra/terraform apply shared.tfplan
```

Both roots require an explicit project ID, and bootstrap additionally requires
an explicit globally unique state-bucket name. The checked-in examples document
the intended deployment without making a bare `terraform apply` target the real
project automatically.

Review saved plans before applying. Never commit plan or state files. See [deployment.md](../../docs/deployment.md) for resources, IAM, bootstrap permissions, imports, and operating boundaries.

Manual Cloud Build submissions must stage source in the dedicated private
`${project_id}-tpp-cloudbuild-source` bucket. Pass its `source` prefix through
`gcloud builds submit --gcs-source-staging-dir`; do not grant the deployer
project-wide Storage Object Viewer, which would expose the Terraform state
bucket.
