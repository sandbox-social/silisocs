#!/usr/bin/env bash
#SBATCH --account=ctb-liyue
#SBATCH --time=12:00:00
#SBATCH --mem-per-cpu=48G
#SBATCH --nodes=1
#SBATCH --gpus-per-node=a100:4
#SBATCH --array=0-0
#SBATCH --cpus-per-task=4

set -euo pipefail

export VLLM_MODEL="${VLLM_MODEL:-$SCRATCH/models/Qwen3.5-4B}"
export VLLM_SERVED_NAME="${VLLM_SERVED_NAME:-qwen3.5-4b}"
export VLLM_TP="${VLLM_TP:-1}"
export VLLM_DP="${VLLM_DP:-4}"

# When Slurm stages this script, BASH_SOURCE points into a spool directory.
# Resolve worker script from submit/work dir to keep wrappers portable.
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
