#!/usr/bin/env bash
set -euo pipefail

# Deploy the primary east-region HTTP/control-plane service.

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"

export SERVICE="${SERVICE:-ekaette-east-canary}"
export REGION="${REGION:-us-east1}"
export MIN_INSTANCES="${MIN_INSTANCES:-2}"
export ALLOW_UNAUTHENTICATED="${ALLOW_UNAUTHENTICATED:-1}"
export WA_SERVICE_TARGET_SERVICE="${WA_SERVICE_TARGET_SERVICE:-ekaette-east-canary}"
export WA_SERVICE_TARGET_REGION="${WA_SERVICE_TARGET_REGION:-us-east1}"

exec "$ROOT_DIR/scripts/deploy_cloud_run.sh" "$@"
