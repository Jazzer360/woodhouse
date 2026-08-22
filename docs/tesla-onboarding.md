# Tesla Developer Application and User Onboarding

**Last verified against Tesla official docs:** 2026-08-22

Re-check current Tesla docs before implementation.

Official references:

- https://developer.tesla.com/docs/fleet-api/authentication/overview
- https://developer.tesla.com/docs/fleet-api/authentication/third-party-tokens
- https://developer.tesla.com/docs/fleet-api/authentication/partner-tokens
- https://developer.tesla.com/docs/fleet-api/virtual-keys/developer-guide
- https://developer.tesla.com/docs/fleet-api/endpoints/partner-endpoints
- https://developer.tesla.com/docs/fleet-api/endpoints/user-endpoints
- https://developer.tesla.com/docs/fleet-api/endpoints/vehicle-endpoints
- https://developer.tesla.com/docs/fleet-api/getting-started/regions-countries
- https://developer.tesla.com/docs/fleet-api/billing-and-limits
- https://developer.tesla.com/docs/fleet-api/fleet-telemetry

---

## 1. Developer app configuration

Canonical production application origin: `https://woodhouse.derekjass.com`.

Canonical production Tesla OAuth redirect URI: `https://woodhouse.derekjass.com/oauth/callback`.

Create one Tesla Fleet API application.

Planned values:

```text
OAuth grant type:
Authorization Code and Machine-to-Machine

Allowed origin:
https://woodhouse.derekjass.com

Optional local origin:
http://localhost:8080

Allowed redirect URI:
https://woodhouse.derekjass.com/oauth/callback

Optional local redirect:
http://localhost:8080/oauth/callback

Returned URL:
leave blank unless current Tesla configuration requires one
```

---

## 2. OAuth scopes

Initial expected third-party user scopes:

```text
openid
offline_access
vehicle_device_data
vehicle_location
vehicle_cmds
vehicle_charging_cmds
```

Do not request `user_data` unless a concrete feature needs Tesla profile/contact/home-address data.

Current Tesla scope notes:

- `vehicle_device_data` -> live/device/service/ownership data;
- `vehicle_location` -> precise/coarse location data;
- `vehicle_cmds` -> normal vehicle command/access capabilities;
- `vehicle_charging_cmds` -> charging history/amount/location and charging controls.

Tesla's current third-party authorization page requires `state`, documents
`nonce` as optional replay prevention, and does not document PKCE parameters.
The live third-party OpenID discovery document likewise does not advertise
`code_challenge_methods_supported`. This implementation therefore uses a
single-use, ten-minute server-side state binding plus a signed-ID-token nonce
check. It intentionally does not send undocumented PKCE parameters. Re-audit
this choice whenever Tesla begins advertising or requiring PKCE.

---

## 3. Generate application EC key pair

Tesla currently requires `prime256v1` / secp256r1.

Example:

```bash
openssl ecparam -name prime256v1 -genkey -noout -out private-key.pem
openssl ec -in private-key.pem -pubout -out public-key.pem
```

Private key -> Secret Manager.  
Public key -> public endpoint.

Required endpoint:

```text
https://woodhouse.derekjass.com/.well-known/appspecific/com.tesla.3p.public-key.pem
```

The private key is never hosted.

---

## 4. Register developer application with Tesla

After the public key endpoint is live:

1. obtain a Tesla partner token using the developer application's client credentials;
2. call `POST /api/1/partner_accounts` for the required region;
3. verify with `GET /api/1/partner_accounts/public_key?domain=...`;
4. keep the public key hosted permanently.

Implement this as an idempotent admin script, not as a public MCP operation.

---

## 5. Platform user onboarding

Admin first adds the user to the platform allowlist.

Desired workflow:

```text
add-user homer@example.com
        |
        +-- Firestore allowlist record
        +-- opaque internal user_id
        +-- per-user BigQuery dataset
```

The user then signs into the platform. On first verified login, bind immutable OIDC identity.

---

## 6. Tesla OAuth authorization-code flow

MCP gateway exposes:

```text
GET /tesla/oauth/start
GET /oauth/callback
```

