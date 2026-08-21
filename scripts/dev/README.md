# Development scripts

Keep local helpers small and equivalent to the documented commands in the repository README and `cloudbuild.pr.yaml`. Phase 1 uses direct `uv`, Terraform, and Docker commands so a wrapper cannot hide safety-relevant behavior.

Development and CI must use mocks/fakes in later phases and must never contact or command a real vehicle.
