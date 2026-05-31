#!/usr/bin/env bash
#SBATCH --account=aip-rrabba
#SBATCH --time=12:00:00
#SBATCH --mem-per-cpu=48G
#SBATCH --nodes=1
#SBATCH --gpus-per-node=h100:4
#SBATCH --cpus-per-task=4

set -euo pipefail

# Tamia cluster wrapper for direct non-study runner execution.
# Keep this file cluster-specific (SBATCH directives only).
# Shared execution logic lives in runner-worker.sh.

if command -v module >/dev/null 2>&1; then
	module load cuda/12.6 || true
fi
if [[ -n "${CUDA_HOME:-}" && -d "${CUDA_HOME}/bin" ]]; then
	export PATH="${CUDA_HOME}/bin:${PATH}"
fi

export VLLM_MODEL="${VLLM_MODEL:-$SCRATCH/models/qwen/Qwen3.5-9B}"
export VLLM_SERVED_NAME="${VLLM_SERVED_NAME:-qwen3.5-9b}"
export VLLM_GPU_MEM_UTIL="${VLLM_GPU_MEM_UTIL:-0.92}"

SUBMIT_ROOT="${SLURM_SUBMIT_DIR:-${PWD}}"
WORKER_SCRIPT="${SUBMIT_ROOT}/slurm_scripts/runner-worker.sh"

if [[ ! -f "${WORKER_SCRIPT}" ]]; then
	SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
	WORKER_SCRIPT="${SCRIPT_DIR}/runner-worker.sh"
fi

if [[ ! -f "${WORKER_SCRIPT}" ]]; then
	echo "Could not locate runner-worker.sh" >&2
	echo "Tried: ${SUBMIT_ROOT}/slurm_scripts/runner-worker.sh and ${SCRIPT_DIR:-<unresolved>}/runner-worker.sh" >&2
	exit 1
fi

exec "${WORKER_SCRIPT}" "$@"
