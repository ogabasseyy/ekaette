#!/usr/bin/env bash
set -euo pipefail

SERVICE_NAME="${SERVICE_NAME:-ekaette}"
REGION="${REGION:-us-central1}"
PROJECT_ID="${PROJECT_ID:-$(gcloud config get-value project 2>/dev/null)}"
TIMEOUT="${TIMEOUT:-3600}"
MEMORY="${MEMORY:-1Gi}"
CPU="${CPU:-2}"
MIN_INSTANCES="${MIN_INSTANCES:-1}"
ALLOW_UNAUTHENTICATED="${ALLOW_UNAUTHENTICATED:-0}"
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

# ── Memory Bank (Agent Engine) ────────────────────────────────────────────────
# For production memory persistence, set AGENT_ENGINE_ID in .env before deploy.
# Provision with: python -m scripts.provision_agent_engine
# The AGENT_ENGINE_ID is auto-read from .env and passed to Cloud Run below.

# ── Build env-vars YAML file from .env ────────────────────────────────────────
ENV_FILE="${ENV_FILE:-.env}"
ENV_YAML=$(mktemp /tmp/deploy-env-XXXXXX.yaml)
trap 'rm -f "${ENV_YAML}"' EXIT

if [[ -f "${ENV_FILE}" ]]; then
  echo "Reading environment variables from ${ENV_FILE}"
  count=0
  while IFS='=' read -r key value; do
    # Skip comments, blank lines, and keys with empty values
    [[ -z "${key}" || "${key}" =~ ^# ]] && continue
    [[ -z "${value}" ]] && continue
    # Strip surrounding quotes from value
    value="${value%\"}"
    value="${value#\"}"
    value="${value%\'}"
    value="${value#\'}"
    # Skip placeholder values
    [[ "${value}" == "<"*">" ]] && continue
    # Skip Cloud Run reserved env vars
    [[ "${key}" == "PORT" || "${key}" == "K_SERVICE" || "${key}" == "K_REVISION" || "${key}" == "K_CONFIGURATION" ]] && continue
    # Escape backslashes and quotes for valid YAML
    value="${value//\\/\\\\}"
    value="${value//\"/\\\"}"
    echo "${key}: \"${value}\"" >> "${ENV_YAML}"
    count=$((count + 1))
  done < <(grep -E '^[A-Z_]+=.' "${ENV_FILE}")
  echo "Loaded ${count} env vars into ${ENV_YAML}"
else
  echo "Warning: ${ENV_FILE} not found — deploying with minimal env vars"
  cat > "${ENV_YAML}" <<YAML
ADMIN_AUTH_MODE: iap
IDEMPOTENCY_STORE_BACKEND: firestore
CONNECTOR_CIRCUIT_BACKEND: firestore
CONNECTOR_LOCK_BACKEND: firestore
YAML
fi

AUTH_FLAG="--no-allow-unauthenticated"
if [[ "${ALLOW_UNAUTHENTICATED}" == "1" ]]; then
  AUTH_FLAG="--allow-unauthenticated"
fi

gcloud run deploy "${SERVICE_NAME}" \
  --source . \
  --project "${PROJECT_ID}" \
  --region "${REGION}" \
  --timeout "${TIMEOUT}" \
  --session-affinity \
  --memory "${MEMORY}" \
  --cpu "${CPU}" \
  --min-instances "${MIN_INSTANCES}" \
  ${AUTH_FLAG} \
  --env-vars-file "${ENV_YAML}"

echo "Deployment complete."
