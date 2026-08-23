# Tesla's official Fleet Telemetry v0.9.4 multi-platform image, resolved
# 2026-08-23. The application image is immutable and contains no custom wire
# protocol or telemetry processing code.
FROM tesla/fleet-telemetry@sha256:28c8b9e244b842a3d7443567cfa385b4db20cf533b8dee3411ce6fe540eb67e2

COPY services/telemetry-edge/config.json /etc/fleet-telemetry/config.json

EXPOSE 443 8080 9090

CMD ["/fleet-telemetry", "-config", "/etc/fleet-telemetry/config.json"]
