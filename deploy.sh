#!/usr/bin/env bash
set -Eeuo pipefail

APP_DIR="/home/luftlage-sentinel2/apps/sentinel1-rfi-detection"
BRANCH="production"

echo "==> Starting deploy at $(date)"
cd "$APP_DIR"

echo "==> Fetching latest code"
git fetch --prune origin

echo "==> Checking out branch: $BRANCH"
git checkout "$BRANCH"

echo "==> Resetting to origin/$BRANCH"
git reset --hard "origin/$BRANCH"

echo "==> Ensuring virtual environment exists"
if [ ! -d ".venv" ]; then
  python3 -m venv .venv
fi

echo "==> Activating virtual environment"
source .venv/bin/activate

echo "==> Upgrading pip"
pip install --upgrade pip

if [ -f "requirements.txt" ]; then
  echo "==> Installing Python dependencies"
  pip install -r requirements.txt
fi

# Optional: make scripts executable if needed
if [ -d "bin" ]; then
  chmod +x bin/* || true
fi

echo "==> Deploy complete at $(date)"
