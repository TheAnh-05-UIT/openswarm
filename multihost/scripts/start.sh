#!/usr/bin/env bash
# Khởi chạy một stage đã được cho phép; không retry hoặc cleanup tự động.
set -euo pipefail
ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
exec "${PYTHON_BIN:-python3}" -B "$ROOT/operations.py" start "$@"
