#!/usr/bin/env bash
set -euo pipefail
source "$(dirname -- "${BASH_SOURCE[0]}")/common.sh"
load_settings
check_engine
dc config --quiet
for service in client-a client-b round-controller; do
  text=$(dc ps --all --quiet "$service")
  [[ -n "$text" && "$text" != *$'\n'* ]] || die "Expected exactly one $service job container."
  state=$(docker inspect --format '{{.State.Status}}/{{.State.ExitCode}}' "$text")
  [[ "$state" == exited/0 ]] || die "Wait for $service to finish successfully before numeric replay ($state)."
done
dc --profile tools run --no-deps --pull never --name "$PROJECT-fedavg-verification" verify-fedavg
