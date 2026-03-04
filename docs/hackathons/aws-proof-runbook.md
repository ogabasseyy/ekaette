# AWS Proof Runbook (Nova Submission)

Use this to record the separate deployment proof clip required by Devpost.

## 1) Show live AWS deployment

1. Open ECS Console -> Cluster -> Service.
2. Show desired/running task count is healthy.
3. Open task -> log stream in CloudWatch.

## 2) Show realtime endpoint

1. Open ALB DNS URL and call:
   - `GET /health/live`
   - `GET /health/ready`
   - `GET /health/provider`
2. Confirm provider response shows `amazon_nova`.

## 3) Show model readiness

Run:

```bash
python3 scripts/aws/check_bedrock_readiness.py --region us-east-1
```

Show that at least one voice, reasoning, and vision model candidate is available.

## 4) Show request logs

In CloudWatch Logs:
1. Filter by latest request/session ID.
2. Show provider/model metadata in structured logs.

## 5) Record commands used

Show terminal run:

```bash
./scripts/aws/build_push_ecr.sh
./scripts/aws/deploy_ecs.sh
```

Then show successful service stability output.
