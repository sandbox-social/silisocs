#!/usr/bin/env bash
# Rebuild both demo videos end to end. See README.md for prerequisites.
#
#   CHROME=<runnable chromium> ./run_all.sh [cli|studio|assemble]
#
# Stages (default: all):
#   cli      - capture the terminal tape (runs the real quickstart simulation),
#              then record it in the styled player
#   studio   - start a Studio server and record the scripted tour (launches one
#              real interactive run; expects the hero run + demo study to exist)
#   assemble - title cards + wait-compression + H.264 encode into docs/assets/videos
set -euo pipefail
cd "$(dirname "$0")"
REPO="$(cd .. && pwd)"
STAGE="${1:-all}"

# Emoji font for the terminal player (too large to commit).
if [ ! -s terminal/vendor/NotoColorEmoji.ttf ]; then
  curl -sL -o terminal/vendor/NotoColorEmoji.ttf \
    https://raw.githubusercontent.com/googlefonts/noto-emoji/main/fonts/NotoColorEmoji.ttf
fi
[ -d node_modules ] || npm install

if [ "$STAGE" = all ] || [ "$STAGE" = cli ]; then
  rm -rf "$REPO/outputs/misinformation/quickstart"
  (cd "$REPO" && set -a && . ./.env && set +a && python demo/capture_cli.py demo/tapes/cli.yaml demo/build/cli)
  node record_cli.mjs cli
fi

if [ "$STAGE" = all ] || [ "$STAGE" = studio ]; then
  # A warm Studio server; --state-dir keeps demo jobs out of your workspace state.
  (cd "$REPO" && set -a && . ./.env && set +a \
    && silisocs-studio --output-root outputs --port 8799 --state-dir "$REPO/demo/build/studio-state") &
  STUDIO_PID=$!
  trap 'kill $STUDIO_PID 2>/dev/null || true' EXIT
  for _ in $(seq 1 120); do
    curl -sf -o /dev/null http://127.0.0.1:8799/ && break
    sleep 1
  done
  STUDIO_URL=http://127.0.0.1:8799 node record_studio.mjs
  kill $STUDIO_PID 2>/dev/null || true
fi

if [ "$STAGE" = all ] || [ "$STAGE" = assemble ]; then
  python assemble.py build/cli.webm "$REPO/docs/assets/videos/silisocs-cli-quickstart.mp4" \
    --title "Silisocs" --subtitle "Quickstart from the command line"
  python assemble.py build/studio.webm "$REPO/docs/assets/videos/silisocs-studio-tour.mp4" \
    --title "Silisocs Studio" --subtitle "Scenario to study, in one workspace" \
    --segments build/studio.segments.json
fi
