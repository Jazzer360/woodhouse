#!/bin/bash
set -eu

METADATA_ROOT="http://metadata.google.internal/computeMetadata/v1"
METADATA_HEADER="Metadata-Flavor: Google"
STATE_DIR="/var/lib/telemetry-edge"
TLS_DIR="$STATE_DIR/tls"
TLS_STAGING_DIR="$STATE_DIR/tls-staging"
TLS_PREVIOUS_DIR="$STATE_DIR/tls-previous"
CONFIG_DIR="$STATE_DIR/config"
CONFIG_FILE="$CONFIG_DIR/config.json"
DOCKER_CONFIG_DIR="/run/telemetry-edge-docker"
CONTAINER_NAME="telemetry-edge"
STATUS_REPORTED="false"

metadata() {
  curl --fail --silent --show-error --header "$METADATA_HEADER" \
    "$METADATA_ROOT/instance/attributes/$1"
}

guest_status() {
  curl --fail --silent --show-error --request PUT --header "$METADATA_HEADER" \
    --data-binary "$1" \
    "$METADATA_ROOT/instance/guest-attributes/telemetry-edge/status" >/dev/null
}

report_status() {
  guest_status "$1"
  STATUS_REPORTED="true"
}

cleanup() {
  rm -f "$DOCKER_CONFIG_DIR/config.json"
  rmdir "$DOCKER_CONFIG_DIR" 2>/dev/null || true
}

report_unhandled_failure() {
  exit_code="$?"
  cleanup
  if [ "$exit_code" -ne 0 ] && [ "$STATUS_REPORTED" != "true" ]; then
    guest_status "failed:${DESIRED_COMMIT:-unknown}:startup-error" || true
  fi
  exit "$exit_code"
}

trap report_unhandled_failure EXIT

access_token() {
  curl --fail --silent --show-error --header "$METADATA_HEADER" \
    "$METADATA_ROOT/instance/service-accounts/default/token" \
    | sed -n 's/.*"access_token"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p'
}

secret_to_file() {
  secret_name="$1"
  output_file="$2"
  token="$3"
  version="${4:-latest}"
  response="$(curl --fail --silent --show-error \
    --header "Authorization: Bearer $token" \
    "https://secretmanager.googleapis.com/v1/projects/$PROJECT_ID/secrets/$secret_name/versions/$version:access")"
  payload="$(printf '%s' "$response" | tr -d '\n' \
    | sed -n 's/.*"data"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p')"
  if [ -z "$payload" ]; then
    return 1
  fi
  temporary_file="${output_file}.new"
  umask 077
  printf '%s' "$payload" | base64 --decode >"$temporary_file"
  test -s "$temporary_file"
  chown 65532:65532 "$temporary_file"
  chmod 0440 "$temporary_file"
  mv -f "$temporary_file" "$output_file"
  printf '%s' "$response" | tr -d '\n' \
    | sed -n 's/.*"name"[[:space:]]*:[[:space:]]*"[^"]*\/versions\/\([0-9][0-9]*\)".*/\1/p'
}

json_field() {
  file="$1"
  field="$2"
  sed -n "s/.*\"$field\"[[:space:]]*:[[:space:]]*\"\([^\"]*\)\".*/\1/p" "$file"
}

valid_sha256() {
  [ "${#1}" -eq 64 ] && ! printf '%s' "$1" | grep -q '[^0-9a-f]'
}

