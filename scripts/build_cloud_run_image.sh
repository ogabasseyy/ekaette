#!/usr/bin/env bash
set -euo pipefail

# Build one reusable image for the east-region Cloud Run services.

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"

PROJECT="${PROJECT:-ekaette}"
REGION="${REGION:-us-east1}"
REPOSITORY="${REPOSITORY:-cloud-run-release}"
IMAGE_NAME="${IMAGE_NAME:-ekaette-app}"
TAG="${TAG:-$(git -C "${ROOT_DIR}" rev-parse --short HEAD)-$(date -u +%Y%m%d%H%M%S)}"
IMAGE_URI="${REGION}-docker.pkg.dev/${PROJECT}/${REPOSITORY}/${IMAGE_NAME}:${TAG}"

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

echo "Preparing reusable image build for ${PROJECT} (${REGION})"
echo "Image URI: ${IMAGE_URI}"
echo "Using .gcloudignore to reduce upload size where possible."

ensure_repository

gcloud builds submit "${ROOT_DIR}" \
  --project "${PROJECT}" \
  --region "${REGION}" \
  --tag "${IMAGE_URI}"

echo "${IMAGE_URI}"
