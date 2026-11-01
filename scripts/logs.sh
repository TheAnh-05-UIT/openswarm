#!/usr/bin/env bash
set -euo pipefail
source "$(dirname -- "${BASH_SOURCE[0]}")/common.sh"
load_settings
dc logs --follow --tail 100 sn sl-a sl-b client-a client-b round-controller
