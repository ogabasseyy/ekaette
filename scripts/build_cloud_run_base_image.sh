#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"

PROJECT="${PROJECT:-ekaette}"
REGION="${REGION:-us-east1}"
REPOSITORY="${REPOSITORY:-cloud-run-release}"
BASE_IMAGE_NAME="${BASE_IMAGE_NAME:-ekaette-runtime-base}"
BASE_TAG="${BASE_TAG:-$(cat "${ROOT_DIR}/requirements.txt" "${ROOT_DIR}/Dockerfile.base" | shasum -a 256 | awk '{print substr($1,1,16)}')}"
BASE_IMAGE_URI="${REGION}-docker.pkg.dev/${PROJECT}/${REPOSITORY}/${BASE_IMAGE_NAME}:${BASE_TAG}"

ensure_repository() {
  if gcloud artifacts repositories describe "${REPOSITORY}" \
    --project "${PROJECT}" \
    --location "${REGION}" >/dev/null 2>&1; then
    return
  fi

  echo "Creating Artifact Registry repository ${REPOSITORY} in ${REGION}..."
  gcloud artifacts repositories create "${REPOSITORY}" \
    --project "${PROJECT}" \
    --location "${REGION}" \
    --repository-format docker \
    --description "Reusable Cloud Run release images for Ekaette" \
    --quiet
}

ensure_repository

if gcloud artifacts docker images describe "${BASE_IMAGE_URI}" \
  --project "${PROJECT}" \
  --location "${REGION}" >/dev/null 2>&1; then
  echo "Reusing existing runtime base image:"
  echo "${BASE_IMAGE_URI}"
  exit 0
fi

echo "Building reusable runtime base image..."
echo "Base image URI: ${BASE_IMAGE_URI}"

gcloud builds submit "${ROOT_DIR}" \
  --project "${PROJECT}" \
  --region "${REGION}" \
  --tag "${BASE_IMAGE_URI}" \
  --file "${ROOT_DIR}/Dockerfile.base"

echo "${BASE_IMAGE_URI}"
