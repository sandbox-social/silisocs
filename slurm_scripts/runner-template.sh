#!/usr/bin/env bash
# Generic Slurm template for one direct Silisocs runner invocation.
#
# Copy this file or pass scheduler options with sbatch. Site-specific account,
# partition, GPU, module, cache, and model-server setup belongs outside the
# public repository.
#
# Example:
#   sbatch --account=<account> --partition=<partition> \
#     slurm_scripts/runner-template.sh scenario=resource_market num_steps=3

#SBATCH --job-name=silisocs-runner
#SBATCH --time=04:00:00
#SBATCH --cpus-per-task=4

set -euo pipefail

REPO_ROOT="${REPO_ROOT:-${SLURM_SUBMIT_DIR:-$PWD}}"
RUNNER_MODULE="${RUNNER_MODULE:-silisocs.runtime.runner}"
RUNNER_CONFIG_PATH="${RUNNER_CONFIG_PATH:-}"
RUNNER_ARGS="${RUNNER_ARGS:-}"
SILISOCS_HPC_SETUP_COMMAND="${SILISOCS_HPC_SETUP_COMMAND:-}"
SILISOCS_HPC_SERVER_COMMAND="${SILISOCS_HPC_SERVER_COMMAND:-}"
SILISOCS_HPC_SERVER_READY_URL="${SILISOCS_HPC_SERVER_READY_URL:-}"
SILISOCS_HPC_SERVER_TIMEOUT_SECONDS="${SILISOCS_HPC_SERVER_TIMEOUT_SECONDS:-600}"

cleanup() {
  if [[ -n "${SILISOCS_SERVER_PID:-}" ]]; then
    kill "${SILISOCS_SERVER_PID}" >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT

cd "${REPO_ROOT}"

if [[ -n "${SILISOCS_HPC_SETUP_COMMAND}" ]]; then
  echo "Running SILISOCS_HPC_SETUP_COMMAND"
  bash -lc "${SILISOCS_HPC_SETUP_COMMAND}"
fi

if [[ -n "${SILISOCS_HPC_SERVER_COMMAND}" ]]; then
  echo "Starting SILISOCS_HPC_SERVER_COMMAND"
  bash -lc "${SILISOCS_HPC_SERVER_COMMAND}" &
  SILISOCS_SERVER_PID=$!
  if [[ -n "${SILISOCS_HPC_SERVER_READY_URL}" ]]; then
    echo "Waiting for ${SILISOCS_HPC_SERVER_READY_URL}"
    for _ in $(seq 1 "${SILISOCS_HPC_SERVER_TIMEOUT_SECONDS}"); do
      if curl -fsS "${SILISOCS_HPC_SERVER_READY_URL}" >/dev/null; then
        break
      fi
      sleep 1
    done
    curl -fsS "${SILISOCS_HPC_SERVER_READY_URL}" >/dev/null
  fi
fi

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
Provide Hydra overrides as script arguments, for example:
  scenario=resource_market num_steps=3
or set RUNNER_ARGS.
EOF
  exit 1
fi

printf 'Executing:\n%s\n' "${CMD[*]}"
"${CMD[@]}"
