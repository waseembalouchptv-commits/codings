#!/bin/bash
# Cloud-session setup: Python deps for docedit, the HyperFrames CLI + GSAP (npm),
# Chrome Headless Shell for HyperFrames rendering, and the HyperFrames agent skills.
# Idempotent; each step is skipped when already satisfied.
set -euo pipefail

if [ "${CLAUDE_CODE_REMOTE:-}" != "true" ]; then
  exit 0
fi

cd "$CLAUDE_PROJECT_DIR"

pip install --quiet --disable-pip-version-check -r requirements.txt 2>&1 | grep -v "Running pip as the 'root' user" || true

npm install --no-fund --no-audit --silent

npx --no-install hyperframes browser ensure >/dev/null

# Core HyperFrames skills plus the workflows that fit talking-head documentaries.
npx --no-install hyperframes skills update talking-head-recut embedded-captions general-video >/dev/null 2>&1 \
  || echo "warning: HyperFrames skills update failed (network?); CLI still works" >&2

echo "docedit + HyperFrames ready"
