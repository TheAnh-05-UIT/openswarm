#!/usr/bin/env bash
# Sinh env mới từ import đã ghim ID; không ghi đè cấu hình hoặc env cũ.
set -euo pipefail
ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
exec "${PYTHON_BIN:-python3}" -B "$ROOT/operations.py" configure "$@"
