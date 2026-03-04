#!/usr/bin/env bash
set -euo pipefail

AWS_REGION="${AWS_REGION:-us-east-1}"
ECS_CLUSTER="${ECS_CLUSTER:?set ECS_CLUSTER}"
ECS_SERVICE="${ECS_SERVICE:?set ECS_SERVICE}"
TASK_DEF_ARN="${TASK_DEF_ARN:?set TASK_DEF_ARN}"
POLL_INTERVAL_SEC="${POLL_INTERVAL_SEC:-10}"
ROLLOUT_TIMEOUT_SEC="${ROLLOUT_TIMEOUT_SEC:-600}"

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

elapsed=0
while true; do
  primary_rollout_state="$(aws ecs describe-services \
    --region "$AWS_REGION" \
    --cluster "$ECS_CLUSTER" \
    --services "$ECS_SERVICE" \
    --query 'services[0].deployments[?status==`PRIMARY`].rolloutState | [0]' \
    --output text)"

  if [[ "$primary_rollout_state" == "COMPLETED" ]]; then
    break
  fi
  if [[ "$primary_rollout_state" == "FAILED" ]]; then
    echo "Rollback deployment is not fully completed." >&2
    echo "Rollout: $primary_rollout_state" >&2
    exit 1
  fi
  if (( elapsed >= ROLLOUT_TIMEOUT_SEC )); then
    echo "Rollback deployment is not fully completed." >&2
    echo "Rollout: $primary_rollout_state" >&2
    echo "Timed out after ${ROLLOUT_TIMEOUT_SEC}s." >&2
    exit 1
  fi

  sleep "$POLL_INTERVAL_SEC"
  elapsed=$((elapsed + POLL_INTERVAL_SEC))
done

current_task_def="$(aws ecs describe-services \
  --region "$AWS_REGION" \
  --cluster "$ECS_CLUSTER" \
  --services "$ECS_SERVICE" \
  --query 'services[0].taskDefinition' \
  --output text)"
if [[ "$current_task_def" != "$TASK_DEF_ARN" ]]; then
  echo "Rollback did not converge to expected task definition." >&2
  echo "Expected: $TASK_DEF_ARN" >&2
  echo "Current:  $current_task_def" >&2
  exit 1
fi

echo "Rollback complete."
