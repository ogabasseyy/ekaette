#!/usr/bin/env bash
set -euo pipefail

SERVICE_NAME="${SERVICE_NAME:-ekaette}"
REGION="${REGION:-us-central1}"
PROJECT_ID="${PROJECT_ID:-$(gcloud config get-value project 2>/dev/null)}"
TIMEOUT="${TIMEOUT:-3600}"
MEMORY="${MEMORY:-1Gi}"
CPU="${CPU:-2}"
MIN_INSTANCES="${MIN_INSTANCES:-1}"

if [[ -z "${PROJECT_ID}" || "${PROJECT_ID}" == "(unset)" ]]; then
  echo "PROJECT_ID is not set and gcloud default project is unset."
  echo "Set PROJECT_ID env var or run: gcloud config set project <project-id>"
  exit 1
fi

echo "Deploying ${SERVICE_NAME} to Cloud Run (${REGION}) in project ${PROJECT_ID}"
gcloud run deploy "${SERVICE_NAME}" \
  --source . \
  --project "${PROJECT_ID}" \
  --region "${REGION}" \
  --timeout "${TIMEOUT}" \
  --session-affinity \
  --memory "${MEMORY}" \
  --cpu "${CPU}" \
  --min-instances "${MIN_INSTANCES}" \
  --allow-unauthenticated

echo "Deployment complete."