Start endpoint:

- authenticated platform user required;
- generate/record state;
- include current required Tesla scopes;
- redirect to `https://auth.tesla.com/oauth2/v3/authorize`;
- use `show_keypair_step=true` if immediately leading into virtual-key pairing.

Callback:

- validate state;
- exchange authorization code at Tesla token endpoint;
- save per-user refresh token state;
- call Tesla region endpoint if needed;
- enumerate vehicles;
- create/update registry records.

Current Tesla token endpoint:

```text
POST https://fleet-auth.prd.vn.cloud.tesla.com/oauth2/v3/token
```

Tesla currently documents refresh tokens as single-use with three-month expiry and a short recovery window for the most recently used token. Re-check exact behavior when implementing.

As verified on 2026-08-22, the recovery window is up to 24 hours. The gateway
encrypts both access and refresh tokens before storing them in Firestore. A
successful refresh atomically replaces the credential blob using a token
version compare-and-swap; `401 login_required` marks only that user's connection
as `reauthorization_required`.

The code-exchange request includes the Tesla client secret. Tesla's documented
refresh request includes the client ID and rotating refresh token, but not the
client secret. Neither response is logged or returned to a platform client.

---

## 7. Multiple vehicles

After OAuth, list all vehicles available through that Tesla account.

For each vehicle:

- create internal `vehicle_id`;
- associate with the authenticated platform user;
- store VIN/Tesla identifiers and display name;
- check Fleet status;
- show/persist Virtual Key state;
- offer pairing if needed;
- configure Fleet Telemetry separately.

Never assume account authorization pairs the key to every vehicle.

---

## 8. Virtual Key pairing

For non-B2B personal vehicles, the owner/user must add the application's public key to each vehicle through Tesla's user-in-the-loop pairing flow.

Deep link:

```text
https://www.tesla.com/_ak/woodhouse.derekjass.com
```

Vehicle-specific form:

```text
https://www.tesla.com/_ak/woodhouse.derekjass.com?vin=<VIN>
```

Tesla explicitly supports selecting among multiple vehicles or passing a VIN.

After pairing, verify key state with `fleet_status` before enabling signed controls/telemetry setup.

---

## 9. Fleet Telemetry configuration per vehicle

Prerequisites currently include supported firmware, application Virtual Key paired, required scopes, and public telemetry server.

Use Tesla's recommended signed configuration path through Vehicle Command Proxy.

For every eligible vehicle:

1. inspect current config;
2. generate desired broad telemetry config against current available-data definitions;
3. apply config;
4. verify sync status;
5. inspect telemetry errors;
6. record config hash/version in platform state.

Do not let one vehicle's pairing/config failure prevent other vehicles on the account from working.

---

## 10. Safe first live validation

Order:

1. vehicle list;
2. region/fleet status;
3. targeted live data read;
4. Virtual Key pair;
5. harmless signed command such as flash lights or climate start/stop;
6. telemetry config;
7. verify telemetry received and persisted.

Do not make unlock, HomeLink, or keyless driving the first real command test.

Phase 4 stops after per-vehicle `fleet_status`; it does not execute the harmless
command in step 5 or configure telemetry in step 6. Those are Phase 6 and Phase
8 checkpoints respectively.

---

## 11. Implemented Phase 4 HTTP and admin surface

Public:

```text
GET /.well-known/appspecific/com.tesla.3p.public-key.pem
GET /oauth/callback
```

Platform-authenticated:

```text
GET  /tesla/oauth/start
GET  /tesla/vehicles
POST /tesla/oauth/refresh
POST /tesla/vehicles/{internal_vehicle_id}/fleet-status
```

The callback derives its owner exclusively from consumed server-side OAuth
state. Vehicle status routes accept only an internal vehicle ID and verify its
stored owner against the authenticated platform identity before contacting
Tesla. The callback and status documents never contain Tesla credentials.

Partner registration is a trusted operator command, not an MCP operation:

```bash
uv run python scripts/admin/register-partner \
  --project-id woodhouse-506215 \
  --client-id "$TESLA_CLIENT_ID" \
  --domain woodhouse.derekjass.com \
  --region na
```