validate_tls_files() {
  directory="$1"
  expected_hostname="$2"
  expected_fullchain_sha="$3"
  expected_leaf_sha="$4"
  cert_file="$directory/fullchain.pem"
  key_file="$directory/privkey.pem"

  [ -s "$cert_file" ] && [ -s "$key_file" ]
  [ "$(grep -c -- '-----BEGIN CERTIFICATE-----' "$cert_file")" -ge 2 ]
  openssl x509 -in "$cert_file" -noout -checkend 604800 >/dev/null
  openssl x509 -in "$cert_file" -noout -ext subjectAltName \
    | grep -F "DNS:$expected_hostname" >/dev/null
  actual_fullchain_sha="$(sha256sum "$cert_file" | cut -d ' ' -f 1)"
  actual_leaf_sha="$(openssl x509 -in "$cert_file" -noout -fingerprint -sha256 \
    | cut -d '=' -f 2 | tr -d ':' | tr 'A-F' 'a-f')"
  cert_public_sha="$(openssl x509 -in "$cert_file" -pubkey -noout \
    | openssl pkey -pubin -outform DER 2>/dev/null | sha256sum | cut -d ' ' -f 1)"
  key_public_sha="$(openssl pkey -in "$key_file" -pubout -outform DER 2>/dev/null \
    | sha256sum | cut -d ' ' -f 1)"

  [ "$actual_fullchain_sha" = "$expected_fullchain_sha" ]
  [ "$actual_leaf_sha" = "$expected_leaf_sha" ]
  [ "$cert_public_sha" = "$key_public_sha" ]
}

prepare_tls() {
  token="$1"
  release_file="$STATE_DIR/tls-release.json.new"
  rm -rf "$TLS_STAGING_DIR"
  mkdir -p "$TLS_STAGING_DIR"
  chown root:65532 "$TLS_STAGING_DIR"
  chmod 0750 "$TLS_STAGING_DIR"

  if release_version="$(secret_to_file "$TLS_RELEASE_SECRET" "$release_file" "$token")" \
    && [ -n "$release_version" ]; then
    cert_secret="$(json_field "$release_file" cert_secret)"
    cert_version="$(json_field "$release_file" cert_version)"
    key_secret="$(json_field "$release_file" key_secret)"
    key_version="$(json_field "$release_file" key_version)"
    release_hostname="$(json_field "$release_file" hostname)"
    fullchain_sha="$(json_field "$release_file" fullchain_sha256)"
    leaf_sha="$(json_field "$release_file" leaf_sha256)"
    [ "$cert_secret" = "$TLS_CERT_SECRET" ]
    [ "$key_secret" = "$TLS_KEY_SECRET" ]
    [ "$release_hostname" = "$TELEMETRY_HOSTNAME" ]
    case "$cert_version" in ''|*[!0-9]*) return 1 ;; esac
    case "$key_version" in ''|*[!0-9]*) return 1 ;; esac
    valid_sha256 "$fullchain_sha"
    valid_sha256 "$leaf_sha"
    secret_to_file "$cert_secret" "$TLS_STAGING_DIR/fullchain.pem" "$token" "$cert_version" >/dev/null
    secret_to_file "$key_secret" "$TLS_STAGING_DIR/privkey.pem" "$token" "$key_version" >/dev/null
    validate_tls_files "$TLS_STAGING_DIR" "$TELEMETRY_HOSTNAME" "$fullchain_sha" "$leaf_sha"
    leaf_prefix="$(printf '%s' "$leaf_sha" | cut -c 1-16)"
    TLS_RELEASE_MARKER="$release_version:$leaf_prefix"
    mv -f "$release_file" "$STATE_DIR/tls-release.json"
  elif [ -s "$TLS_DIR/fullchain.pem" ] && [ -s "$TLS_DIR/privkey.pem" ]; then
    fullchain_sha="$(sha256sum "$TLS_DIR/fullchain.pem" | cut -d ' ' -f 1)"
    leaf_sha="$(openssl x509 -in "$TLS_DIR/fullchain.pem" -noout -fingerprint -sha256 \
      | cut -d '=' -f 2 | tr -d ':' | tr 'A-F' 'a-f')"
    validate_tls_files "$TLS_DIR" "$TELEMETRY_HOSTNAME" "$fullchain_sha" "$leaf_sha"
    rm -rf "$TLS_STAGING_DIR"
    TLS_RELEASE_MARKER="$(cat "$STATE_DIR/deployed-tls-release" 2>/dev/null || printf legacy)"
    return 0
  else
    rm -f "$release_file"
    secret_to_file "$TLS_CERT_SECRET" "$TLS_STAGING_DIR/fullchain.pem" "$token" >/dev/null
    secret_to_file "$TLS_KEY_SECRET" "$TLS_STAGING_DIR/privkey.pem" "$token" >/dev/null
    fullchain_sha="$(sha256sum "$TLS_STAGING_DIR/fullchain.pem" | cut -d ' ' -f 1)"
    leaf_sha="$(openssl x509 -in "$TLS_STAGING_DIR/fullchain.pem" -noout -fingerprint -sha256 \
      | cut -d '=' -f 2 | tr -d ':' | tr 'A-F' 'a-f')"
    validate_tls_files "$TLS_STAGING_DIR" "$TELEMETRY_HOSTNAME" "$fullchain_sha" "$leaf_sha"
    TLS_RELEASE_MARKER="legacy"
  fi

  rm -rf "$TLS_PREVIOUS_DIR"
  if [ -d "$TLS_DIR" ]; then
    mv "$TLS_DIR" "$TLS_PREVIOUS_DIR"
  fi
  mv "$TLS_STAGING_DIR" "$TLS_DIR"
  chown root:65532 "$TLS_DIR"
  chmod 0750 "$TLS_DIR"
}

