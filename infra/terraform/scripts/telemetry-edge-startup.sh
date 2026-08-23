#!/bin/bash
set -eu

METADATA_ROOT="http://metadata.google.internal/computeMetadata/v1"
METADATA_HEADER="Metadata-Flavor: Google"
STATE_DIR="/var/lib/telemetry-edge"
TLS_DIR="$STATE_DIR/tls"
CONFIG_DIR="$STATE_DIR/config"
CONFIG_FILE="$CONFIG_DIR/config.json"
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

report_unhandled_failure() {
  exit_code="$?"
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
  response="$(curl --fail --silent --show-error \
    --header "Authorization: Bearer $token" \
    "https://secretmanager.googleapis.com/v1/projects/$PROJECT_ID/secrets/$secret_name/versions/latest:access")"
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
  until curl --fail --silent --show-error --max-time 2 \
    http://127.0.0.1:8080/status >/dev/null; do
    attempts=$((attempts + 1))
    if [ "$attempts" -ge 30 ]; then
      return 1
    fi
    sleep 2
  done
}

mkdir -p "$TLS_DIR" "$CONFIG_DIR"
chmod 0750 "$STATE_DIR" "$TLS_DIR" "$CONFIG_DIR"

PROJECT_ID="$(metadata telemetry-edge-project-id)"
REGION="$(metadata telemetry-edge-region)"
REPOSITORY="$(metadata telemetry-edge-repository)"
TLS_CERT_SECRET="$(metadata telemetry-edge-tls-cert-secret)"
TLS_KEY_SECRET="$(metadata telemetry-edge-tls-key-secret)"
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
secret_to_file "$TLS_CERT_SECRET" "$TLS_DIR/fullchain.pem" "$TOKEN"
secret_to_file "$TLS_KEY_SECRET" "$TLS_DIR/privkey.pem" "$TOKEN"

printf '%s' "$TOKEN" | docker login --username oauth2accesstoken --password-stdin \
  "https://$REGION-docker.pkg.dev" >/dev/null
unset TOKEN
docker pull "$DESIRED_IMAGE" >/dev/null

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
  report_status "success:$DESIRED_COMMIT"
  exit 0
fi

docker logs --tail 100 "$CONTAINER_NAME" 2>&1 || true
docker rm --force "$CONTAINER_NAME" >/dev/null 2>&1 || true
if [ -n "$PREVIOUS_IMAGE" ] && [ "$PREVIOUS_IMAGE" != "$DESIRED_IMAGE" ]; then
  if run_receiver "$PREVIOUS_IMAGE" && wait_for_health; then
    report_status "failed:$DESIRED_COMMIT:rolled-back"
    exit 1
  fi
fi
report_status "failed:$DESIRED_COMMIT:no-healthy-rollback"
exit 1
