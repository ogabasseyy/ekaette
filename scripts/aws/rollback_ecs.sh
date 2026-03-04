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

current_task_def="$(aws ecs describe-services \
  --region "$AWS_REGION" \
  --cluster "$ECS_CLUSTER" \
  --services "$ECS_SERVICE" \
  --query 'services[0].taskDefinition' \
  --output text)"
primary_rollout_state="$(aws ecs describe-services \
  --region "$AWS_REGION" \
  --cluster "$ECS_CLUSTER" \
  --services "$ECS_SERVICE" \
  --query 'services[0].deployments[?status==`PRIMARY`].rolloutState | [0]' \
  --output text)"

if [[ "$current_task_def" != "$TASK_DEF_ARN" ]]; then
  echo "Rollback did not converge to expected task definition." >&2
  echo "Expected: $TASK_DEF_ARN" >&2
  echo "Current:  $current_task_def" >&2
  exit 1
fi
if [[ "$primary_rollout_state" != "COMPLETED" ]]; then
  echo "Rollback deployment is not fully completed." >&2
  echo "Rollout: $primary_rollout_state" >&2
  exit 1
fi

echo "Rollback complete."
