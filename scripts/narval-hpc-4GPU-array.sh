#!/usr/bin/env bash
#SBATCH --account=ctb-liyue
#SBATCH --time=12:00:00
#SBATCH --mem-per-cpu=48G
#SBATCH --gpus-per-node=a100:2
#SBATCH --array=0-0
#SBATCH --cpus-per-task=4

set -euo pipefail

# When Slurm stages this script, BASH_SOURCE points into a spool directory.
# Resolve worker script from submit/work dir to keep wrappers portable.
SUBMIT_ROOT="${SLURM_SUBMIT_DIR:-${PWD}}"
WORKER_SCRIPT="${SUBMIT_ROOT}/scripts/study-array-worker.sh"

if [[ ! -f "${WORKER_SCRIPT}" ]]; then
	SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
	WORKER_SCRIPT="${SCRIPT_DIR}/study-array-worker.sh"
fi

if [[ ! -f "${WORKER_SCRIPT}" ]]; then
	echo "Could not locate study-array-worker.sh" >&2
	echo "Tried: ${SUBMIT_ROOT}/scripts/study-array-worker.sh and ${SCRIPT_DIR:-<unresolved>}/study-array-worker.sh" >&2
	exit 1
fi

exec "${WORKER_SCRIPT}" "$@"
