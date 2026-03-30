#!/usr/bin/env bash
#SBATCH --account=def-rrabba
#SBATCH --time=12:00:00
#SBATCH --mem-per-cpu=48G
#SBATCH --gpus-per-node=a100:4
#SBATCH --array=0-0
#SBATCH --cpus-per-task=4

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec "${SCRIPT_DIR}/study-array-worker.sh" "$@"
