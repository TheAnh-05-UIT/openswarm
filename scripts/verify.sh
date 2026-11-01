#!/usr/bin/env bash
set -euo pipefail
source "$(dirname -- "${BASH_SOURCE[0]}")/common.sh"
load_settings
check_engine
dc config --quiet
# Retain this one-shot verification container for evidence; no --rm or cleanup.
dc --profile tools run --no-deps --pull never --name "$PROJECT-verification" verify