run_receiver() {
  image="$1"
  docker run --detach --name "$CONTAINER_NAME" --restart=always \
    --read-only --tmpfs /tmp:rw,noexec,nosuid,size=16m \
    --cap-drop=ALL --security-opt=no-new-privileges:true \
    --publish 0.0.0.0:443:443 \
    --publish 127.0.0.1:8080:8080 \
    --publish 127.0.0.1:9090:9090 \
    --mount "type=bind,src=$CONFIG_FILE,dst=/etc/fleet-telemetry/config.json,readonly" \
    --mount "type=bind,src=$TLS_DIR,dst=/etc/fleet-telemetry/tls,readonly" \
    --label "tpp.component=telemetry-edge" \
    "$image" >/dev/null
}

wait_for_health() {
  attempts=0
  until [ "$(docker inspect --format '{{.State.Running}}' "$CONTAINER_NAME" 2>/dev/null || true)" = "true" ] \
    && curl --fail --silent --show-error --max-time 2 \
      http://127.0.0.1:8080/status >/dev/null; do
    attempts=$((attempts + 1))
    if [ "$attempts" -ge 30 ]; then
      return 1
    fi
    sleep 2
  done

  restart_count="$(docker inspect --format '{{.RestartCount}}' "$CONTAINER_NAME")"
  sleep 5
  [ "$(docker inspect --format '{{.State.Running}}' "$CONTAINER_NAME")" = "true" ]
  [ "$(docker inspect --format '{{.RestartCount}}' "$CONTAINER_NAME")" = "$restart_count" ]
  curl --fail --silent --show-error --max-time 2 \
    http://127.0.0.1:8080/status >/dev/null
}

mkdir -p "$STATE_DIR" "$CONFIG_DIR"
chown root:65532 "$STATE_DIR" "$CONFIG_DIR"
chmod 0750 "$STATE_DIR" "$CONFIG_DIR"

PROJECT_ID="$(metadata telemetry-edge-project-id)"
REGION="$(metadata telemetry-edge-region)"
REPOSITORY="$(metadata telemetry-edge-repository)"
TLS_CERT_SECRET="$(metadata telemetry-edge-tls-cert-secret)"
TLS_KEY_SECRET="$(metadata telemetry-edge-tls-key-secret)"
TLS_RELEASE_SECRET="$(metadata telemetry-edge-tls-release-secret)"
TELEMETRY_HOSTNAME="$(metadata telemetry-edge-hostname)"
DESIRED_IMAGE="$(metadata telemetry-edge-image || true)"
DESIRED_COMMIT="$(metadata telemetry-edge-commit || true)"

if [ -z "$DESIRED_IMAGE" ]; then
  report_status "awaiting-image"
  exit 0
