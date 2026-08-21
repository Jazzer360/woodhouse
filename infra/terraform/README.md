# Terraform

This is an intentionally empty, valid Terraform root for Phase 1. It proves formatting, initialization, validation, and no-op planning without creating providers or production infrastructure.

Phase 2 will add the shared GCP baseline described in `docs/architecture.md`. Per-user BigQuery datasets remain outside shared Terraform and are created by the idempotent Phase 3 admin workflow.

```bash
terraform fmt -check
terraform init -backend=false
terraform validate
terraform plan -input=false -lock=false
```
