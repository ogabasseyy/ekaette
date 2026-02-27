#!/usr/bin/env bash
set -euo pipefail

SERVICE_NAME="${SERVICE_NAME:-ekaette}"
REGION="${REGION:-us-central1}"
PROJECT_ID="${PROJECT_ID:-$(gcloud config get-value project 2>/dev/null)}"
TIMEOUT="${TIMEOUT:-3600}"
MEMORY="${MEMORY:-1Gi}"
CPU="${CPU:-2}"
MIN_INSTANCES="${MIN_INSTANCES:-1}"
RUN_RELEASE_GATE="${RUN_RELEASE_GATE:-1}"
RUN_DOCS_CHECK="${RUN_DOCS_CHECK:-0}"
RELEASE_GATE_STRICT="${RELEASE_GATE_STRICT:-1}"
PYTHON_BIN="${PYTHON_BIN:-}"

if [[ -z "${PROJECT_ID}" || "${PROJECT_ID}" == "(unset)" ]]; then
  echo "PROJECT_ID is not set and gcloud default project is unset."
  echo "Set PROJECT_ID env var or run: gcloud config set project <project-id>"
  exit 1
fi

if [[ -z "${PYTHON_BIN}" ]]; then
  if [[ -x "./.venv/bin/python" ]]; then
    PYTHON_BIN="./.venv/bin/python"
  elif command -v python3 >/dev/null 2>&1; then
    PYTHON_BIN="python3"
  elif command -v python >/dev/null 2>&1; then
    PYTHON_BIN="python"
  else
    echo "No Python interpreter found. Set PYTHON_BIN or install python3."
    exit 1
  fi
fi

if [[ "${RUN_RELEASE_GATE}" == "1" ]]; then
  echo "Running release gates before deployment..."
  GATE_CMD=("${PYTHON_BIN}" -m scripts.release_gate --repo-root .)
  if [[ "${RUN_DOCS_CHECK}" == "1" ]]; then
    GATE_CMD+=(--run-docs-check)
  fi
  if [[ "${RELEASE_GATE_STRICT}" == "1" ]]; then
    GATE_CMD+=(--strict)
  fi
  "${GATE_CMD[@]}"
fi

echo "Deploying ${SERVICE_NAME} to Cloud Run (${REGION}) in project ${PROJECT_ID}"
ADMIN_AUTH_MODE="${ADMIN_AUTH_MODE:-iap}"
ADMIN_IAP_AUDIENCE="${ADMIN_IAP_AUDIENCE:-}"
IDEMPOTENCY_STORE_BACKEND="${IDEMPOTENCY_STORE_BACKEND:-firestore}"
CONNECTOR_CIRCUIT_BACKEND="${CONNECTOR_CIRCUIT_BACKEND:-firestore}"
CONNECTOR_LOCK_BACKEND="${CONNECTOR_LOCK_BACKEND:-firestore}"

gcloud run deploy "${SERVICE_NAME}" \
  --source . \
  --project "${PROJECT_ID}" \
  --region "${REGION}" \
  --timeout "${TIMEOUT}" \
  --session-affinity \
  --memory "${MEMORY}" \
  --cpu "${CPU}" \
  --min-instances "${MIN_INSTANCES}" \
  --allow-unauthenticated \
  --set-env-vars "ADMIN_AUTH_MODE=${ADMIN_AUTH_MODE},ADMIN_IAP_AUDIENCE=${ADMIN_IAP_AUDIENCE},IDEMPOTENCY_STORE_BACKEND=${IDEMPOTENCY_STORE_BACKEND},CONNECTOR_CIRCUIT_BACKEND=${CONNECTOR_CIRCUIT_BACKEND},CONNECTOR_LOCK_BACKEND=${CONNECTOR_LOCK_BACKEND}"

echo "Deployment complete."
