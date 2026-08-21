# Terraform

This root defines the Phase 2 shared GCP baseline for project `woodhouse-506215` in `us-central1`. It creates no per-user BigQuery datasets and no secret values.

## State bootstrap

The `bootstrap/` root creates the versioned, private GCS bucket used by the shared root. Its one-resource local state is intentionally separate because Terraform cannot create its own backend bucket while using that backend.

```bash
terraform -chdir=infra/terraform/bootstrap init
terraform -chdir=infra/terraform/bootstrap plan -out=bootstrap.tfplan
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
terraform -chdir=infra/terraform/bootstrap plan -refresh=false -input=false -lock=false

terraform -chdir=infra/terraform fmt -check
terraform -chdir=infra/terraform init -backend=false
terraform -chdir=infra/terraform validate
```

A normal plan from the shared root requires the bootstrapped GCS backend and operator Application Default Credentials; use the live-plan commands below for that authoritative review.

## Live plan and apply

After authenticating with the documented bootstrap permissions and initializing the GCS backend:

```bash
terraform -chdir=infra/terraform plan -out=shared.tfplan
terraform -chdir=infra/terraform apply shared.tfplan
```

Review saved plans before applying. Never commit plan or state files. See [deployment.md](../../docs/deployment.md) for resources, IAM, bootstrap permissions, imports, and operating boundaries.
