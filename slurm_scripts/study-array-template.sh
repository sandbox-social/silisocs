#!/usr/bin/env bash
# Generic Slurm array template for Silisocs studies.
#
# Use silisocs.studies.run_study slurm-array to compute the array size and export
# the run plan, or copy this template and add site-specific SBATCH directives.

#SBATCH --job-name=silisocs-study
#SBATCH --time=04:00:00
#SBATCH --cpus-per-task=4
#SBATCH --array=0-0

set -euo pipefail

REPO_ROOT="${REPO_ROOT:-${SLURM_SUBMIT_DIR:-$PWD}}"
STUDY_FILE="${STUDY_FILE:?Set STUDY_FILE to a study.yaml path or study directory}"
RUNNER_PYTHON="${RUNNER_PYTHON:-python}"
PLAN_JSON="${PLAN_JSON:-${REPO_ROOT}/logs/study_plan_array_${SLURM_JOB_ID:-local}.json}"
ARRAY_MODE="${ARRAY_MODE:-case}"
MAX_CONCURRENT="${MAX_CONCURRENT:-1}"
HYPOTHESIS_IDS="${HYPOTHESIS_IDS:-}"
CONDITION_IDS="${CONDITION_IDS:-}"
SUB_EXPERIMENT_IDS="${SUB_EXPERIMENT_IDS:-}"
SEED_IDS="${SEED_IDS:-}"
RUN_IDS="${RUN_IDS:-}"
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
mkdir -p "$(dirname "${PLAN_JSON}")"

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

PLAN_ARGS=(--study "${STUDY_FILE}" plan --output "${PLAN_JSON}")
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
if [[ -n "${RUN_IDS}" ]]; then
  PLAN_ARGS+=(--only-run-id "${RUN_IDS}")
fi

if [[ ! -f "${PLAN_JSON}" ]]; then
  "${RUNNER_PYTHON}" -m silisocs.studies.run_study "${PLAN_ARGS[@]}"
else
  echo "Using existing PLAN_JSON: ${PLAN_JSON}"
fi

mapfile -t RUN_MATRIX < <(
  "${RUNNER_PYTHON}" - <<'PY'
import json
import os
from pathlib import Path

rows = json.loads(Path(os.environ["PLAN_JSON"]).read_text()).get("plan", [])
mode = os.environ.get("ARRAY_MODE", "case").strip().lower()
seen = set()

def emit(key):
    if key not in seen:
        seen.add(key)
        print("::".join(key))

for row in rows:
    if mode == "case":
        emit((str(row.get("hypothesis", "")), str(row.get("condition", ""))))
    elif mode == "seed":
        emit((str(row.get("hypothesis", "")), str(row.get("condition", "")), str(row.get("seed", ""))))
    elif mode == "hypothesis":
        emit((str(row.get("hypothesis", "")),))
    elif mode == "run":
        emit((str(row.get("run_id", "")),))
    else:
        raise SystemExit(f"Unsupported ARRAY_MODE={mode}")
PY
)

if [[ "${#RUN_MATRIX[@]}" -eq 0 ]]; then
  echo "No expanded runs found for requested filters" >&2
  exit 1
fi

TASK_ID="${SLURM_ARRAY_TASK_ID:-0}"
if (( TASK_ID < 0 || TASK_ID >= ${#RUN_MATRIX[@]} )); then
  echo "SLURM_ARRAY_TASK_ID=${TASK_ID} out of range for ${#RUN_MATRIX[@]} groups; exiting cleanly."
  exit 0
fi

TARGET="${RUN_MATRIX[${TASK_ID}]}"
RUN_ARGS=(--study "${STUDY_FILE}" run --max-concurrent "${MAX_CONCURRENT}")

case "${ARRAY_MODE}" in
  case)
    RUN_ARGS+=(--only-hypothesis "${TARGET%%::*}" --only-condition "${TARGET##*::}")
    ;;
  seed)
    TARGET_HYPOTHESIS="${TARGET%%::*}"
    REST="${TARGET#*::}"
    TARGET_CONDITION="${REST%%::*}"
    TARGET_SEED="${REST##*::}"
    RUN_ARGS+=(--only-hypothesis "${TARGET_HYPOTHESIS}" --only-condition "${TARGET_CONDITION}" --only-seed "${TARGET_SEED}")
    ;;
  hypothesis)
    RUN_ARGS+=(--only-hypothesis "${TARGET}")
    ;;
  run)
    RUN_ARGS+=(--only-run-id "${TARGET}")
    ;;
esac

if [[ -n "${SUB_EXPERIMENT_IDS}" ]]; then
  RUN_ARGS+=(--only-sub-experiment "${SUB_EXPERIMENT_IDS}")
fi
if [[ -n "${CONDITION_IDS}" && "${ARRAY_MODE}" == "hypothesis" ]]; then
  RUN_ARGS+=(--only-condition "${CONDITION_IDS}")
fi
if [[ -n "${SEED_IDS}" && "${ARRAY_MODE}" != "seed" && "${ARRAY_MODE}" != "run" ]]; then
  RUN_ARGS+=(--only-seed "${SEED_IDS}")
fi
if [[ -n "${RUN_IDS}" && "${ARRAY_MODE}" != "run" ]]; then
  RUN_ARGS+=(--only-run-id "${RUN_IDS}")
fi

"${RUNNER_PYTHON}" -m silisocs.studies.run_study "${RUN_ARGS[@]}"
