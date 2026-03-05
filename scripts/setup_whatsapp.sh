#!/usr/bin/env bash
# ─────────────────────────────────────────────────
# WhatsApp Cloud API — Quick Setup Helper
# Generates secrets & prints the env vars to append
# ─────────────────────────────────────────────────
set -euo pipefail

echo "=== Ekaette WhatsApp Setup ==="
echo ""

# ── Generate secrets ──
WA_VERIFY_TOKEN=$(openssl rand -hex 16)
WA_SVC_SECRET=$(openssl rand -hex 32)

echo "Generated secrets (save these!):"
echo "  WHATSAPP_VERIFY_TOKEN = $WA_VERIFY_TOKEN"
echo "  WA_SERVICE_SECRET     = $WA_SVC_SECRET"
echo ""

# ── Print env block for Cloud Run / .env ──
cat <<ENVBLOCK
# ─── Copy the block below into your .env (or Cloud Run env vars) ───

# WhatsApp Cloud API — Text/Media Messaging
WHATSAPP_ENABLED=false
WHATSAPP_ACCESS_TOKEN=              # Already set if you have WA SIP calling
WHATSAPP_PHONE_NUMBER_ID=           # Already set if you have WA SIP calling
WHATSAPP_API_VERSION=v25.0
WHATSAPP_APP_SECRET=                # ← MANUAL: Meta Dashboard → Settings → Basic
WHATSAPP_VERIFY_TOKEN=${WA_VERIFY_TOKEN}
WA_SERVICE_SECRET=${WA_SVC_SECRET}
WA_SERVICE_SECRET_PREVIOUS=
WA_SERVICE_AUTH_MAX_SKEW_SECONDS=300
WA_CLOUD_TASKS_QUEUE_NAME=wa-webhook-processing
WA_CLOUD_TASKS_MAX_ATTEMPTS=3
WA_CLOUD_TASKS_AUDIENCE=            # ← SET AFTER: https://<WA_SERVICE_API_DOMAIN>/api/v1/at/whatsapp/process
WA_GRAPH_RETRY_MAX_ATTEMPTS=3
WA_GRAPH_RETRY_MAX_BACKOFF_SECONDS=8
WA_UTILITY_TEMPLATE_NAME=           # ← MANUAL: create template in Meta Business Manager first
WA_UTILITY_TEMPLATE_LANGUAGE=en_US
WA_SEND_IDEMPOTENCY_TTL_HOURS=24
WA_WEBHOOK_RATE_LIMIT_MODE=best_effort_local   # Change to edge_enforced for production
WA_EDGE_RATELIMIT_HEADER=X-Edge-RateLimit-Checked
WA_REPLAY_BUCKET=                   # ← SET AFTER: terraform apply (use output value)
WA_REPLAY_BLOB_PREFIX=wa/replay/
WA_REPLAY_BLOB_TTL_HOURS=24

# ─── Copy the block below into your SIP bridge VM .env ───

WA_SERVICE_API_BASE_URL=            # ← SET AFTER: https://<WA_SERVICE_API_DOMAIN>
WA_SERVICE_SECRET=${WA_SVC_SECRET}

ENVBLOCK

echo ""
echo "=== Remaining manual steps (5 total) ==="
echo ""
echo "1. Copy App Secret from Meta Dashboard → set WHATSAPP_APP_SECRET above"
echo "2. In Meta Dashboard → WhatsApp → Configuration:"
echo "   Webhook URL: https://<YOUR_WA_WEBHOOK_DOMAIN>/api/v1/at/whatsapp/webhook"
echo "   Verify Token: ${WA_VERIFY_TOKEN}"
echo "   Subscribe to: messages"
echo "3. Create a utility template in Meta Business Manager"
echo "   → set WA_UTILITY_TEMPLATE_NAME to the approved template name"
echo "4. Set Terraform domain vars in terraform.tfvars:"
echo "   wa_webhook_domain, wa_service_api_domain, app_public_domain, wa_vm_egress_cidrs"
echo "5. After terraform apply, set:"
echo "   WA_CLOUD_TASKS_AUDIENCE, WA_REPLAY_BUCKET, WA_SERVICE_API_BASE_URL"
echo "   Then: WHATSAPP_ENABLED=true"
echo ""
echo "Done! 21 of 26 steps handled. 5 remain (Meta Dashboard + domains)."
