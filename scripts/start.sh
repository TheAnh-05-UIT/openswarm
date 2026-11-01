#!/usr/bin/env bash
set -euo pipefail
source "$(dirname -- "${BASH_SOURCE[0]}")/common.sh"
load_settings
check_engine
dc config --quiet
# A run is a fresh job, never implicit recovery of memory-only SN/SL state.
for kind in container network volume; do
  args=()
  [[ "$kind" != container ]] || args+=(--all)
  [[ -z "$(docker "$kind" ls "${args[@]}" --quiet --filter "label=com.docker.compose.project=$PROJECT")" ]] || die "Existing project $kind resources; choose a new RUN_ID, retain old evidence."
done
for volume in deployment checkpoint-a checkpoint-b controller-data; do
  if docker volume inspect "${PROJECT}_$volume" >/dev/null 2>&1; then die 'Named volume already exists; choose a new RUN_ID.'; fi
done
if docker network inspect "${PROJECT}_swarm" >/dev/null 2>&1; then die 'Named network already exists; choose a new RUN_ID.'; fi
dc pull
dc up --detach --no-build --pull never --no-recreate
dc ps --all
printf 'Started a NEW job. Follow logs; do not restart individual services to resume training.\n'