fi

IMAGE_PREFIX="$REGION-docker.pkg.dev/$PROJECT_ID/$REPOSITORY/telemetry-edge@sha256:"
case "$DESIRED_IMAGE" in
  "$IMAGE_PREFIX"*) IMAGE_DIGEST="${DESIRED_IMAGE#"$IMAGE_PREFIX"}" ;;
  *) IMAGE_DIGEST="" ;;
esac
if [ "${#IMAGE_DIGEST}" -ne 64 ] || printf '%s' "$IMAGE_DIGEST" | grep -q '[^0-9a-f]'; then
  report_status "failed:invalid-image"
  exit 1
fi

if [ "${#DESIRED_COMMIT}" -ne 40 ] || printf '%s' "$DESIRED_COMMIT" | grep -q '[^0-9a-f]'; then
  report_status "failed:invalid-commit"
  exit 1
fi

temporary_config="$CONFIG_FILE.new"
umask 077
metadata telemetry-edge-config >"$temporary_config"
test -s "$temporary_config"
grep -F "\"gcp_project_id\":\"$PROJECT_ID\"" "$temporary_config" >/dev/null
chown 65532:65532 "$temporary_config"
chmod 0440 "$temporary_config"
mv -f "$temporary_config" "$CONFIG_FILE"

TOKEN="$(access_token)"
test -n "$TOKEN"
prepare_tls "$TOKEN"

mkdir -p "$DOCKER_CONFIG_DIR"
chmod 0700 "$DOCKER_CONFIG_DIR"
export DOCKER_CONFIG="$DOCKER_CONFIG_DIR"
printf '%s' "$TOKEN" | docker login --username oauth2accesstoken --password-stdin \
  "https://$REGION-docker.pkg.dev" >/dev/null
unset TOKEN
docker pull "$DESIRED_IMAGE" >/dev/null
cleanup

PREVIOUS_IMAGE=""
if docker inspect "$CONTAINER_NAME" >/dev/null 2>&1; then
  PREVIOUS_IMAGE="$(docker inspect --format '{{.Config.Image}}' "$CONTAINER_NAME")"
  docker rm --force "$CONTAINER_NAME" >/dev/null
elif [ -s "$STATE_DIR/deployed-image" ]; then
  PREVIOUS_IMAGE="$(cat "$STATE_DIR/deployed-image")"
fi

if run_receiver "$DESIRED_IMAGE" && wait_for_health; then
  printf '%s' "$DESIRED_IMAGE" >"$STATE_DIR/deployed-image"
  printf '%s' "$DESIRED_COMMIT" >"$STATE_DIR/deployed-commit"
  printf '%s' "$TLS_RELEASE_MARKER" >"$STATE_DIR/deployed-tls-release"
  report_status "success:$DESIRED_COMMIT:tls=$TLS_RELEASE_MARKER"
  exit 0
fi

docker logs --tail 100 "$CONTAINER_NAME" 2>&1 || true
docker rm --force "$CONTAINER_NAME" >/dev/null 2>&1 || true
if [ -d "$TLS_PREVIOUS_DIR" ]; then
  rm -rf "$TLS_DIR"
  mv "$TLS_PREVIOUS_DIR" "$TLS_DIR"
  if run_receiver "$DESIRED_IMAGE" && wait_for_health; then
    report_status "failed:$DESIRED_COMMIT:tls-rolled-back"
    exit 1
  fi
  docker rm --force "$CONTAINER_NAME" >/dev/null 2>&1 || true
fi
if [ -n "$PREVIOUS_IMAGE" ] && [ "$PREVIOUS_IMAGE" != "$DESIRED_IMAGE" ]; then
  if run_receiver "$PREVIOUS_IMAGE" && wait_for_health; then
    report_status "failed:$DESIRED_COMMIT:rolled-back"
    exit 1
  fi
fi
report_status "failed:$DESIRED_COMMIT:no-healthy-rollback"
exit 1
