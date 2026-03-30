#!/usr/bin/env bash
set -euo pipefail

# Shared worker logic for cluster array wrappers.
# Cluster-specific sbatch directives should live in thin wrapper scripts.

REPO_ROOT="${REPO_ROOT:-$HOME/mastodon-sim}"
UV_HOME="${UV_HOME:-$HOME}"
STUDY_FILE="${STUDY_FILE:-experiments/studies/election_opinion_program_v1.yaml}"
UV_PROJECT_DIR="${UV_PROJECT_DIR:-${REPO_ROOT}}"
VLLM_BIN="${VLLM_BIN:-$HOME/.venvs/vllm/bin/vllm}"
HF_HUB_OFFLINE="${HF_HUB_OFFLINE:-1}"
HF_DATASETS_OFFLINE="${HF_DATASETS_OFFLINE:-1}"

VLLM_HOST="${VLLM_HOST:-127.0.0.1}"
VLLM_PORT="${VLLM_PORT:-30000}"
VLLM_MODEL="${VLLM_MODEL:-$SCRATCH/models/Qwen3.5-4B}"
VLLM_SERVED_NAME="${VLLM_SERVED_NAME:-qwen3.5-4b}"
VLLM_TP="${VLLM_TP:-1}"
VLLM_DP="${VLLM_DP:-2}"
VLLM_GPU_MEM_UTIL="${VLLM_GPU_MEM_UTIL:-0.92}"
VLLM_MAX_LEN="${VLLM_MAX_LEN:-30000}"

HYPOTHESIS_IDS="${HYPOTHESIS_IDS:-h1_initial_news_bias_shift}"
CONDITION_IDS="${CONDITION_IDS:-}"
SUB_EXPERIMENT_IDS="${SUB_EXPERIMENT_IDS:-}"
SEED_IDS="${SEED_IDS:-}"
ARRAY_MODE="${ARRAY_MODE:-case}"
MAX_CONCURRENT="${MAX_CONCURRENT:-8}"

if [[ ! -d "${REPO_ROOT}" ]]; then
  echo "REPO_ROOT does not exist: ${REPO_ROOT}" >&2
  exit 1
fi

if [[ ! -f "${REPO_ROOT}/${STUDY_FILE}" ]]; then
  echo "Study file not found: ${REPO_ROOT}/${STUDY_FILE}" >&2
  exit 1
fi

export HF_HUB_OFFLINE HF_DATASETS_OFFLINE
echo "HF_HUB_OFFLINE=${HF_HUB_OFFLINE} HF_DATASETS_OFFLINE=${HF_DATASETS_OFFLINE}"

mkdir -p "${REPO_ROOT}/logs"

