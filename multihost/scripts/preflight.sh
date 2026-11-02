#!/usr/bin/env bash
# Đọc điều kiện host/mạng; phải xem từng JSON check, không chỉ exit code.
set -euo pipefail
ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
exec "${PYTHON_BIN:-python3}" -B "$ROOT/operations.py" preflight "$@"
