#!/usr/bin/env bash
# In kế hoạch một stage trên đúng host, không tạo tài nguyên.
set -euo pipefail
ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
exec "${PYTHON_BIN:-python3}" -B "$ROOT/operations.py" plan "$@"
