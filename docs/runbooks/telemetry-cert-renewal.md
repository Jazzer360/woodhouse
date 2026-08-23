# Telemetry Certificate Renewal

**Status:** Phase 7 operational procedure; re-verify during Phase 11.

The public receiver is `telemetry.woodhouse.derekjass.com:443`. It terminates
Tesla mTLS inside the official Fleet Telemetry receiver. Its leaf/full-chain
certificate and private key are stored only as Secret Manager versions named
`telemetry-edge-tls-cert` and `telemetry-edge-tls-key`. Terraform creates the
containers and IAM, never the values.

## Renewal

1. Renew with the existing commonly trusted public CA before 30 days remain.
   For manual Let's Encrypt DNS-01, use the command in the
   [Phase 7 checkpoint](../deployment.md#3-issue-validate-and-upload-the-tls-certificate).
2. Confirm the SAN is exactly `telemetry.woodhouse.derekjass.com`, the chain is
   complete, `openssl x509 -checkend 2592000` succeeds, and the leaf/private-key
   public-key SHA-256 values match.
3. Add new secret versions with `gcloud secrets versions add --data-file`.
   Never paste PEM data into a shell argument, log, Terraform variable, or Git.
4. Reset `tpp-telemetry-edge` through the reviewed exact-digest deployment or:

   ```bash
   gcloud compute instances reset tpp-telemetry-edge \
     --project=woodhouse-506215 --zone=us-central1-a
   ```

5. Require guest status `success:<deployed-commit>`, local `/status`, and public
   `openssl s_client -verify_return_error` success. Run Tesla's pinned
   `v0.9.4` `tools/check_server_cert.sh` as documented in deployment notes.
6. Leave the previous enabled secret versions available until the public
   handshake and synthetic ingestion proof pass. Secret Manager `latest` is
   loaded only at receiver restart.

## Rollback

If the receiver cannot start or the public chain fails, disable the new cert
and key versions, ensure the previous pair are the latest enabled versions, and
reset the VM. Do not rotate only one half of the pair. Verify guest health and
public TLS again before closing the incident.

Certificate renewal changes no Tesla OAuth token, Virtual Key, command key,
vehicle ownership mapping, or historical record.
