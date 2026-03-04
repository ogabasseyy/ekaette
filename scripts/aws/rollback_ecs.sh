#!/usr/bin/env bash
set -euo pipefail

AWS_REGION="${AWS_REGION:-us-east-1}"
ECS_CLUSTER="${ECS_CLUSTER:?set ECS_CLUSTER}"
ECS_SERVICE="${ECS_SERVICE:?set ECS_SERVICE}"
TASK_DEF_ARN="${TASK_DEF_ARN:?set TASK_DEF_ARN}"

echo "Rolling back ${ECS_CLUSTER}/${ECS_SERVICE} to task definition:"
echo "  ${TASK_DEF_ARN}"

aws ecs update-service \
  --region "$AWS_REGION" \
  --cluster "$ECS_CLUSTER" \
  --service "$ECS_SERVICE" \
  --task-definition "$TASK_DEF_ARN" >/dev/null

aws ecs wait services-stable \
  --region "$AWS_REGION" \
  --cluster "$ECS_CLUSTER" \
  --services "$ECS_SERVICE"

echo "Rollback complete."

