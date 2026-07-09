"""Built-in Engine loop policies."""

from __future__ import annotations

import time
from collections.abc import Mapping
from typing import Any

from silisocs.runtime.telemetry.engine_metrics import (
    capture_retry_counters,
    collect_unique_models,
    set_model_retry_phase,
    summarize_retry_delta,
)
from silisocs.simulation_engines.recorders import probe_empty
from silisocs.simulation_engines.runtime_base import LoopStrategy


def _collect_agent_flows(game_masters: list[Any]) -> dict[str, str]:
    """Merge ``agent_flow_tags`` across all game masters (earliest GM wins)."""
    agent_flows: dict[str, str] = {}
    for game_master in game_masters:
        tags = getattr(game_master, "agent_flow_tags", None)
        if isinstance(tags, Mapping):
            for name, flow in tags.items():
                agent_flows.setdefault(str(name), str(flow))
    return agent_flows


def _collect_models(game_masters: list[Any], agents: list[Any]) -> list[Any]:
    """Unique model objects across every game master and agent (id-deduped)."""
    models: dict[int, Any] = {}
    for game_master in game_masters:
        for model in collect_unique_models(game_master, agents):
            models.setdefault(id(model), model)
    return list(models.values())


class FixedStepsLoopStrategy(LoopStrategy):
    """Default loop strategy: run steps from start_step up to max_steps."""

    name = "fixed_steps"

    def run(
        self,
        *,
        engine: Any,
        game_masters: list[Any],
        agents: list[Any],
        max_steps: int,
        start_step: int,
        verbose: bool,
        checkpoint_callback: Any | None,
    ) -> None:
        step = max(0, int(start_step))
        agent_flows = _collect_agent_flows(game_masters)
        models = _collect_models(game_masters, agents)
        interventions = getattr(engine, "interventions", None)
        if interventions:
            # Reconstruct persistent intervention state (participation/ban/recsys
            # swaps) that isn't in the checkpoint. No-op on a fresh run.
            interventions.replay_persistent(
                start_step=step, engine=engine, game_masters=game_masters, agents=agents
            )
        while step < int(max_steps):
            t0 = time.time()
            probe_phase = probe_empty(len(agents))
            if engine.probe_runner is not None:
                # Bracket probe execution the same way scheduling brackets the
                # action phase, so probe tokens/retries land in the "probe"
                # bucket (not the leftover "other" default).
                probe_start = time.time()
                before = capture_retry_counters(models)
                set_model_retry_phase(models, "probe")
                try:
                    deployed, selected = engine.probe_runner.maybe_run(
                        step=step,
                        agents=agents,
                        worker_limit=None,
                        agent_flows=agent_flows,
                    )
                finally:
                    set_model_retry_phase(models, "other")
                probe_phase["deployed"] = deployed
                probe_phase["selected_agents"] = selected
                probe_phase["duration_s"] = round(time.time() - probe_start, 4)
                probe_phase["retry"] = summarize_retry_delta(before, capture_retry_counters(models))
            if interventions:
                # After probes (which measure the pre-intervention world), before
                # the step. Single-threaded boundary — handlers mutate freely.
                interventions.apply_due(
                    step=step, engine=engine, game_masters=game_masters, agents=agents
                )
            step_result = engine.run_step(
                step_index=step,
                game_masters=game_masters,
                agents=agents,
                verbose=verbose,
            )
            step_result.probe_phase = probe_phase
            duration = time.time() - t0
            engine.recorder.record_episode(
                episode=step,
                duration_s=duration,
                total_agents=len(agents),
                step_result=step_result,
            )
            if checkpoint_callback is not None:
                checkpoint_callback(step + 1)
            if verbose:
                print(f"Episode {step} finished in {duration:.2f}s")
            step += 1
