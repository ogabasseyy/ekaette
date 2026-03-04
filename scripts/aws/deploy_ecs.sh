#!/usr/bin/env bash
set -euo pipefail

AWS_REGION="${AWS_REGION:-us-east-1}"
ECS_CLUSTER="${ECS_CLUSTER:?set ECS_CLUSTER}"
ECS_SERVICE="${ECS_SERVICE:?set ECS_SERVICE}"

echo "Forcing new deployment: ${ECS_CLUSTER}/${ECS_SERVICE} (${AWS_REGION})"
deployment_id="$(aws ecs update-service \
  --region "$AWS_REGION" \
  --cluster "$ECS_CLUSTER" \
  --service "$ECS_SERVICE" \
  --force-new-deployment \
  --query 'service.deployments[?status==`PRIMARY`].id | [0]' \
  --output text)"

echo "Waiting for service stability..."
aws ecs wait services-stable \
  --region "$AWS_REGION" \
  --cluster "$ECS_CLUSTER" \
  --services "$ECS_SERVICE"

primary_deployment_id="$(aws ecs describe-services \
  --region "$AWS_REGION" \
  --cluster "$ECS_CLUSTER" \
  --services "$ECS_SERVICE" \
  --query 'services[0].deployments[?status==`PRIMARY`].id | [0]' \
  --output text)"
primary_rollout_state="$(aws ecs describe-services \
  --region "$AWS_REGION" \
  --cluster "$ECS_CLUSTER" \
  --services "$ECS_SERVICE" \
  --query 'services[0].deployments[?status==`PRIMARY`].rolloutState | [0]' \
  --output text)"

if [[ -z "$deployment_id" || "$deployment_id" == "None" ]]; then
  echo "Failed to capture deployment ID from update-service output." >&2
  exit 1
fi
if [[ "$primary_deployment_id" != "$deployment_id" ]]; then
  echo "Deployment did not converge to the forced deployment." >&2
  echo "Expected deployment: $deployment_id" >&2
  echo "Current primary:    $primary_deployment_id" >&2
  exit 1
fi
if [[ "$primary_rollout_state" != "COMPLETED" ]]; then
  echo "Primary deployment rollout is not completed." >&2
  echo "Deployment: $primary_deployment_id" >&2
  echo "Rollout:    $primary_rollout_state" >&2
  exit 1
fi

echo "Deployment complete."
