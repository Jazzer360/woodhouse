# OAuth Recovery

**Status:** Phase 4 Tesla OAuth recovery implemented; re-verify during Phase 11.

This runbook will cover platform/Tesla authorization expiry, revocation, refresh-token rotation failure, `login_required`, safe reconnect, and validation that immutable platform identity and per-user Tesla token boundaries remain intact.

## Detection

The gateway marks `tesla_connections/{user_id}.status` as
`reauthorization_required` when Tesla returns `401 login_required` during refresh.
This normally means the three-month refresh token expired, newer rotations
cycled it out, or the Tesla account password changed. Do not log or inspect the
encrypted credential blob.

## Safe recovery

1. Confirm the platform user remains active and the immutable OIDC binding is
   unchanged.
2. Have that authenticated user start `GET /tesla/oauth/start` again.
3. Complete Tesla consent and the callback. The callback replaces only that
   user's encrypted connection state.
4. Call `POST /tesla/oauth/refresh` once and verify the safe token version
   increments; no token value should be returned.
5. Fetch `GET /tesla/vehicles` and compare all vehicles with the pre-recovery
   owner mappings.
6. Refresh `fleet-status` per vehicle. OAuth renewal does not imply that a
   removed Virtual Key was re-paired.

If a refresh exchange succeeded but persistence failed, retry promptly. Tesla
currently keeps the most recently used refresh token valid for up to 24 hours
for this recovery case. Do not copy either token out of Firestore or attempt to
repair ciphertext manually.

## Revocation

For immediate platform revocation, run `disable-user`; it blocks the gateway
without deleting history. Tesla consent may also be revoked through Tesla's
Account Security consent page. Remove the Virtual Key separately from each
vehicle's Locks screen when vehicle-level trust must be revoked.
