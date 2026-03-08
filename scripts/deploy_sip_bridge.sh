#!/usr/bin/env bash
set -euo pipefail
# Deploy SIP bridge + shared packages to GCE VM.
#
# Usage:
#   ./scripts/deploy_sip_bridge.sh            # full deploy
#   ZONE=us-east1-b ./scripts/deploy_sip_bridge.sh  # override zone
#
# Prerequisites:
#   - gcloud CLI authenticated with project access
#   - VM env files exist: /home/mac/sip_bridge.env, /home/mac/wa_bridge.env
#     (see scripts/systemd/sip-bridge-envvars.template and
#      scripts/systemd/wa-gateway-envvars.template for required vars)

ZONE="${ZONE:-us-central1-a}"
PROJECT="${PROJECT:-ekaette}"
VM="${VM:-ekaette-sip}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"

echo "Syncing shared/ to VM..."
gcloud compute scp --recurse "$ROOT_DIR/shared/" \
  "$VM:/home/mac/shared/" --zone="$ZONE" --project="$PROJECT"

echo "Syncing sip_bridge/ to VM..."
gcloud compute scp --recurse "$ROOT_DIR/sip_bridge/" \
  "$VM:/home/mac/sip_bridge/" --zone="$ZONE" --project="$PROJECT"

echo "Syncing requirements.txt to VM..."
gcloud compute scp "$ROOT_DIR/requirements.txt" \
  "$VM:/home/mac/requirements.txt" --zone="$ZONE" --project="$PROJECT"

echo "Installing pinned dependencies from requirements.txt..."
gcloud compute ssh "$VM" --zone="$ZONE" --project="$PROJECT" \
  --command="/home/mac/sip_venv/bin/pip install -q --upgrade -r /home/mac/requirements.txt"

echo "Installing systemd unit files from tracked templates..."
gcloud compute scp \
  "$ROOT_DIR/scripts/systemd/sip-bridge.service" \
  "$ROOT_DIR/scripts/systemd/wa-gateway.service" \
  "$VM:/tmp/" --zone="$ZONE" --project="$PROJECT"
gcloud compute ssh "$VM" --zone="$ZONE" --project="$PROJECT" \
  --command="sudo cp /tmp/sip-bridge.service /tmp/wa-gateway.service /etc/systemd/system/ && sudo systemctl daemon-reload"

echo "Enabling services for boot persistence..."
gcloud compute ssh "$VM" --zone="$ZONE" --project="$PROJECT" \
  --command="sudo systemctl enable sip-bridge.service wa-gateway.service"

echo "Verifying systemd runtime contract (fail-closed)..."
gcloud compute ssh "$VM" --zone="$ZONE" --project="$PROJECT" \
  --command="
    fail=0
    for unit in sip-bridge.service wa-gateway.service; do
      grep -q 'PYTHONPATH=/home/mac' /etc/systemd/system/\$unit || { echo \"FATAL: \$unit missing PYTHONPATH=/home/mac\"; fail=1; }
      grep -q 'WorkingDirectory=/home/mac' /etc/systemd/system/\$unit || { echo \"FATAL: \$unit missing WorkingDirectory=/home/mac\"; fail=1; }
    done
    # Verify env files referenced by units exist
    for envfile in /home/mac/sip_bridge.env /home/mac/wa_bridge.env; do
      [ -f \"\$envfile\" ] || { echo \"FATAL: \$envfile not found — create it before deploying\"; fail=1; }
    done
    exit \$fail
  "

echo "Restarting services..."
gcloud compute ssh "$VM" --zone="$ZONE" --project="$PROJECT" \
  --command="sudo systemctl restart sip-bridge.service wa-gateway.service"

echo "Checking status..."
gcloud compute ssh "$VM" --zone="$ZONE" --project="$PROJECT" \
  --command="systemctl is-active sip-bridge.service wa-gateway.service"

echo "SIP bridge deploy complete."
