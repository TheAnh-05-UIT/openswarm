#!/usr/bin/env bash
# Literal .env reader: never source/eval user-controlled input.
set -euo pipefail
ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }
load_settings() {
  [[ -f "$ROOT/.env" ]] || die 'Copy .env.example to .env and edit it first.'
  local line key value required seen='|'
  while IFS= read -r line || [[ -n "$line" ]]; do
    line=${line%$'\r'}
    [[ -z "$line" || "$line" == \#* ]] && continue
    [[ "$line" =~ ^([A-Z][A-Z0-9_]*)=([a-zA-Z0-9_.-]*)$ ]] || die 'Only literal KEY=VALUE entries without shell expansion/quotes are supported.'
    key=${BASH_REMATCH[1]}; value=${BASH_REMATCH[2]}
    case "$key" in
      IMAGE_NAMESPACE|SN_VERSION|SL_VERSION|USER_TF_VERSION|SWOP_VERSION|RUN_ID|EXCLUSIVE_OWNER_ACK|SN_PORT|SL_PORT|CONTROLLER_PORT|ROUNDS|SAMPLES_A|SAMPLES_B|DATA_SEED_A|DATA_SEED_B|EPOCHS|BATCH_SIZE|LEARNING_RATE|STARTUP_TIMEOUT|ROUND_TIMEOUT) ;;
      *) die "Unsupported setting: $key" ;;
    esac
    [[ "$seen" != *"|$key|"* ]] || die "Duplicate setting: $key"
    seen+="$key|"
    export "$key=$value"
  done < "$ROOT/.env"
  for required in IMAGE_NAMESPACE SN_VERSION SL_VERSION USER_TF_VERSION SWOP_VERSION RUN_ID EXCLUSIVE_OWNER_ACK SN_PORT SL_PORT CONTROLLER_PORT ROUNDS SAMPLES_A SAMPLES_B DATA_SEED_A DATA_SEED_B EPOCHS BATCH_SIZE LEARNING_RATE STARTUP_TIMEOUT ROUND_TIMEOUT; do
    [[ "$seen" == *"|$required|"* && -n "${!required:-}" ]] || die "Missing setting: $required"
  done
  [[ "$RUN_ID" =~ ^[0-9]{8}t[0-9]{6}z-[0-9a-f]{8}$ ]] || die 'Invalid fresh RUN_ID format.'
  [[ "$EXCLUSIVE_OWNER_ACK" == true ]] || die 'EXCLUSIVE_OWNER_ACK must be true; ensure one trusted round writer.'
  [[ "$IMAGE_NAMESPACE" =~ ^[a-z0-9][a-z0-9_-]{1,63}$ ]] || die 'Invalid image namespace.'
  for required in SN_VERSION SL_VERSION USER_TF_VERSION SWOP_VERSION; do
    value=${!required}
    [[ "$value" =~ ^[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}$ && "${value,,}" != latest ]] || die 'Explicit non-latest image tags required.'
  done
  check_integer SN_PORT 1024 65535; check_integer SL_PORT 1024 65535; check_integer CONTROLLER_PORT 1024 65535
  [[ "$SN_PORT" != "$CONTROLLER_PORT" ]] || die 'SN and controller share a network namespace; use distinct ports.'
  check_integer ROUNDS 2 2; check_integer SAMPLES_A 1 256; check_integer SAMPLES_B 1 256
  check_integer DATA_SEED_A 0 2147483647; check_integer DATA_SEED_B 0 2147483647
  check_integer EPOCHS 1 10; check_integer BATCH_SIZE 1 64
  check_integer STARTUP_TIMEOUT 30 1800; check_integer ROUND_TIMEOUT 30 1800
  [[ "$LEARNING_RATE" =~ ^[0-9]+(\.[0-9]+)?$ ]] || die 'Invalid learning rate.'
  awk -v rate="$LEARNING_RATE" 'BEGIN {exit !(rate >= 0.0001 && rate <= 0.1)}' || die 'Learning rate outside 0.0001..0.1.'
  for value in "$SAMPLES_A" "$SAMPLES_B"; do
    (( ((value + BATCH_SIZE - 1) / BATCH_SIZE) * EPOCHS <= 1024 )) || die 'User-TF supports at most 1024 minibatch updates per round.'
  done
  PROJECT="openswarm-$RUN_ID"
  # Explicit args plus removal of inherited Compose overrides prevent accidental reuse.
  unset COMPOSE_PROJECT_NAME COMPOSE_FILE COMPOSE_PROFILES COMPOSE_REMOVE_ORPHANS COMPOSE_ENV_FILES
  export COMPOSE_REMOVE_ORPHANS=0
  [[ -r "$ROOT/config/initialize.py" && -r "$ROOT/config/profile.template.json" && -r "$ROOT/config/assets/initial.iutfw" && -r "$ROOT/config/verify.py" && -r "$ROOT/config/verify_fedavg.py" ]] || die 'Incomplete deployment repository.'
  (cd "$ROOT" && printf '%s  %s\n' 590525b9b37b66b74a21c492a910a27b30ca030d2d15bac64a1b4203ae314702 config/assets/initial.iutfw | sha256sum --check --status) || die 'Numeric bootstrap checksum mismatch.'
  # These four images require no static secret. SN ownership tokens are generated
  # in memory and never put in .env or logs; secrets/ is reserved and unused.
}
check_integer() {
  local value=${!1}
  [[ "$value" =~ ^(0|[1-9][0-9]{0,9})$ ]] || die "Invalid integer: $1"
  (( 10#$value >= $2 && 10#$value <= $3 )) || die "Setting outside supported bounds: $1"
}
dc() { docker compose --project-directory "$ROOT" --env-file "$ROOT/.env" -p "$PROJECT" -f "$ROOT/compose.yaml" "$@"; }
check_engine() {
  command -v docker >/dev/null || die 'Docker CLI is required.'
  docker compose version >/dev/null || die 'Docker Compose v2 plugin is required.'
  [[ -z "${DOCKER_HOST:-}" || "$DOCKER_HOST" == unix://* ]] || die 'Use the local Unix Docker Engine on this Pi.'
  local endpoint
  endpoint=$(docker context inspect --format '{{.Endpoints.docker.Host}}')
  [[ "$endpoint" == unix://* ]] || die 'Select the local Unix Docker context; remote deployment is unsupported.'
  [[ "$(uname -m)" == aarch64 || "$(uname -m)" == arm64 ]] || die 'This deployment targets a physical ARM64 host; emulation is not native evidence.'
  case "$(docker info --format '{{.OSType}}/{{.Architecture}}')" in linux/aarch64|linux/arm64) ;; *) die 'A local Linux ARM64 Docker Engine is required.' ;; esac
}
