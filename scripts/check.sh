#!/usr/bin/env bash
# Kiểm tra cấu hình, local Engine và Compose; không khởi động job.
set -euo pipefail
source "$(dirname -- "${BASH_SOURCE[0]}")/common.sh"
load_settings
check_engine
dc config --quiet
printf 'Static configuration OK: %s. No containers started; native runtime is NOT VERIFIED.\n' "$PROJECT"
