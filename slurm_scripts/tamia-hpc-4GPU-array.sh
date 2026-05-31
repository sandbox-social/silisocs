#!/usr/bin/env bash
#SBATCH --account=aip-rrabba
#SBATCH --time=12:00:00
#SBATCH --mem-per-cpu=48G
#SBATCH --nodes=1
#SBATCH --gpus-per-node=h100:4
#SBATCH --array=0-0
#SBATCH --cpus-per-task=4

set -euo pipefail

# Tamia cluster wrapper.
# Keep this file cluster-specific (SBATCH directives only).
# Shared execution logic lives in study-array-worker.sh.

if command -v module >/dev/null 2>&1; then
	module load cuda/12.6 || true
fi
if [[ -n "${CUDA_HOME:-}" && -d "${CUDA_HOME}/bin" ]]; then
	export PATH="${CUDA_HOME}/bin:${PATH}"
fi

export VLLM_MODEL="${VLLM_MODEL:-$SCRATCH/models/qwen/Qwen3.5-9B}"
export VLLM_SERVED_NAME="${VLLM_SERVED_NAME:-qwen3.5-9b}"
export VLLM_GPU_MEM_UTIL="${VLLM_GPU_MEM_UTIL:-0.92}"

# When Slurm stages this script, BASH_SOURCE points into a spool directory.
SUBMIT_ROOT="${SLURM_SUBMIT_DIR:-${PWD}}"
WORKER_SCRIPT="${SUBMIT_ROOT}/slurm_scripts/study-array-worker.sh"

if [[ ! -f "${WORKER_SCRIPT}" ]]; then
	SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
	WORKER_SCRIPT="${SCRIPT_DIR}/study-array-worker.sh"
fi

if [[ ! -f "${WORKER_SCRIPT}" ]]; then
	echo "Could not locate study-array-worker.sh" >&2
	echo "Tried: ${SUBMIT_ROOT}/slurm_scripts/study-array-worker.sh and ${SCRIPT_DIR:-<unresolved>}/study-array-worker.sh" >&2
	exit 1
fi

exec "${WORKER_SCRIPT}" "$@"
