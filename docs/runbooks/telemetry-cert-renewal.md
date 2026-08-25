# Telemetry Certificate Renewal

**Status:** Automated production procedure.

The public receiver is `telemetry.woodhouse.derekjass.com:443`. Tesla requires
a commonly trusted server certificate and terminates mTLS inside the official
Fleet Telemetry receiver. A scheduled Cloud Run Job renews the certificate with
Let's Encrypt DNS-01; there is no recurring manual TXT-record workflow.

## Steady-state contract

- Cloud Scheduler invokes `tpp-telemetry-cert-renewer` twice daily at 05:17
  and 17:17 UTC.
- Cloud Run task retries remain disabled. A failure after ACME issuance but
  before candidate persistence is not provably idempotent, so blindly retrying
  the whole task could request duplicate certificates. Monitoring alerts
  immediately; the next twice-daily run re-evaluates whatever durable state is
  present. Inspect the failure category before an additional manual run.
- The job first validates the stored certificate locally. It does not contact
  ACME or Cloudflare until less than one third of the certificate's issued
  lifetime remains (one half for certificates lasting at most ten days). This
  follows Certbot's current lifetime-relative policy and automatically adapts
  as public certificate lifetimes shorten.
- Once that threshold is crossed, the job explicitly renews the one configured
  lineage. The twice-daily schedule and retries leave many opportunities to
  recover before expiration without issuing unnecessarily early.
- A Cloudflare API token has `Zone:DNS:Edit` for the `derekjass.com` zone only.
  Never use a Global API Key.
- The token is available only through `cloudflare-dns-api-token` in Secret
  Manager and never reaches telemetry-edge.
- The renewer reads the active release's exact public-certificate version to
  decide whether renewal is due without restoring ACME state. It cannot read
  the stored TLS private-key secret; it only adds a new key version after
  generating a replacement in memory.
- Compact Certbot account/lineage state is versioned in
  `telemetry-acme-state` so renewals reuse the ACME account.
- A candidate must have the exact SAN, a complete chain, a matching private
  key, and at least seven days of remaining validity before activation. This
  deployment-safety floor is deliberately separate from the proportional
  renewal threshold and does not reject newly issued 45-day certificates.
- Certificate and key versions are written first. The
  `telemetry-edge-tls-release` manifest is written last and references their
  exact versions and hashes, providing the atomic activation boundary.
- The job resets only `tpp-telemetry-edge`, waits for its guest status to name
  the release, and compares the public leaf fingerprint with the candidate.
- The VM retains the previous pair and restores it if the receiver cannot
  become healthy. The short reset closes the existing receiver connection, so
  the vehicle reconnects and replays unacknowledged data under
  `delivery_policy = "latest"`. Tesla documents a finite 5,000-message buffer
  (at least 2,500 seconds at the maximum collection cadence), so compatible
  routine renewal should delay rather than drop records; an outage exceeding
  that buffer is not lossless and must alert.
- Monitoring alerts on any failed execution and on 23 hours without a
  successful check. The absence threshold represents roughly two missed
  scheduled checks and stays within Cloud Monitoring's maximum 23-hour-30-minute
  metric-absence window. Configure at least one resource in
  `monitoring_notification_channels`; an alert with no notification channel is
  visible in Cloud Monitoring but cannot wake an operator.

## Relationship to the vehicle configuration

Routine leaf renewal must not require reconfiguring a vehicle. Phase 8 must put
stable CA trust material—not the expiring server leaf—into the signed Fleet
Telemetry `ca` field and keep the hostname and port stable. When a candidate
chains to that configured trust profile, the renewal job may rotate the server
certificate without a Tesla API call, vehicle wake, or telemetry-configuration
update.

