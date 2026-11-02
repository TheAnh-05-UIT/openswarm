#!/usr/bin/env bash
# Dừng exact owned IDs trên host đã chọn, giữ network/container/storage.
set -euo pipefail
ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
exec "${PYTHON_BIN:-python3}" -B "$ROOT/operations.py" stop "$@"
