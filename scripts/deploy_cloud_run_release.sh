#!/usr/bin/env bash
set -euo pipefail

# Build once, deploy the same image to both east-region services.

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"

PROJECT="${PROJECT:-ekaette}"
REGION="${REGION:-us-east1}"
REPOSITORY="${REPOSITORY:-cloud-run-release}"
IMAGE_NAME="${IMAGE_NAME:-ekaette-app}"
TAG="${TAG:-$(git -C "${ROOT_DIR}" rev-parse --short HEAD)-$(date -u +%Y%m%d%H%M%S)}"
IMAGE_URI="${REGION}-docker.pkg.dev/${PROJECT}/${REPOSITORY}/${IMAGE_NAME}:${TAG}"

echo "Building one image for both east-region services..."
PROJECT="${PROJECT}" REGION="${REGION}" REPOSITORY="${REPOSITORY}" IMAGE_NAME="${IMAGE_NAME}" TAG="${TAG}" \
  "${ROOT_DIR}/scripts/build_cloud_run_image.sh"

echo "Deploying ${IMAGE_URI} to ${PROJECT}/${REGION} services..."
IMAGE_URI="${IMAGE_URI}" RUN_RELEASE_GATE=1 "${ROOT_DIR}/scripts/deploy_cloud_run_main.sh"
IMAGE_URI="${IMAGE_URI}" RUN_RELEASE_GATE=0 "${ROOT_DIR}/scripts/deploy_cloud_run_live.sh"

echo "Release complete with shared image:"
echo "  ${IMAGE_URI}"
