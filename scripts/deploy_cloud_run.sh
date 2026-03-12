#!/usr/bin/env bash
# Deploy Ekaette backend to Cloud Run.
#
# Usage:
#   SERVICE=ekaette-east-canary ./scripts/deploy_cloud_run.sh
#   SERVICE=ekaette-east-canary ./scripts/deploy_cloud_run.sh --env-only
#
# This is the canonical Cloud Run deployment entrypoint. Keep Cloud Run config,
# release gates, and .env-to-YAML export logic here so deploy behavior does not drift.

set -euo pipefail

SERVICE="${SERVICE:-${SERVICE_NAME:-ekaette}}"
PROJECT="${PROJECT:-${PROJECT_ID:-ekaette}}"
REGION="${REGION:-us-east1}"
PORT="${PORT:-8080}"
TIMEOUT="${TIMEOUT:-3600}"
MEMORY="${MEMORY:-1Gi}"
CPU="${CPU:-2}"
CONCURRENCY="${CONCURRENCY:-80}"
MIN_INSTANCES="${MIN_INSTANCES:-2}"
ALLOW_UNAUTHENTICATED="${ALLOW_UNAUTHENTICATED:-0}"
RUN_RELEASE_GATE="${RUN_RELEASE_GATE:-1}"
RUN_DOCS_CHECK="${RUN_DOCS_CHECK:-0}"
RELEASE_GATE_STRICT="${RELEASE_GATE_STRICT:-1}"
SESSION_AFFINITY="${SESSION_AFFINITY:-1}"
APP_MODULE="${APP_MODULE:-}"
IMAGE_URI="${IMAGE_URI:-${IMAGE:-}}"
WA_SERVICE_TARGET_SERVICE="${WA_SERVICE_TARGET_SERVICE:-}"
WA_SERVICE_TARGET_REGION="${WA_SERVICE_TARGET_REGION:-${REGION}}"
WA_SERVICE_API_BASE_URL_OVERRIDE="${WA_SERVICE_API_BASE_URL_OVERRIDE:-}"
WA_CLOUD_TASKS_AUDIENCE_OVERRIDE="${WA_CLOUD_TASKS_AUDIENCE_OVERRIDE:-}"
PYTHON_BIN="${PYTHON_BIN:-}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"
ENV_FILE="${ENV_FILE:-$ROOT_DIR/.env}"
ENV_YAML="$(mktemp "${TMPDIR:-/tmp}/ekaette-cloudrun-env-XXXXXX")"
ENV_ONLY=0

usage() {
  echo "Usage: SERVICE=<service> [REGION=<region>] [IMAGE_URI=<image>] $0 [--env-only]" >&2
  exit 1
}

