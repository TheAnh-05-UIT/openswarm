#!/usr/bin/env bash
# Dừng đúng container thuộc job single-host hiện tại; không xóa dữ liệu.
set -euo pipefail
source "$(dirname -- "${BASH_SOURCE[0]}")/common.sh"
load_settings
check_engine
text=$(docker container ls --all --quiet --filter "label=com.docker.compose.project=$PROJECT")
[[ -n "$text" ]] || { printf 'No containers for %s\n' "$PROJECT"; exit 0; }
mapfile -t ids <<< "$text"
# Kiểm tra TOÀN BỘ ứng viên trước khi dừng; không xóa container hoặc dữ liệu.
for id in "${ids[@]}"; do
  identity=$(docker inspect --format '{{index .Config.Labels "com.docker.compose.project"}}|{{index .Config.Labels "io.openswarm.deployment.owner"}}|{{index .Config.Labels "io.openswarm.deployment.run"}}' "$id")
  [[ "$identity" == "$PROJECT|openswarm-deployment|$RUN_ID" ]] || die "Ownership mismatch for $id; nothing stopped."
done
# Dừng controller trước, rồi client, SL và SN; kiểm tra lại đúng các ID đã chọn.
for service in round-controller verify-fedavg verify client-a client-b sl-a sl-b sn bundle-init; do
  for id in "${ids[@]}"; do
    name=$(docker inspect --format '{{index .Config.Labels "com.docker.compose.service"}}' "$id")
    if [[ "$name" == "$service" && "$(docker inspect --format '{{.State.Running}}' "$id")" == true ]]; then
      docker container stop --timeout 30 "$id"
    fi
  done
done
printf 'Stopped only owned containers; all containers/networks/volumes retained. Recovery requires a new RUN_ID.\n'
