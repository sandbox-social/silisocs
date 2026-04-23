#!/usr/bin/env bash
set -euo pipefail

# Shared worker logic for direct runner jobs (non-study mode).
# Starts vLLM, waits for readiness, then executes the runner command provided
# via script args (or RUNNER_ARGS env var).

REPO_ROOT="${REPO_ROOT:-$HOME/mastodon-sim}"
UV_HOME="${UV_HOME:-$HOME}"
UV_PROJECT_DIR="${UV_PROJECT_DIR:-${REPO_ROOT}}"
RUNNER_MODULE="${RUNNER_MODULE:-mastodon_sim.runtime.runner}"
RUNNER_CONFIG_PATH="${RUNNER_CONFIG_PATH:-}"
RUNNER_ARGS="${RUNNER_ARGS:-}"

VLLM_BIN="${VLLM_BIN:-$HOME/.venvs/vllm-home/bin/vllm}"
VLLM_HOST="${VLLM_HOST:-127.0.0.1}"
VLLM_PORT="${VLLM_PORT:-30000}"
VLLM_MODEL="${VLLM_MODEL:-$SCRATCH/models/qwen/Qwen3.5-9B}"
VLLM_SERVED_NAME="${VLLM_SERVED_NAME:-qwen3.5-9b}"
VLLM_TP="${VLLM_TP:-1}"
VLLM_DP="${VLLM_DP:-4}"
VLLM_GPU_MEM_UTIL="${VLLM_GPU_MEM_UTIL:-0.92}"
VLLM_MAX_LEN="${VLLM_MAX_LEN:-30000}"
VLLM_GDN_PREFILL_BACKEND="${VLLM_GDN_PREFILL_BACKEND:-flashinfer}"
VLLM_STARTUP_TIMEOUT_SECONDS="${VLLM_STARTUP_TIMEOUT_SECONDS:-2400}"

if [[ ! -d "${REPO_ROOT}" ]]; then
  echo "REPO_ROOT does not exist: ${REPO_ROOT}" >&2
  exit 1
fi

if command -v module >/dev/null 2>&1; then
  module load cuda/12.6 || true
fi
if [[ -n "${CUDA_HOME:-}" && -d "${CUDA_HOME}/bin" ]]; then
  export PATH="${CUDA_HOME}/bin:${PATH}"
fi

export UV_PROJECT_ENVIRONMENT="${UV_PROJECT_ENVIRONMENT:-$SCRATCH/.venvs/mastodon-sim}"
export UV_CACHE_DIR="${UV_CACHE_DIR:-$SCRATCH/.cache/uv}"
export UV_LINK_MODE="${UV_LINK_MODE:-copy}"
export XDG_CACHE_HOME="${XDG_CACHE_HOME:-$SCRATCH/.cache}"
export HF_HOME="${HF_HOME:-$SCRATCH/.cache/huggingface}"
export HF_HUB_CACHE="${HF_HUB_CACHE:-$HF_HOME/hub}"
export HF_DATASETS_CACHE="${HF_DATASETS_CACHE:-$HF_HOME/datasets}"
export TRANSFORMERS_CACHE="${TRANSFORMERS_CACHE:-$HF_HOME/transformers}"
export VLLM_CACHE_ROOT="${VLLM_CACHE_ROOT:-$SCRATCH/.cache/vllm}"
export TWHIN_MODEL_PATH="${TWHIN_MODEL_PATH:-$SCRATCH/models/twitter/twhin-bert-base}"
export TORCHINDUCTOR_CACHE_DIR="${TORCHINDUCTOR_CACHE_DIR:-$VLLM_CACHE_ROOT/torchinductor}"
export TRITON_CACHE_DIR="${TRITON_CACHE_DIR:-$VLLM_CACHE_ROOT/triton}"