cleanup() {
  if [[ -n "${VLLM_PID:-}" ]]; then
    echo "Stopping vLLM server PID=${VLLM_PID}"
    kill "${VLLM_PID}" >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT

PLAN_JSON="${PLAN_JSON:-${REPO_ROOT}/logs/study_plan_array_${SLURM_JOB_ID:-local}.json}"
export PLAN_JSON

PLAN_ARGS=(
  --study "${STUDY_FILE}"
  plan
  --output "${PLAN_JSON}"
)

if [[ -n "${HYPOTHESIS_IDS}" ]]; then
  PLAN_ARGS+=(--only-hypothesis "${HYPOTHESIS_IDS}")
fi
if [[ -n "${CONDITION_IDS}" ]]; then
  PLAN_ARGS+=(--only-condition "${CONDITION_IDS}")
fi
if [[ -n "${SUB_EXPERIMENT_IDS}" ]]; then
  PLAN_ARGS+=(--only-sub-experiment "${SUB_EXPERIMENT_IDS}")
fi
if [[ -n "${SEED_IDS}" ]]; then
  PLAN_ARGS+=(--only-seed "${SEED_IDS}")
fi

if [[ ! -f "${PLAN_JSON}" ]]; then
  echo "Building expanded plan for this job"
  (
    cd "${REPO_ROOT}"
    uv run python -m experiments.run_study "${PLAN_ARGS[@]}"
  )
else
  echo "Using existing PLAN_JSON: ${PLAN_JSON}"
fi

mapfile -t RUN_MATRIX < <(
  cd "${REPO_ROOT}"
  uv run python - <<'PY'
import json
import os
from pathlib import Path

plan_path = Path(os.environ["PLAN_JSON"])
array_mode = os.environ.get("ARRAY_MODE", "case").strip().lower()
data = json.loads(plan_path.read_text())
rows = data.get("plan", [])

if array_mode == "case":
  seen = set()
  for row in rows:
    key = (str(row.get("hypothesis", "")), str(row.get("condition", "")))
    if key not in seen:
      seen.add(key)
      print("::".join(key))
elif array_mode == "seed":
  seen = set()
  for row in rows:
    key = (
      str(row.get("hypothesis", "")),
      str(row.get("condition", "")),
      str(row.get("seed", "")),
    )
    if key not in seen:
      seen.add(key)
      print("::".join(key))
elif array_mode == "hypothesis":
  seen = set()
  for row in rows:
    key = str(row.get("hypothesis", ""))
    if key not in seen:
      seen.add(key)
      print(key)
elif array_mode == "run":
  for row in rows:
    print(
      "::".join(
        [
          str(row.get("hypothesis", "")),
          str(row.get("condition", "")),
          str(row.get("seed", "")),
          str(row.get("scenario", "")),
        ]
      )
    )
else:
  raise SystemExit(f"Unsupported ARRAY_MODE={array_mode}")
PY
)

if [[ "${#RUN_MATRIX[@]}" -eq 0 ]]; then
  echo "No expanded runs found for requested filters" >&2
  exit 1
fi

TASK_ID="${SLURM_ARRAY_TASK_ID:-0}"
if (( TASK_ID < 0 || TASK_ID >= ${#RUN_MATRIX[@]} )); then
  echo "SLURM_ARRAY_TASK_ID=${TASK_ID} out of range for ${#RUN_MATRIX[@]} runs; exiting cleanly."
  exit 0
fi

TARGET="${RUN_MATRIX[${TASK_ID}]}"
TARGET_HYPOTHESIS=""
TARGET_CONDITION=""
TARGET_SEED=""
TARGET_SCENARIO=""

case "${ARRAY_MODE}" in
  case)
    TARGET_HYPOTHESIS="${TARGET%%::*}"
    TARGET_CONDITION="${TARGET##*::}"
    ;;
  seed)
    TARGET_HYPOTHESIS="${TARGET%%::*}"
    REST="${TARGET#*::}"
    TARGET_CONDITION="${REST%%::*}"
    TARGET_SEED="${REST##*::}"
    ;;
  hypothesis)
    TARGET_HYPOTHESIS="${TARGET}"
    ;;
  run)
    TARGET_HYPOTHESIS="${TARGET%%::*}"
    REST="${TARGET#*::}"
    TARGET_CONDITION="${REST%%::*}"
    REST="${REST#*::}"
    TARGET_SEED="${REST%%::*}"
    TARGET_SCENARIO="${REST##*::}"
    ;;
esac

echo "[1/4] Starting vLLM server from ${UV_HOME}"
cd "${UV_HOME}"
if [[ -x "${VLLM_BIN}" ]]; then
  "${VLLM_BIN}" serve "${VLLM_MODEL}" \
    --served-model-name "${VLLM_SERVED_NAME}" \
    --host "${VLLM_HOST}" \
    --port "${VLLM_PORT}" \
    --tensor-parallel-size "${VLLM_TP}" \
    --data-parallel-size "${VLLM_DP}" \
    --gpu-memory-utilization "${VLLM_GPU_MEM_UTIL}" \
    --max-model-len "${VLLM_MAX_LEN}" \
    --enable-prefix-caching \
    --enable-chunked-prefill \
    --language-model-only \
    --reasoning-parser qwen3 \
    --enable-auto-tool-choice \
    --tool-call-parser qwen3_coder \
    > "${REPO_ROOT}/logs/vllm_${VLLM_SERVED_NAME}_array${TASK_ID}.log" 2>&1 &
