#!/usr/bin/env bash
# Deploy Ekaette backend to Cloud Run WITH env vars.
#
# Usage:
#   ./scripts/deploy_cloudrun.sh            # build + deploy + set env vars
#   ./scripts/deploy_cloudrun.sh --env-only # update env vars without rebuilding
#
# This script ensures env vars are never wiped by a bare `gcloud run deploy --source .`.
# It reads from .env, filters out SIP bridge / WA bridge VM-only vars, converts to YAML,
# and applies via --env-vars-file.

set -euo pipefail

PROJECT="ekaette"
REGION="us-central1"
SERVICE="ekaette"
PORT="8000"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"
ENV_FILE="$ROOT_DIR/.env"
TMP_YAML="/tmp/ekaette_cloudrun_env.yaml"

if [[ ! -f "$ENV_FILE" ]]; then
  echo "ERROR: $ENV_FILE not found" >&2
  exit 1
fi

echo "==> Generating Cloud Run env vars from .env ..."

python3 -c "
import yaml, sys

# Patterns for VM-only env vars (SIP bridge, WA bridge runtime)
SKIP_PREFIXES = (
    'SIP_BRIDGE_HOST', 'SIP_BRIDGE_PORT', 'SIP_PUBLIC_IP', 'SIP_REGISTRAR',
    'SIP_USERNAME', 'SIP_PASSWORD', 'SIP_REGISTER_', 'SIP_HEALTH_',
    'SIP_RTP_', 'SIP_ALLOWED_', 'SIP_SYSTEM_',
    'WA_SIP_', 'WA_GATEWAY_', 'WA_LIVE_MODEL', 'WA_SYSTEM_',
    'WA_GEMINI_', 'WA_SANDBOX_', 'WA_HEALTH_', 'WA_COMPANY_',
    'WA_TENANT_',
    'GATEWAY_MODE', 'GATEWAY_WS_',
)
# Cloud Run reserves PORT
RESERVED = {'PORT'}

envs = {}
with open('$ENV_FILE') as f:
    for line in f:
        line = line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        key, val = line.split('=', 1)
        key = key.strip()
        val = val.strip()
        if not val:
            continue
        if key in RESERVED:
            continue
        if any(key.startswith(p) for p in SKIP_PREFIXES):
            continue
        envs[key] = val

# Always ensure these are set
envs['ALLOW_MISSING_WS_ORIGIN'] = 'true'

with open('$TMP_YAML', 'w') as f:
    yaml.dump(envs, f, default_flow_style=False)

print(f'  {len(envs)} env vars written to $TMP_YAML')
"

if [[ "${1:-}" == "--env-only" ]]; then
  echo "==> Updating env vars only (no rebuild) ..."
  gcloud run services update "$SERVICE" \
    --project "$PROJECT" \
    --region "$REGION" \
    --port="$PORT" \
    --env-vars-file="$TMP_YAML" \
    --quiet
else
  echo "==> Building and deploying ..."
  gcloud run deploy "$SERVICE" \
    --source "$ROOT_DIR" \
    --project "$PROJECT" \
    --region "$REGION" \
    --port="$PORT" \
    --env-vars-file="$TMP_YAML" \
    --quiet
fi

echo "==> Done. Service URL: https://${SERVICE}-233619833678.${REGION}.run.app"
