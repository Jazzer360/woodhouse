# Tesla's official Fleet Telemetry v0.9.4 multi-platform image, resolved
# 2026-08-23. The application image is immutable and contains no custom wire
# protocol or telemetry processing code.
FROM tesla/fleet-telemetry@sha256:28c8b9e244b842a3d7443567cfa385b4db20cf533b8dee3411ce6fe540eb67e2

EXPOSE 443 8080 9090

# Terraform injects the deployment project into the checked-in base config;
# the VM startup script writes and mounts that generated config read-only.
CMD ["/fleet-telemetry", "-config", "/etc/fleet-telemetry/config.json"]