else
  uv run --project "${UV_PROJECT_DIR}" vllm serve "${VLLM_MODEL}" \
    --served-model-name "${VLLM_SERVED_NAME}" \
    --host "${VLLM_HOST}" \
    --port "${VLLM_PORT}" \
    --tensor-parallel-size "${VLLM_TP}" \
    --data-parallel-size "${VLLM_DP}" \
    --gpu-memory-utilization "${VLLM_GPU_MEM_UTIL}" \
    --max-model-len "${VLLM_MAX_LEN}" \
    --enable-prefix-caching \
    --enable-chunked-prefill \
    --language-model-only \
    --reasoning-parser qwen3 \
    --enable-auto-tool-choice \
    --tool-call-parser qwen3_coder \
    > "${REPO_ROOT}/logs/vllm_${VLLM_SERVED_NAME}_array${TASK_ID}.log" 2>&1 &
fi
VLLM_PID=$!

echo "Waiting for vLLM health check"
VLLM_READY=0
for _ in $(seq 1 120); do
  if curl -sf "http://${VLLM_HOST}:${VLLM_PORT}/v1/models" >/dev/null; then
    VLLM_READY=1
    break
  fi
  if ! kill -0 "${VLLM_PID}" >/dev/null 2>&1; then
    break
  fi
  sleep 5
done

if [[ "${VLLM_READY}" -ne 1 ]]; then
  echo "vLLM failed to become healthy on ${VLLM_HOST}:${VLLM_PORT}" >&2
  echo "Tail of vLLM log:" >&2
  tail -n 120 "${REPO_ROOT}/logs/vllm_${VLLM_SERVED_NAME}_array${TASK_ID}.log" >&2 || true
  exit 1
fi

echo "[2/4] Repo: ${REPO_ROOT}"
cd "${REPO_ROOT}"

PLAN_RUN_ARGS=(
  --study "${STUDY_FILE}"
  plan
)

RUN_ARGS=(
  --study "${STUDY_FILE}"
  run
  --max-concurrent "${MAX_CONCURRENT}"
)

case "${ARRAY_MODE}" in
  case)
    PLAN_RUN_ARGS+=(--only-hypothesis "${TARGET_HYPOTHESIS}" --only-condition "${TARGET_CONDITION}")
    RUN_ARGS+=(--only-hypothesis "${TARGET_HYPOTHESIS}" --only-condition "${TARGET_CONDITION}")
    ;;
  seed|run)
    PLAN_RUN_ARGS+=(
      --only-hypothesis "${TARGET_HYPOTHESIS}"
      --only-condition "${TARGET_CONDITION}"
      --only-seed "${TARGET_SEED}"
    )
    RUN_ARGS+=(
      --only-hypothesis "${TARGET_HYPOTHESIS}"
      --only-condition "${TARGET_CONDITION}"
      --only-seed "${TARGET_SEED}"
    )
    ;;
  hypothesis)
    PLAN_RUN_ARGS+=(--only-hypothesis "${TARGET_HYPOTHESIS}")
    RUN_ARGS+=(--only-hypothesis "${TARGET_HYPOTHESIS}")
    ;;
esac

if [[ -n "${SUB_EXPERIMENT_IDS}" ]]; then
  PLAN_RUN_ARGS+=(--only-sub-experiment "${SUB_EXPERIMENT_IDS}")
  RUN_ARGS+=(--only-sub-experiment "${SUB_EXPERIMENT_IDS}")
fi

echo "[3/4] Plan selected run group"
uv run python -m experiments.run_study "${PLAN_RUN_ARGS[@]}"

echo "[4/4] Run selected run group with max_concurrent=${MAX_CONCURRENT}"
uv run python -m experiments.run_study "${RUN_ARGS[@]}"

echo "Completed array task ${TASK_ID} in mode ${ARRAY_MODE}"
