#!/usr/bin/env bash
set -euo pipefail

# Deploy the dedicated realtime websocket service.
#
# This keeps long-lived SIP/WA websocket sessions off the primary ingress
# service so AT callback and health requests stay responsive.

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"

export SERVICE="${SERVICE:-ekaette-live-east-canary}"
export REGION="${REGION:-us-east1}"
export APP_MODULE="${APP_MODULE:-main_live:app}"
export MIN_INSTANCES="${MIN_INSTANCES:-1}"
export ALLOW_UNAUTHENTICATED="${ALLOW_UNAUTHENTICATED:-1}"

exec "$ROOT_DIR/scripts/deploy_cloud_run.sh" "$@"
