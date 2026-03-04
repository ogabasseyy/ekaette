#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT_DIR"

if [ ! -d .venv-nova ]; then
  python3 -m venv .venv-nova
fi

source .venv-nova/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt -r requirements-dev.txt

echo
echo "Nova environment ready."
echo "1) cp .env.nova.example .env"
echo "2) fill AWS credentials/role and Nova model IDs"
echo "3) run: uvicorn main:app --reload --port 8000"

