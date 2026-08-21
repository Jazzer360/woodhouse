# Tesla Developer Application and User Onboarding

**Last verified against Tesla official docs:** 2026-08-21

Re-check current Tesla docs before implementation.

Official references:

- https://developer.tesla.com/docs/fleet-api/authentication/overview
- https://developer.tesla.com/docs/fleet-api/authentication/third-party-tokens
- https://developer.tesla.com/docs/fleet-api/virtual-keys/developer-guide
- https://developer.tesla.com/docs/fleet-api/endpoints/partner-endpoints
- https://developer.tesla.com/docs/fleet-api/fleet-telemetry

---

## 1. Developer app configuration

Create one Tesla Fleet API application.

Planned values:

```text
OAuth grant type:
Authorization Code and Machine-to-Machine

Allowed origin:
https://tesla.<personal-domain>

Optional local origin:
http://localhost:8080

Allowed redirect URI:
https://tesla.<personal-domain>/oauth/tesla/callback

Optional local redirect:
http://localhost:8080/oauth/tesla/callback

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
https://tesla.<personal-domain>/.well-known/appspecific/com.tesla.3p.public-key.pem
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
GET /oauth/tesla/start
GET /oauth/tesla/callback
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
https://tesla.com/_ak/tesla.<personal-domain>
```

Vehicle-specific form:

```text
https://tesla.com/_ak/tesla.<personal-domain>?vin=<VIN>
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

---

## 11. Revocation/recovery

Provide runbooks for:

- Tesla OAuth expired/revoked;
- refresh-token persistence failure;
- password reset causing login-required;
- Virtual Key removed from one vehicle;
- telemetry configuration removed;
- application public key unavailable;
- application key rotation.
