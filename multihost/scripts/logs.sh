#!/usr/bin/env bash
# Đọc số dòng logs có giới hạn, chỉ từ container thuộc job đã chọn.
# Hỗ trợ --since UTC; JSON ghi rõ byte budget/truncation, không phải durable cursor.
set -euo pipefail
ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
exec "${PYTHON_BIN:-python3}" -B "$ROOT/operations.py" logs "$@"
