#!/usr/bin/env bash
# Hiển thị ID/state của container có đúng labels của host/job.
set -euo pipefail
ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
exec "${PYTHON_BIN:-python3}" -B "$ROOT/operations.py" status "$@"
