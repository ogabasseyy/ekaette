#!/usr/bin/env bash
set -euo pipefail

AWS_REGION="${AWS_REGION:-us-east-1}"
ECS_CLUSTER="${ECS_CLUSTER:?set ECS_CLUSTER}"
ECS_SERVICE="${ECS_SERVICE:?set ECS_SERVICE}"

echo "Forcing new deployment: ${ECS_CLUSTER}/${ECS_SERVICE} (${AWS_REGION})"
aws ecs update-service \
  --region "$AWS_REGION" \
  --cluster "$ECS_CLUSTER" \
  --service "$ECS_SERVICE" \
  --force-new-deployment >/dev/null

echo "Waiting for service stability..."
aws ecs wait services-stable \
  --region "$AWS_REGION" \
  --cluster "$ECS_CLUSTER" \
  --services "$ECS_SERVICE"

echo "Deployment complete."

