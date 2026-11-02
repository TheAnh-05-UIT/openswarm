#!/usr/bin/env bash
# Kiểm tra bundle/import/env và Compose của đúng host; không startup.
set -euo pipefail
ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
exec "${PYTHON_BIN:-python3}" -B "$ROOT/operations.py" check "$@"
