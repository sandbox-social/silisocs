#!/usr/bin/env bash
# Rebuild the four product demo videos end to end. See README.md for prerequisites.
#
#   CHROME=<runnable chromium> ./run_all.sh [cli|studio|scenario|study|assemble]
set -euo pipefail

cd "$(dirname "$0")"
REPO="$(cd .. && pwd)"
STAGE="${1:-all}"
STUDIO_PID=""

case "$STAGE" in
  all|cli|studio|scenario|study|assemble) ;;
  *)
    echo "usage: $0 [cli|studio|scenario|study|assemble]" >&2
    exit 2
    ;;
esac

if [ -d "$REPO/.venv/bin" ]; then
  export PATH="$REPO/.venv/bin:$PATH"
fi

stop_studio() {
  if [ -n "$STUDIO_PID" ]; then
    kill "$STUDIO_PID" 2>/dev/null || true
    wait "$STUDIO_PID" 2>/dev/null || true
    STUDIO_PID=""
  fi
}
trap stop_studio EXIT

wait_for_studio() {
  local port="$1"
  local ready=0
  for _ in $(seq 1 120); do
    if curl -sf "http://127.0.0.1:${port}/api/ready" 2>/dev/null \
      | grep -q '"ready": *true'; then
      ready=1
      break
    fi
    sleep 1
  done
  if [ "$ready" -ne 1 ]; then
    echo "studio: /api/ready never reported ready after 120s" >&2
    exit 1
  fi
}

start_studio() {
  local port="$1"
  local repo_root="$2"
  local output_root="$3"
  local state_dir="$4"
  (
    cd "$REPO"
    set -a
    . ./.env
    set +a
    exec silisocs-studio \
      --repo-root "$repo_root" \
      --output-root "$output_root" \
      --port "$port" \
      --state-dir "$state_dir"
  ) &
  STUDIO_PID=$!
  wait_for_studio "$port"
}

needs_node=0
case "$STAGE" in
  all|cli|studio|scenario|study) needs_node=1 ;;
esac
if [ "$needs_node" -eq 1 ]; then
  [ -d node_modules ] || npm ci
fi

case "$STAGE" in
  all|cli|study)
    # Emoji font for the terminal player (too large to commit).
    if [ ! -s terminal/vendor/NotoColorEmoji.ttf ]; then
      curl -sL -o terminal/vendor/NotoColorEmoji.ttf \
        https://raw.githubusercontent.com/googlefonts/noto-emoji/main/fonts/NotoColorEmoji.ttf
    fi
    ;;
esac

if [ "$STAGE" = all ] || [ "$STAGE" = cli ]; then
  rm -rf "$REPO/outputs/misinformation/quickstart"
  (
    cd "$REPO"
    set -a
    . ./.env
    set +a
    python demo/capture_cli.py demo/tapes/cli.yaml demo/build/cli
  )
  node record_cli.mjs cli
fi

if [ "$STAGE" = all ] || [ "$STAGE" = studio ]; then
  # The tour uses existing run/study artifacts and launches one interactive run.
  start_studio \
    8799 \
    "$REPO" \
    "$REPO/outputs" \
    "$REPO/demo/build/studio-state"
  STUDIO_URL=http://127.0.0.1:8799 node record_studio.mjs
  stop_studio
fi

if [ "$STAGE" = all ] || [ "$STAGE" = scenario ]; then
  # Author into a disposable project so recording never modifies repository content.
  scenario_workspace="$REPO/demo/build/scenario-workspace"
  rm -rf "$scenario_workspace"
  mkdir -p "$scenario_workspace/scenarios" "$scenario_workspace/outputs"
  start_studio \
    8797 \
    "$scenario_workspace" \
    "$scenario_workspace/outputs" \
    "$scenario_workspace/.silisocs"
  STUDIO_URL=http://127.0.0.1:8797 node record_create_scenario.mjs
  stop_studio

  # Verify the UI round-trip exactly preserved the committed source scenario.
  diff -ru \
    "$REPO/scenarios/campus_rumor/conf" \
    "$scenario_workspace/scenarios/campus_rumor/conf"
fi

if [ "$STAGE" = all ] || [ "$STAGE" = study ]; then
  study_root="$REPO/experiments/studies/rumor_pressure_demo"
  # These directories are generated, gitignored, and specific to this demo.
  rm -rf "$study_root/generated" "$study_root/runs"
  (
    cd "$REPO"
    set -a
    . ./.env
    set +a
    python demo/capture_cli.py demo/tapes/study.yaml demo/build/design-study
  )
  node record_cli.mjs design-study
fi

if [ "$STAGE" = all ] || [ "$STAGE" = assemble ]; then
  python assemble.py build/cli.webm "$REPO/docs/assets/videos/silisocs-cli-quickstart.mp4" \
    --title "Silisocs" --subtitle "Quickstart from the command line"
  python assemble.py build/studio.webm "$REPO/docs/assets/videos/silisocs-studio-tour.mp4" \
    --title "Silisocs Studio" --subtitle "Scenario to study, in one workspace" \
    --segments build/studio.segments.json
  python assemble.py \
    build/create-scenario.webm \
    "$REPO/docs/assets/videos/silisocs-create-scenario.mp4" \
    --title "Silisocs" --subtitle "Create a scenario in Studio"
  python assemble.py \
    build/design-study.webm \
    "$REPO/docs/assets/videos/silisocs-design-study.mp4" \
    --title "Silisocs" --subtitle "Design a study & custom eval"
fi
