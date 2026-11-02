#!/usr/bin/env bash
# Chạy verifier bằng image SWOP sau khi clients/controller đã hoàn tất.
set -euo pipefail
source "$(dirname -- "${BASH_SOURCE[0]}")/common.sh"
load_settings
check_engine
dc config --quiet
# Giữ container xác minh làm bằng chứng; không dùng --rm hoặc tự dọn dữ liệu.
dc --profile tools run --no-deps --pull never --name "$PROJECT-verification" verify
