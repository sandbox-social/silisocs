#!/usr/bin/env bash
# Run the replication with NO language model, using the deterministic scripted
# behaviour in components/scripted_behavior.py.
#
# This is the fast development loop and what the end-to-end test drives: every
# phase executes (orders clear, dyads alternate, ratings parse) without a single
# provider call, so a config change can be checked in seconds.
#
#   ./replications/signaling/tools/run_scripted.sh [world] [extra hydra overrides...]
#
#   world: smoke (default) | default | asocial | asocial_personal
set -euo pipefail

WORLD="${1:-smoke}"
shift || true

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
cd "${ROOT}"

# `replications` is repository content, not part of the installed wheel, so the
# repo root has to be importable for the configured class_paths to resolve.
export PYTHONPATH="${ROOT}${PYTHONPATH:+:${PYTHONPATH}}"

# The behaviour needs the same data files and calendar the backends use; the
# ${...} values are resolved by Hydra against the selected world config, so this
# stays correct for every condition.
uv run silisocs \
  --config-path replications/signaling/conf \
  env=signaling \
  "world=${WORLD}" \
  sim.llm.provider=scripted \
  sim.llm.disabled=false \
  +sim.llm.extra_kwargs.behavior_class_path=replications.signaling.components.scripted_behavior.ScriptedSignalingBehavior \
  '+sim.llm.extra_kwargs.behavior_params.goods_path=${goods_path}' \
  '+sim.llm.extra_kwargs.behavior_params.sellers_path=${sellers_path}' \
  '+sim.llm.extra_kwargs.behavior_params.personas_path=${personas_path}' \
  '+sim.llm.extra_kwargs.behavior_params.num_agents=${num_agents}' \
  '+sim.llm.extra_kwargs.behavior_params.num_days=${num_days}' \
  '+sim.llm.extra_kwargs.behavior_params.seed=${seed}' \
  '+sim.llm.extra_kwargs.behavior_params.calendar=${calendar}' \
  "$@"