Tesla requires the configured host and CA to validate the served certificate
and recommends checking the exact combination with its official
[`check_server_cert.sh`](https://github.com/teslamotors/fleet-telemetry/blob/v0.9.4/tools/check_server_cert.sh).
Its documentation does not guarantee that an arbitrary future public-CA chain
change remains compatible. Before the first real vehicle configuration, Phase
8 must therefore add a gate that validates every candidate leaf and chain
against the canonical vehicle trust profile before activation.

If the hostname, port, or CA trust profile must change, do not let this job
silently activate an incompatible certificate and do not give it Tesla
credentials. Keep serving the still-valid certificate, alert, and use the
separate Phase 8 per-vehicle signed reconciler described in
`docs/tesla-onboarding.md`. That reconciler may perform transport-trust
maintenance automatically only for vehicles whose operator has explicitly
enabled managed maintenance. Cutover waits for every required vehicle to report
`synced=true` and for a canary to establish a genuine authenticated connection.

## One-time enablement checkpoint

1. In Cloudflare, create a custom API token with only `Zone:DNS:Edit`, limited
   to the `derekjass.com` zone. Do not grant account-wide access or use the
   Global API Key.
2. Apply Terraform once with automation disabled. This creates the empty secret
   containers without exposing values.
3. In Google Cloud Console, add the token as a new value of
   `cloudflare-dns-api-token`. Do not paste it into chat, Terraform, source, or
   a command argument.
4. In the ignored `infra/terraform/terraform.tfvars`, set:

   ```hcl
   enable_telemetry_certificate_automation = true
   telemetry_certificate_acme_email        = "OPERATOR_EMAIL"
   telemetry_certificate_schedule_paused   = true
   ```

5. Plan, review, and apply. The schedule intentionally remains paused while the
   placeholder job image exists.
6. Deploy the merged image through the normal digest-pinned path:

   ```bash
   gcloud builds triggers run tpp-main-certificate-renewer \
     --project=woodhouse-506215 --region=us-central1 --branch=main
   ```

7. Run and wait for the first execution:

   ```bash
   gcloud run jobs execute tpp-telemetry-cert-renewer \
     --project=woodhouse-506215 --region=us-central1 --wait
   ```

   Require a structured `telemetry_certificate_check` event with status
   `renewed` or `healthy`, guest status containing `:tls=<release>:<hash>`, and
   a valid public handshake. Confirm that the temporary `_acme-challenge` TXT
   record was removed by the plugin.
8. Set `telemetry_certificate_schedule_paused = false`, apply Terraform, and
   execute the Scheduler job once as a final proof.

## Failure and recovery

Do not disable the currently deployed certificate versions during an incident.
The active release manifest is immutable and previous Secret Manager versions
remain available.

1. Inspect the Cloud Run Job execution and its safe `error_type` and
   `error_category`. Certbot failures are reduced to stable categories such as
  `dns_provider_authorization`, `dns_propagation`, `acme_rate_limited`,
  `acme_unavailable`, `certbot_timeout`, or `acme_state_invalid`; logs never contain the
   Cloudflare token, private key, certificate bodies, or raw Certbot output.
2. Confirm the Cloudflare token still has zone-scoped DNS edit access and the
   authoritative nameservers remain Cloudflare.
3. Confirm Cloud Scheduler, Cloud Run, Secret Manager, Compute Engine, and
   external DNS are within their provider SLOs.
4. Re-run the job. If activation previously failed, the unchanged release
   manifest causes another targeted reset and verification attempt.
5. If a candidate itself is bad, disable only its unreferenced versions. Restore
   an earlier `telemetry-edge-tls-release` version as latest only after checking
   its referenced certificate is not near expiry, then reset the edge.

Certificate renewal changes no Tesla OAuth token, Virtual Key, command key,
vehicle ownership mapping, Fleet Telemetry configuration, or historical row.
That statement applies to compatible leaf renewal. A CA or endpoint migration
is a separate, audited Phase 8 operation and is never performed by this job.

The credential format and least-privilege token recommendation follow the
official [Certbot Cloudflare plugin documentation](https://certbot-dns-cloudflare.readthedocs.io/en/stable/).