if [[ $# -gt 1 ]]; then
  usage
fi
if [[ "${1:-}" == "--env-only" ]]; then
  ENV_ONLY=1
elif [[ $# -eq 1 ]]; then
  usage
fi

if [[ -z "${SERVICE+x}" && -z "${SERVICE_NAME+x}" ]]; then
  cat >&2 <<'EOF'
Refusing to deploy without an explicit SERVICE.

Use one of:
  ./scripts/deploy_cloud_run_main.sh
  ./scripts/deploy_cloud_run_live.sh
or set SERVICE=<name> explicitly before running deploy_cloud_run.sh.
EOF
  exit 2
fi

if [[ -z "${PYTHON_BIN}" ]]; then
  if [[ -x "${ROOT_DIR}/.venv/bin/python" ]]; then
    PYTHON_BIN="${ROOT_DIR}/.venv/bin/python"
  elif command -v python3 >/dev/null 2>&1; then
    PYTHON_BIN="python3"
  elif command -v python >/dev/null 2>&1; then
    PYTHON_BIN="python"
  else
    echo "No Python interpreter found. Set PYTHON_BIN or install python3." >&2
    exit 1
  fi
fi

trap 'rm -f "${ENV_YAML}"' EXIT

if [[ "${RUN_RELEASE_GATE}" == "1" && "${ENV_ONLY}" != "1" ]]; then
  echo "Running release gates before deployment..."
  GATE_CMD=("${PYTHON_BIN}" -m scripts.release_gate --repo-root "${ROOT_DIR}")
  if [[ "${RUN_DOCS_CHECK}" == "1" ]]; then
    GATE_CMD+=(--run-docs-check)
  fi
  if [[ "${RELEASE_GATE_STRICT}" == "1" ]]; then
    GATE_CMD+=(--strict)
  fi
  "${GATE_CMD[@]}"
fi

echo "Preparing Cloud Run config for ${SERVICE} in project ${PROJECT} (${REGION})"

if [[ -z "${WA_SERVICE_TARGET_SERVICE}" && "${SERVICE}" == "ekaette-east-canary" && "${REGION}" == "us-east1" ]]; then
  WA_SERVICE_TARGET_SERVICE="${SERVICE}"
  WA_SERVICE_TARGET_REGION="${REGION}"
fi

if [[ -n "${WA_SERVICE_TARGET_SERVICE}" && -z "${WA_SERVICE_API_BASE_URL_OVERRIDE}" ]]; then
  WA_SERVICE_API_BASE_URL_OVERRIDE="$(
    gcloud run services describe "${WA_SERVICE_TARGET_SERVICE}" \
      --project "${PROJECT}" \
      --region "${WA_SERVICE_TARGET_REGION}" \
      --format='value(status.url)'
  )"
fi

if [[ -n "${WA_SERVICE_API_BASE_URL_OVERRIDE}" && -z "${WA_CLOUD_TASKS_AUDIENCE_OVERRIDE}" ]]; then
  WA_CLOUD_TASKS_AUDIENCE_OVERRIDE="${WA_SERVICE_API_BASE_URL_OVERRIDE%/}/api/v1/at/whatsapp/process"
fi

"${PYTHON_BIN}" - "$ENV_FILE" "$ENV_YAML" "$APP_MODULE" \
  "$WA_SERVICE_API_BASE_URL_OVERRIDE" "$WA_CLOUD_TASKS_AUDIENCE_OVERRIDE" <<'PY'
from __future__ import annotations

import json
import pathlib
import re
import sys


env_file = pathlib.Path(sys.argv[1])
tmp_yaml = pathlib.Path(sys.argv[2])
app_module = sys.argv[3].strip()
wa_service_api_base_url_override = sys.argv[4].strip()
wa_cloud_tasks_audience_override = sys.argv[5].strip()

SKIP_PREFIXES = (
    "SIP_BRIDGE_HOST", "SIP_BRIDGE_PORT", "SIP_PUBLIC_IP", "SIP_REGISTRAR",
    "SIP_USERNAME", "SIP_PASSWORD", "SIP_REGISTER_", "SIP_HEALTH_",
    "SIP_RTP_", "SIP_ALLOWED_", "SIP_SYSTEM_",
    "WA_SIP_", "WA_GATEWAY_", "WA_LIVE_MODEL", "WA_SYSTEM_",
    "WA_GEMINI_", "WA_SANDBOX_", "WA_HEALTH_", "WA_COMPANY_",
    "WA_TENANT_", "WA_TLS_",
    "GATEWAY_MODE", "GATEWAY_WS_",
)
RESERVED = {"PORT", "K_SERVICE", "K_REVISION", "K_CONFIGURATION"}
DEFAULT_ENVS = {
    "ADMIN_AUTH_MODE": "iap",
    "IDEMPOTENCY_STORE_BACKEND": "firestore",
    "CONNECTOR_CIRCUIT_BACKEND": "firestore",
    "CONNECTOR_LOCK_BACKEND": "firestore",
}
assignment_re = re.compile(r"^(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)=(.*)$")


def normalize_value(raw: str) -> str:
    value = raw.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        value = value[1:-1]
    else:
        # Strip inline comments (only for unquoted values)
        comment_match = re.search(r'\s+#\s', value)
        if comment_match:
            value = value[:comment_match.start()]
    return value


def yaml_quote(raw: str) -> str:
    return json.dumps(raw, ensure_ascii=False)[1:-1]


envs: dict[str, str] = {}
if env_file.exists():
    for raw_line in env_file.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        match = assignment_re.match(line)
        if match is None:
            continue
        key, raw_value = match.groups()
        value = normalize_value(raw_value)
        if not value or (value.startswith("<") and value.endswith(">")):
            continue
        if key in RESERVED:
            continue
        if any(key.startswith(prefix) for prefix in SKIP_PREFIXES):
            continue
        envs[key] = value
else:
    envs.update(DEFAULT_ENVS)
    print(
        f"Warning: {env_file} not found; using minimal Cloud Run defaults only.",
        file=sys.stderr,
    )

# Cloud Run WebSocket clients such as the SIP and WA gateways may connect
# without an Origin header. Tighten ALLOWED_ORIGINS and revisit websocket
# checks in app/api/v1/public/core_helpers.py and app/api/v1/realtime/session_init.py
# before disabling or narrowing this override.
added_missing_origin_default = "ALLOW_MISSING_WS_ORIGIN" not in envs
if added_missing_origin_default:
    envs["ALLOW_MISSING_WS_ORIGIN"] = "true"
if app_module:
    envs["APP_MODULE"] = app_module
if wa_service_api_base_url_override:
    envs["WA_SERVICE_API_BASE_URL"] = wa_service_api_base_url_override.rstrip("/")
if wa_cloud_tasks_audience_override:
    envs["WA_CLOUD_TASKS_AUDIENCE"] = wa_cloud_tasks_audience_override.rstrip("/")
if envs.get("GOOGLE_GENAI_USE_VERTEXAI", "").strip().lower() == "true":
    # Runtime GenAI clients are constructed explicitly in code when Vertex is
    # enabled, so carrying GOOGLE_API_KEY into Cloud Run only reintroduces noisy
    # backend-precedence warnings and config drift.
    envs.pop("GOOGLE_API_KEY", None)

with tmp_yaml.open("w", encoding="utf-8") as handle:
    handle.write("# Generated by scripts/deploy_cloud_run.sh\n")
    if added_missing_origin_default:
        handle.write(
            "# Defaulted ALLOW_MISSING_WS_ORIGIN for Cloud Run websocket clients "
            "that omit an Origin header.\n"
        )
    for key in sorted(envs):
        handle.write(f'{key}: "{yaml_quote(envs[key])}"\n')

print(f"Loaded {len(envs)} env vars into {tmp_yaml}")
PY

DEPLOY_AUTH_FLAG=(--no-allow-unauthenticated)
UPDATE_AUTH_FLAG=(--invoker-iam-check)
if [[ "${ALLOW_UNAUTHENTICATED}" == "1" ]]; then
  DEPLOY_AUTH_FLAG=(--allow-unauthenticated)
  UPDATE_AUTH_FLAG=(--no-invoker-iam-check)
fi

SESSION_AFFINITY_FLAG=(--session-affinity)
if [[ "${SESSION_AFFINITY}" != "1" ]]; then
  SESSION_AFFINITY_FLAG=(--no-session-affinity)
fi

run_source_deploy() {
  local start_ts now elapsed heartbeat_interval deploy_pid
  heartbeat_interval=30
  start_ts="$(date +%s)"

  echo "Building and deploying ${SERVICE}..."
  echo "Note: after 'Uploading sources... done', Cloud Build may continue quietly for several minutes."
  echo "Do not interrupt the deploy early unless it clearly exceeds your normal build time."

  gcloud run deploy "${SERVICE}" \
    --source "${ROOT_DIR}" \
    --project "${PROJECT}" \
    --region "${REGION}" \
    --port="${PORT}" \
    --timeout="${TIMEOUT}" \
    --memory="${MEMORY}" \
    --cpu="${CPU}" \
    --concurrency="${CONCURRENCY}" \
    --min-instances="${MIN_INSTANCES}" \
    --cpu-throttling \
    "${SESSION_AFFINITY_FLAG[@]}" \
    --env-vars-file="${ENV_YAML}" \
    "${DEPLOY_AUTH_FLAG[@]}" \
    --quiet &
  deploy_pid=$!

  while kill -0 "${deploy_pid}" 2>/dev/null; do
    sleep "${heartbeat_interval}"
    if kill -0 "${deploy_pid}" 2>/dev/null; then
      now="$(date +%s)"
      elapsed=$((now - start_ts))
      echo "Deploy still running after ${elapsed}s. If source upload already finished, Cloud Build is likely still building/pushing the image." >&2
    fi
  done

  wait "${deploy_pid}"
}

run_image_deploy() {
  if [[ -z "${IMAGE_URI}" ]]; then
    echo "IMAGE_URI is required for image-based deploys." >&2
    exit 1
  fi

  echo "Deploying prebuilt image ${IMAGE_URI} to ${SERVICE}..."
  gcloud run deploy "${SERVICE}" \
    --image "${IMAGE_URI}" \
    --project "${PROJECT}" \
    --region "${REGION}" \
    --port="${PORT}" \
    --timeout="${TIMEOUT}" \
    --memory="${MEMORY}" \
    --cpu="${CPU}" \
    --concurrency="${CONCURRENCY}" \
    --min-instances="${MIN_INSTANCES}" \
    --cpu-throttling \
    "${SESSION_AFFINITY_FLAG[@]}" \
    --env-vars-file="${ENV_YAML}" \
    "${DEPLOY_AUTH_FLAG[@]}" \
    --quiet
}

if [[ "${ENV_ONLY}" == "1" ]]; then
  echo "Updating Cloud Run service config and env vars only (no rebuild)..."
  gcloud run services update "${SERVICE}" \
    --project "${PROJECT}" \
    --region "${REGION}" \
    --port="${PORT}" \
    --timeout="${TIMEOUT}" \
    --memory="${MEMORY}" \
    --cpu="${CPU}" \
    --concurrency="${CONCURRENCY}" \
    --min-instances="${MIN_INSTANCES}" \
    --cpu-throttling \
    "${SESSION_AFFINITY_FLAG[@]}" \
    --env-vars-file="${ENV_YAML}" \
    "${UPDATE_AUTH_FLAG[@]}" \
    --quiet
elif [[ -n "${IMAGE_URI}" ]]; then
  run_image_deploy
else
  run_source_deploy
fi

if [[ "${ENABLE_CROSS_CHANNEL_CONTEXT_TTL:-1}" == "1" ]]; then
  if ! gcloud firestore fields ttls update expires_at \
    --collection-group=cross_channel_context \
    --project "${PROJECT}" \
    --enable-ttl \
    --quiet 2>&1; then
    echo "Warning: Failed to update Firestore TTL policy (non-fatal)" >&2
  fi
fi

SERVICE_URL="$(gcloud run services describe "${SERVICE}" --project "${PROJECT}" --region "${REGION}" --format='value(status.url)' 2>/dev/null || true)"
if [[ -n "${SERVICE_URL}" ]]; then
  echo "Deployment complete. Service URL: ${SERVICE_URL}"
else
  PROJECT_NUMBER="$(gcloud projects describe "${PROJECT}" --format='value(projectNumber)' 2>/dev/null || true)"
  if [[ -n "${PROJECT_NUMBER}" ]]; then
    echo "Deployment complete. Service URL: https://${SERVICE}-${PROJECT_NUMBER}.${REGION}.run.app"
  else
    echo "Deployment complete. Unable to resolve the Cloud Run URL automatically for ${SERVICE} in ${REGION}."
  fi
fi
