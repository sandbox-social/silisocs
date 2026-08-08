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


def _probe_anchors_in_use(runner: Any) -> set[str]:
    """Return the loop anchors a probe runner uses (legacy runners: pre_step only)."""
    getter = getattr(runner, "anchors_in_use", None)
    return set(getter()) if callable(getter) else {"pre_step"}


def await_step_permission(engine: Any, step: int) -> bool:
    """Block until episode ``step`` is permitted to run; ``False`` means stop.

    A custom ``LoopStrategy`` owns interactive run control the same way it owns
    probe timing: call this at its episode boundary and ``break`` when it returns
    ``False``, and the strategy inherits play/pause/step/stop
    (``sim.engine.control``) for free. Returns ``True`` immediately when no gate
    is attached — the default for a non-interactive run — so a strategy that
    calls it costs nothing when control is off.
    """
    gate = getattr(engine, "step_gate", None)
    return True if gate is None else bool(gate.await_turn(step))


def emit_run_event(engine: Any, kind: str, **fields: Any) -> None:
    """Emit to the engine's attached run-event log; no-op when none is attached.

    Same seam pattern as :func:`await_step_permission`: the session attaches a
    ``run_event_log`` to the engine, and a custom ``LoopStrategy`` keeps live
    observers (Studio's watch view) working by calling this at its own step
    boundaries — ``step_started`` before an episode, ``step_finished`` after
    its checkpoint chance.
    """
    log = getattr(engine, "run_event_log", None)
    if log is not None:
        log.emit(kind, **fields)


def run_probe_phase(
    engine: Any,
    *,
    step: int,
    agents: list[Any],
    agent_flows: Mapping[str, str],
    models: list[Any],
    anchor: str,
) -> dict[str, Any]:
    """Deploy probes for one loop anchor with probe-bucketed retry telemetry.

    A custom ``LoopStrategy`` owns probe timing: call this at whatever loop
    boundaries it wants probes to fire (the default strategy uses
    pre_step/post_step/run_end); nothing else is required. Brackets the deploy so
    probe tokens/retries land in the "probe" telemetry bucket, and returns a
    probe_phase dict (empty if no runner is configured). A legacy runner without
    ``anchors_in_use`` is driven only at ``pre_step`` with the pre-anchor signature.
    """
    probe_phase = probe_empty(len(agents))
    runner = getattr(engine, "probe_runner", None)
    if runner is None:
        return probe_phase
    anchor_aware = callable(getattr(runner, "anchors_in_use", None))
    probe_start = time.time()
    before = capture_retry_counters(models)
    set_model_retry_phase(models, "probe")
    try:
        if anchor_aware:
            deployed, selected = runner.maybe_run(
                step=step,
                agents=agents,
                worker_limit=None,
                agent_flows=agent_flows,
                anchor=anchor,
            )
        else:
            deployed, selected = runner.maybe_run(
                step=step, agents=agents, worker_limit=None, agent_flows=agent_flows
            )
    finally:
        set_model_retry_phase(models, "other")
    probe_phase["deployed"] = deployed
    probe_phase["selected_agents"] = selected
    probe_phase["duration_s"] = round(time.time() - probe_start, 4)
    probe_phase["retry"] = summarize_retry_delta(before, capture_retry_counters(models))
    return probe_phase


def _merge_probe_phase(base: dict[str, Any], extra: dict[str, Any]) -> dict[str, Any]:
    """Fold a second anchor's probe telemetry into a step's probe_phase (in place)."""
    if not extra.get("deployed"):
        return base
    base["deployed"] = True
    base["selected_agents"] = int(base.get("selected_agents", 0)) + int(
        extra.get("selected_agents", 0)
    )
    base["duration_s"] = round(
        float(base.get("duration_s", 0.0)) + float(extra.get("duration_s", 0.0)), 4
    )
    base_retry = base.get("retry") or {}
    extra_retry = extra.get("retry") or {}
    merged = dict(base_retry)
    for key in ("calls", "failed_calls", "retries"):
        merged[key] = int(base_retry.get(key, 0)) + int(extra_retry.get(key, 0))
    merged["models_with_activity"] = max(
        int(base_retry.get("models_with_activity", 0)),
        int(extra_retry.get("models_with_activity", 0)),
    )
    calls = int(merged.get("calls", 0))
    merged["retry_per_call"] = round(merged["retries"] / calls, 4) if calls else 0.0
    merged["failure_ratio"] = round(merged["failed_calls"] / calls, 4) if calls else 0.0
    base["retry"] = merged
    return base


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
        probe_anchors = _probe_anchors_in_use(getattr(engine, "probe_runner", None))
        interventions = getattr(engine, "interventions", None)
        if interventions:
            # Reconstruct persistent intervention state (participation/ban/recsys
            # swaps) that isn't in the checkpoint. No-op on a fresh run.
            interventions.replay_persistent(
                start_step=step, engine=engine, game_masters=game_masters, agents=agents
            )
        executed_last_step: int | None = None
        while step < int(max_steps):
            # Interactive run control (no-op unless sim.engine.control attached a gate).
            if not await_step_permission(engine, step):
                break  # stop requested at the episode boundary
            emit_run_event(engine, "step_started", step=step)
            t0 = time.time()
            # pre_step: measure the pre-intervention world before the step runs.
            probe_phase = run_probe_phase(
                engine,
                step=step,
                agents=agents,
                agent_flows=agent_flows,
                models=models,
                anchor="pre_step",
            )
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
            if "post_step" in probe_anchors:
                # post_step: measure the world the step just produced.
                _merge_probe_phase(
                    probe_phase,
                    run_probe_phase(
                        engine,
                        step=step,
                        agents=agents,
                        agent_flows=agent_flows,
                        models=models,
                        anchor="post_step",
                    ),
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
            # After the checkpoint chance: an observer reacting to this row can
            # rely on the episode's checkpoint already being on disk.
            emit_run_event(engine, "step_finished", step=step)
            if verbose:
                print(f"Episode {step} finished in {duration:.2f}s")
            executed_last_step = step
            step += 1
        if "run_end" in probe_anchors and executed_last_step is not None:
            # run_end: one terminal measurement of the final world. Its probe rows
            # are logged (anchor=run_end, episode=last step); it is not folded into
            # per-episode sim_metrics telemetry (no step_result to attach to).
            run_probe_phase(
                engine,
                step=executed_last_step,
                agents=agents,
                agent_flows=agent_flows,
                models=models,
                anchor="run_end",
            )
