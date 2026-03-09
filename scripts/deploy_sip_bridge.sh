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

atomic_sync_dir() {
  local src_dir="$1"
  local dest_dir="$2"
  local base_name
  local temp_dir
  local old_dir
  base_name="$(basename "$dest_dir")"
  temp_dir="${dest_dir}.new"
  old_dir="${dest_dir}.old"

  gcloud compute ssh "$VM" --zone="$ZONE" --project="$PROJECT" \
    --command="rm -rf \"$temp_dir\" \"$old_dir\""
  gcloud compute scp --recurse "$src_dir" \
    "$VM:$temp_dir" --zone="$ZONE" --project="$PROJECT"
  gcloud compute ssh "$VM" --zone="$ZONE" --project="$PROJECT" \
    --command="
      set -e
      synced_path=\"$temp_dir\"
      if [ -d \"$temp_dir/$base_name\" ]; then
        synced_path=\"$temp_dir/$base_name\"
      fi
      rm -rf \"$old_dir\"
      if [ -e \"$dest_dir\" ]; then
        mv \"$dest_dir\" \"$old_dir\"
      fi
      mv \"\$synced_path\" \"$dest_dir\"
      if [ -d \"$temp_dir\" ] && [ \"$temp_dir\" != \"$dest_dir\" ]; then
        rm -rf \"$temp_dir\"
      fi
      rm -rf \"$old_dir\"
    "
}

echo "Syncing shared/ to VM..."
atomic_sync_dir "$ROOT_DIR/shared" "/home/mac/shared"

echo "Syncing sip_bridge/ to VM..."
atomic_sync_dir "$ROOT_DIR/sip_bridge" "/home/mac/sip_bridge"

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
    # Verify env files referenced by units exist and contain PHONE_ID_HMAC_KEY
    for envfile in /home/mac/sip_bridge.env /home/mac/wa_bridge.env; do
      [ -f \"\$envfile\" ] || { echo \"FATAL: \$envfile not found — create it before deploying\"; fail=1; continue; }
      grep -q '^PHONE_ID_HMAC_KEY=' \"\$envfile\" || { echo \"FATAL: \$envfile missing PHONE_ID_HMAC_KEY\"; fail=1; continue; }
      grep -qE '^PHONE_ID_HMAC_KEY=[\"'"'"'"]?ekaette-phone-id-dev-key[\"'"'"'"]?$' \"\$envfile\" && { echo \"FATAL: \$envfile still using dev-key for PHONE_ID_HMAC_KEY — generate a secure key\"; fail=1; }
    done
    exit \$fail
  "

echo "Restarting services..."
gcloud compute ssh "$VM" --zone="$ZONE" --project="$PROJECT" \
  --command="sudo systemctl restart sip-bridge.service wa-gateway.service"

echo "Checking status..."
gcloud compute ssh "$VM" --zone="$ZONE" --project="$PROJECT" \
  --command="systemctl is-active sip-bridge.service wa-gateway.service || { journalctl -u sip-bridge -u wa-gateway -n 20 --no-pager; exit 1; }"

echo "SIP bridge deploy complete."