The command reads the client secret and expected public key directly from their
Secret Manager versions. It verifies before registering, registers only when
missing, and verifies the returned key afterward. Repeat `--region eu` when
serving an EMEA account. China requires the separately documented Tesla China
account/application and is not enabled merely by adding `--region cn` to the
North American application.

---

## 12. Required operator checkpoint — first real Tesla onboarding

Complete these steps in order after the Phase 4 image is reviewed, merged, and
deployed. Never paste the Tesla client secret, OAuth tokens, refresh token, or
private EC key into chat, shell arguments, Terraform, or source files.

### Checkpoint A — Tesla developer application and billing

In the Tesla Developer dashboard, verify:

```text
Application origin: https://woodhouse.derekjass.com
OAuth redirect URI: https://woodhouse.derekjass.com/oauth/callback
Grant types: Authorization Code and Machine-to-Machine
Scopes: openid offline_access vehicle_device_data vehicle_location vehicle_cmds vehicle_charging_cmds
```

Confirm the Tesla account email is verified and MFA is enabled. In **Billing and
Usage**, add a payment method and set a small positive monthly billing limit;
Tesla currently defaults the limit to zero and disables applications with no
payment method or exhausted limit. Tesla currently provides a $10 monthly small-
application discount, but the limit remains an important safety control.

### Checkpoint B — key material and Secret Manager versions

First apply the reviewed Phase 4 Terraform with Tesla onboarding still disabled
so the new secret containers and keyless registrar exist. In the uncommitted
tfvars file set the operator binding, then plan and apply:

```hcl
enable_tesla_onboarding  = false
partner_admin_principals = ["user:jazzer36@gmail.com"]
```

```powershell
terraform -chdir=infra/terraform plan -var-file=terraform.tfvars -out=phase4-foundation.tfplan
terraform -chdir=infra/terraform apply phase4-foundation.tfplan
```

Generate the Tesla-required P-256 pair in a new restricted temporary directory:

```powershell
$keyDir = Join-Path $env:TEMP "tpp-tesla-key"
New-Item -ItemType Directory -Force -Path $keyDir | Out-Null
openssl ecparam -name prime256v1 -genkey -noout -out "$keyDir\private-key.pem"
openssl ec -in "$keyDir\private-key.pem" -pubout -out "$keyDir\public-key.pem"
gcloud secrets versions add tesla-command-private-key --project woodhouse-506215 --data-file="$keyDir\private-key.pem"
gcloud secrets versions add tesla-command-public-key --project woodhouse-506215 --data-file="$keyDir\public-key.pem"
openssl rand -base64 32 | gcloud secrets versions add tesla-token-encryption-key --project woodhouse-506215 --data-file=-
```

In **Google Cloud Console -> Security -> Secret Manager ->
`tesla-client-secret` -> New version**, paste the Tesla client secret into the
secret-value field and save it there. Do not put it in a PowerShell variable or
command history. Verify only version metadata:

```powershell
gcloud secrets versions list tesla-client-secret --project woodhouse-506215
gcloud secrets versions list tesla-command-private-key --project woodhouse-506215
gcloud secrets versions list tesla-command-public-key --project woodhouse-506215
gcloud secrets versions list tesla-token-encryption-key --project woodhouse-506215
```

After upload, securely remove the temporary private-key file. Retain the public
file only long enough for the fingerprint check below.

### Checkpoint C — deploy configuration and domain

Set these non-secret values in the uncommitted `infra/terraform/terraform.tfvars`:

```hcl
enable_tesla_onboarding  = true
tesla_client_id          = "Tesla dashboard client ID"
tesla_app_domain         = "woodhouse.derekjass.com"
tesla_oauth_redirect_uri = "https://woodhouse.derekjass.com/oauth/callback"
tesla_initial_audience   = "https://fleet-api.prd.na.vn.cloud.tesla.com"
partner_admin_principals = ["user:jazzer36@gmail.com"]
```

