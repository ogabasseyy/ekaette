# Nova Migration Plan v2 (2026 Best-Practice Hardened)

## Objective
Ship an AWS-native Amazon Nova runtime parity build for Ekaette (web realtime + SIP + WhatsApp + core AT text paths) with production-grade deployment and proof artifacts for Devpost.

## Core Runtime Decisions
- Provider: `LLM_PROVIDER=amazon_nova`
- Region: `us-east-1`
- Voice transport: backend proxy only (no browser direct Bedrock stream)
- Deployment: ECS Fargate + ALB
- Storage: DynamoDB + S3
- Model fallback chains:
  - Voice: `amazon.nova-2-sonic-v1:0`, `amazon.nova-sonic-v1:0`
  - Reasoning: `amazon.nova-2-lite-v1:0`, `amazon.nova-lite-v1:0`
  - Vision: `amazon.nova-pro-v1:0`

## Completed in this migration baseline
1. Provider abstraction scaffold (`app/runtime/providers/*`) and startup capability probe.
2. Provider-aware text and vision bridges with Nova Bedrock clients.
3. `/api/token` compatibility behavior for proxy-only transport in Nova mode.
4. Health endpoints:
   - `GET /health`
   - `GET /health/live`
   - `GET /health/ready`
   - `GET /health/provider`
5. Frontend transport cleanup:
   - direct-live branch removed from socket runtime
   - legacy direct-live dependency removed
6. AWS deployment scaffolding:
   - `infra/aws/terraform/*` for ECS + ALB + IAM + DynamoDB + S3
   - deployment scripts and Bedrock readiness preflight under `scripts/aws/`

## Remaining High-Risk Workstreams
1. Full bidirectional Sonic voice stream integration in realtime loop.
2. Complete SIP and WhatsApp runtime switch from Nova sessions to Nova voice session transport.
3. End-to-end regression suite updates for provider-specific event behavior.
4. CloudWatch dashboard/alarm materialization + Bedrock invocation logging policy wiring.

## 48-Hour Execution Sequence
### Day 1
1. Wire Nova voice session transport into websocket streaming path.
2. Migrate SIP bridge session loop to `sip_bridge/nova_voice_client.py`.
3. Migrate WhatsApp session loop to `sip_bridge/nova_voice_client.py`.

### Day 2
1. Deploy ECS stack and validate websocket stability behind ALB.
2. Run Bedrock readiness check and load smoke tests.
3. Capture demo + AWS proof clip + architecture diagram.
4. Finalize README and Devpost submission text.

## Success Criteria
- Realtime conversation runs through Nova backend on deployed AWS endpoint.
- At least one tool-enabled flow (valuation/booking/support) works end-to-end.
- SIP/WhatsApp voice bridge paths run against Nova transport without codec regressions.
- Submission includes reproducible setup/deploy docs and cloud proof artifacts.

