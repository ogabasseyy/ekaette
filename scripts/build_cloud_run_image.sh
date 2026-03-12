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

echo "Preparing reusable image build for ${PROJECT} (${REGION})"
echo "Image URI: ${IMAGE_URI}"
echo "Runtime base image: ${BASE_IMAGE_URI}"
echo "Using .gcloudignore to reduce upload size where possible."

PROJECT="${PROJECT}" REGION="${REGION}" REPOSITORY="${REPOSITORY}" BASE_IMAGE_NAME="${BASE_IMAGE_NAME}" BASE_TAG="${BASE_TAG}" \
  "${ROOT_DIR}/scripts/build_cloud_run_base_image.sh" >/dev/null

BUILD_CONFIG="$(mktemp "${TMPDIR:-/tmp}/ekaette-release-build-XXXXXX.yaml")"
trap 'rm -f "${BUILD_CONFIG}"' EXIT
cat >"${BUILD_CONFIG}" <<EOF
steps:
  - name: gcr.io/cloud-builders/docker
    args:
      - build
      - -f
      - Dockerfile.release
      - -t
      - ${IMAGE_URI}
      - --build-arg
      - BASE_IMAGE=${BASE_IMAGE_URI}
      - .
images:
  - ${IMAGE_URI}
EOF

gcloud builds submit "${ROOT_DIR}" \
  --project "${PROJECT}" \
  --region "${REGION}" \
  --config "${BUILD_CONFIG}"

echo "${IMAGE_URI}"
