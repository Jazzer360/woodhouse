# Telemetry Certificate Renewal

**Status:** Automated production procedure.

The public receiver is `telemetry.woodhouse.derekjass.com:443`. Tesla requires
a commonly trusted server certificate and terminates mTLS inside the official
Fleet Telemetry receiver. A scheduled Cloud Run Job renews the certificate with
Let's Encrypt DNS-01; there is no recurring manual TXT-record workflow.

## Steady-state contract

- Cloud Scheduler invokes `tpp-telemetry-cert-renewer` daily at 05:17 UTC.
- Certbot renews only inside its normal renewal window.
- A Cloudflare API token has `Zone:DNS:Edit` for the `derekjass.com` zone only.
  Never use a Global API Key.
- The token is available only through `cloudflare-dns-api-token` in Secret
  Manager and never reaches telemetry-edge.
- Compact Certbot account/lineage state is versioned in
  `telemetry-acme-state` so renewals reuse the ACME account.
- A candidate must have the exact SAN, a complete chain, a matching private
  key, and at least 45 days of remaining validity before activation.
- Certificate and key versions are written first. The
  `telemetry-edge-tls-release` manifest is written last and references their
  exact versions and hashes, providing the atomic activation boundary.
- The job resets only `tpp-telemetry-edge`, waits for its guest status to name
  the release, and compares the public leaf fingerprint with the candidate.
- The VM retains the previous pair and restores it if the receiver cannot
  become healthy. Vehicles may reconnect during the short reset; reliable
  acknowledgements and vehicle buffering protect the raw stream.
- Monitoring alerts on any failed execution and on 48 hours without a
  successful check. Configure at least one resource in
  `monitoring_notification_channels`; an alert with no notification channel is
  visible in Cloud Monitoring but cannot wake an operator.

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

1. Inspect the Cloud Run Job execution and its safe `error_type`; logs never
   contain the Cloudflare token, private key, certificate bodies, or Certbot
   output.
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

The credential format and least-privilege token recommendation follow the
official [Certbot Cloudflare plugin documentation](https://certbot-dns-cloudflare.readthedocs.io/en/stable/).