mkdir -p "${REPO_ROOT}/logs"
mkdir -p "${UV_PROJECT_ENVIRONMENT}" "${UV_CACHE_DIR}"
mkdir -p "${HF_HUB_CACHE}" "${HF_DATASETS_CACHE}" "${TRANSFORMERS_CACHE}"
mkdir -p "${VLLM_CACHE_ROOT}" "${TORCHINDUCTOR_CACHE_DIR}" "${TRITON_CACHE_DIR}"

cleanup() {
  if [[ -n "${VLLM_PID:-}" ]]; then
    echo "Stopping vLLM server PID=${VLLM_PID}"
    kill "${VLLM_PID}" >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT

echo "[1/3] Starting vLLM"
cd "${UV_HOME}"
VLLM_FLAGS=(
  --served-model-name "${VLLM_SERVED_NAME}"
  --host "${VLLM_HOST}"
  --port "${VLLM_PORT}"
  --tensor-parallel-size "${VLLM_TP}"
  --data-parallel-size "${VLLM_DP}"
  --gpu-memory-utilization "${VLLM_GPU_MEM_UTIL}"
  --max-model-len "${VLLM_MAX_LEN}"
  --enable-prefix-caching
  --enable-chunked-prefill
  --language-model-only
  --reasoning-parser qwen3
  --enable-auto-tool-choice
  --tool-call-parser qwen3_coder
  --gdn-prefill-backend "${VLLM_GDN_PREFILL_BACKEND}"
)
if [[ -x "${VLLM_BIN}" ]]; then
  "${VLLM_BIN}" serve "${VLLM_MODEL}" "${VLLM_FLAGS[@]}" > "${REPO_ROOT}/logs/vllm_${VLLM_SERVED_NAME}_direct.log" 2>&1 &
else
  uv run --project "${UV_PROJECT_DIR}" vllm serve "${VLLM_MODEL}" "${VLLM_FLAGS[@]}" > "${REPO_ROOT}/logs/vllm_${VLLM_SERVED_NAME}_direct.log" 2>&1 &
fi
VLLM_PID=$!

echo "Waiting for vLLM health check"
VLLM_READY=0
sleep_interval=5
max_checks=$(( (VLLM_STARTUP_TIMEOUT_SECONDS + sleep_interval - 1) / sleep_interval ))
for _ in $(seq 1 "${max_checks}"); do
  if curl -sf "http://${VLLM_HOST}:${VLLM_PORT}/v1/models" >/dev/null; then
    VLLM_READY=1
    break
  fi
  if ! kill -0 "${VLLM_PID}" >/dev/null 2>&1; then
    break
  fi
  sleep "${sleep_interval}"
done

if [[ "${VLLM_READY}" -ne 1 ]]; then
  echo "vLLM failed to become healthy on ${VLLM_HOST}:${VLLM_PORT}" >&2
  echo "Tail of vLLM log:" >&2
  tail -n 120 "${REPO_ROOT}/logs/vllm_${VLLM_SERVED_NAME}_direct.log" >&2 || true
  exit 1
fi

echo "[2/3] Building runner command"
cd "${REPO_ROOT}"
CMD=(uv run python -m "${RUNNER_MODULE}")
if [[ -n "${RUNNER_CONFIG_PATH}" ]]; then
  CMD+=(--config-path "${RUNNER_CONFIG_PATH}")
fi

if (( $# > 0 )); then
  CMD+=("$@")
elif [[ -n "${RUNNER_ARGS}" ]]; then
  read -r -a RUNNER_ARGS_ARR <<< "${RUNNER_ARGS}"
  CMD+=("${RUNNER_ARGS_ARR[@]}")
else
  cat >&2 <<'EOF'
No runner overrides were provided.
Provide Hydra overrides either:
  1) as script arguments, e.g. scenario=default sim.num_steps=50
  2) via RUNNER_ARGS env var
EOF
  exit 1
fi

printf 'Executing:\n%s\n' "${CMD[*]}"
"${CMD[@]}" | tee "${REPO_ROOT}/logs/direct_runner_$(date +%Y%m%d_%H%M%S).log"
echo "[3/3] Run finished."