Plan, inspect, and apply Terraform. Deploy the merged commit-addressed gateway
image through the normal main-branch delivery path. If the Cloud Run custom
domain mapping does not already exist, create it and copy the exact DNS records
reported by Google into the authoritative DNS provider:

```powershell
gcloud run domain-mappings create --service mcp-gateway --domain woodhouse.derekjass.com --region us-central1 --project woodhouse-506215
gcloud run domain-mappings describe --domain woodhouse.derekjass.com --region us-central1 --project woodhouse-506215
```

Wait for the mapping certificate to become active. Then verify the hosted key
matches the local public key without displaying private material:

```powershell
curl.exe -fsS "https://woodhouse.derekjass.com/.well-known/appspecific/com.tesla.3p.public-key.pem" -o "$keyDir\hosted-public-key.pem"
openssl pkey -pubin -in "$keyDir\public-key.pem" -outform DER | openssl dgst -sha256
openssl pkey -pubin -in "$keyDir\hosted-public-key.pem" -outform DER | openssl dgst -sha256
```

Both fingerprints must match before partner registration.

### Checkpoint D — partner registration

Use keyless impersonation of the narrow `tpp-partner-admin` account:

```powershell
gcloud auth application-default login --impersonate-service-account=tpp-partner-admin@woodhouse-506215.iam.gserviceaccount.com
uv sync --frozen --all-packages --group dev
uv run python scripts/admin/register-partner --project-id woodhouse-506215 --client-id "TESLA_CLIENT_ID_FROM_DASHBOARD" --domain woodhouse.derekjass.com --region na
```

The safe output must say `registered` or `already_registered`; it contains no
partner token or secret. Register additional non-China regions only when users
there are actually supported, using another `--region` argument.

### Checkpoint E — one approved user's Tesla OAuth

Acquire a fresh platform Google ID token through the already-validated Phase 3
flow and keep it only in a local shell variable. Request the authorization URL:

```powershell
curl.exe -sS -D "$env:TEMP\tpp-tesla-oauth-headers.txt" -o NUL -H "Authorization: Bearer $token" "https://woodhouse.derekjass.com/tesla/oauth/start"
Select-String -Path "$env:TEMP\tpp-tesla-oauth-headers.txt" -Pattern '^Location:'
```

Open that `Location` URL, approve the documented scopes, and allow Tesla to
return to the exact callback URI. A successful callback returns only a safe
connection summary and every discovered vehicle with an internal `vehicle_id`
and `virtual_key_status`; it never returns OAuth tokens.

### Checkpoint F — prove rotation, ownership, and per-vehicle key state

With the same short-lived platform ID token, force one safe token rotation and
then retrieve the server-derived vehicle list:

```powershell
curl.exe -sS -X POST -H "Authorization: Bearer $token" -H "Content-Type: application/json" --data "{}" "https://woodhouse.derekjass.com/tesla/oauth/refresh"
curl.exe -sS -H "Authorization: Bearer $token" "https://woodhouse.derekjass.com/tesla/vehicles"
```

The refresh response exposes only a monotonically increased token version and
expiry. For each returned internal vehicle ID, refresh Fleet status:

```powershell
curl.exe -sS -X POST -H "Authorization: Bearer $token" -H "Content-Type: application/json" --data "{}" "https://woodhouse.derekjass.com/tesla/vehicles/INTERNAL_VEHICLE_ID/fleet-status"
```

Open each returned `virtual_key_pairing_url` that reports `pending` and complete
the Tesla mobile-app pairing for that specific vehicle. Re-run only that
vehicle's Fleet-status request. A vehicle is complete only when its own status
is `paired`; other vehicles may remain `pending` without invalidating the Tesla
account connection.

Checkpoint evidence is complete when partner verification succeeds, OAuth and
one forced refresh succeed, the returned list matches all expected vehicles,
every record is owned by the authenticated internal user in Firestore, and each
vehicle has a known `paired` or `pending` status.

---

## 13. Revocation/recovery

Provide runbooks for:

- Tesla OAuth expired/revoked;
- refresh-token persistence failure;
- password reset causing login-required;
- Virtual Key removed from one vehicle;
- telemetry configuration removed;
- application public key unavailable;
- application key rotation.
