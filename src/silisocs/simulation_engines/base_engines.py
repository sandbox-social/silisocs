"""Native runtime engine and preset wrappers."""

from __future__ import annotations

import concurrent.futures
import functools
import logging
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from typing import Any, ClassVar, cast

from omegaconf import OmegaConf

from silisocs.agents.base_agent import Agent
from silisocs.runtime.telemetry import (
    SimMetricsCollector,
    capture_retry_counters,
    collect_retry_telemetry,
    collect_unique_models,
    compute_dynamic_worker_limit,
    resolve_configured_worker_cap,
    set_model_retry_phase,
    summarize_retry_delta,
    update_adaptive_worker_cap,
)
from silisocs.runtime.types import ActionOutput, ActionSpec
from silisocs.simulation_engines.policies.factory import (
    build_flow_turn_policies,
    build_turn_policy,
)
from silisocs.simulation_engines.policies.loops import FixedStepsLoopStrategy
from silisocs.simulation_engines.policies.steps import (
    BaseStepStrategy,
    FlowStepStrategy,
    MultiGMStepStrategy,
)
from silisocs.simulation_engines.recorders import DefaultEngineRecorder, probe_empty, retry_empty
from silisocs.simulation_engines.runtime_base import (
    AgentStepResult,
    EngineRecorder,
    LoopStrategy,
    ProbeRunner,
    RuntimeEngineBase,
    StepBatch,
    StepResult,
    StepStrategy,
    TurnPolicy,
)

_LOGGER = logging.getLogger(__name__)


def _ensure_gm_method(game_master: Any, method: str) -> Callable[..., Any]:
    fn = getattr(game_master, method, None)
    if not callable(fn):
        raise TypeError(f"Runtime game masters must expose {method}(...).")
    return fn


def _set_gm_episode_index(game_master: Any, step_index: int) -> None:
    backend = getattr(game_master, "backend", None)
    action_logger = getattr(backend, "action_logger", None)
    if action_logger is not None and hasattr(action_logger, "episode_idx"):
        action_logger.episode_idx = int(step_index)


def _gm_episode_index(game_master: Any) -> int:
    backend = getattr(game_master, "backend", None)
    action_logger = getattr(backend, "action_logger", None)
    raw = getattr(action_logger, "episode_idx", -1)
    try:
        return int(raw)
    except (TypeError, ValueError):
        return -1


class RuntimeEngine(RuntimeEngineBase):
    """Strategy-driven runtime engine."""

    def __init__(
        self,
        *,
        config: Any | None = None,
        loop_strategy: LoopStrategy | None = None,
        step_strategy: StepStrategy | None = None,
        turn_policy: TurnPolicy | None = None,
        gm_turn_policies: Mapping[str, TurnPolicy] | None = None,
        probe_runner: ProbeRunner | None = None,
        recorder: EngineRecorder | None = None,
    ) -> None:
        self.config = config
        self.loop_strategy = loop_strategy or FixedStepsLoopStrategy()
        self.step_strategy = step_strategy or BaseStepStrategy()
        self.turn_policy = turn_policy or build_turn_policy(
            {"built_in": "single_action", "params": {}}
        )
        # Per-GM turn policies (keyed by GM name) let a backend set its own action
        # cadence; consulted in _batch_tasks below per-flow override but above global.
        self.gm_turn_policies: dict[str, TurnPolicy] = dict(gm_turn_policies or {})
        self.probe_runner = probe_runner
        output_rootname = ""
        if config is not None:
            output_rootname = str(getattr(config, "output_rootname", "") or "")
        self.recorder = recorder or DefaultEngineRecorder(output_rootname=output_rootname)
        self._configured_worker_cap = (
            resolve_configured_worker_cap(config) if config is not None else None
        )
        self._action_phase_cap: int | None = None
        self._gm_locks: dict[int, threading.Lock] = {}
        self._gm_locks_guard = threading.Lock()
        self._initialization_context: Any | None = None

    def _gm_lock(self, game_master: Any) -> threading.Lock:
        key = id(game_master)
        with self._gm_locks_guard:
            lock = self._gm_locks.get(key)
            if lock is None:
                lock = threading.Lock()
                self._gm_locks[key] = lock
        return lock

    def initialize(
        self,
        *,
        agents: list[Any],
        game_masters: list[Any],
        agent_initializer: Any | None,
        game_master_initializer: Any | None,
        simulation_initializer: Any | None,
        initialization_context: Any | None,
        initializer_model: Any | None,
    ) -> None:
        self._initialization_context = initialization_context
        if agent_initializer is not None:
            agent_initializer.initialize(
                agents=agents,
                model=initializer_model,
                context=initialization_context,
            )
        if game_master_initializer is not None:
            game_master_initializer.initialize(
                agents=agents,
                game_masters=game_masters,
                context=initialization_context,
            )
        if simulation_initializer is not None:
            simulation_initializer.initialize(
                agents=agents,
                game_masters=game_masters,
                model=initializer_model,
                context=initialization_context,
            )

    def run_agent_step(
        self,
        *,
        game_master: Any,
        agent: Any,
        action_spec: ActionSpec,
        verbose: bool,
        observe_before_action: bool = True,
    ) -> AgentStepResult:
        del verbose
        episode_idx = _gm_episode_index(game_master)
        model = getattr(agent, "model", None)
        set_ctx = getattr(model, "set_runtime_context", None)
        clear_ctx = getattr(model, "clear_runtime_context", None)
        if callable(set_ctx):
            set_ctx(
                agent_name=agent.name,
                episode_idx=episode_idx,
                phase="action",
                action_tag=action_spec.tag,
            )
        try:
            if observe_before_action:
                with self._gm_lock(game_master):
                    observe_fn = _ensure_gm_method(game_master, "make_observation")
                    observation = str(observe_fn(agent.name))
                if observation.strip():
                    agent.observe(observation)

            raw_action = agent.act(action_spec)
            action_output = (
                raw_action
                if isinstance(raw_action, ActionOutput)
                else ActionOutput.from_text(str(raw_action or ""))
            )
            rendered = str(action_output)
            with self._gm_lock(game_master):
                resolve_fn = _ensure_gm_method(game_master, "resolve_action")
                resolved = str(resolve_fn(agent.name, action_output))
            if resolved.strip():
                agent.observe(resolved)
            return AgentStepResult(
                agent_name=agent.name,
                rendered_action=rendered,
                raw_action=action_output,
                resolved_result=resolved,
            )
        finally:
            if callable(clear_ctx):
                clear_ctx()

    def _agent_flow_tag(self, game_master: Any, agent_name: str) -> str:
        flow_map = dict(getattr(game_master, "agent_flow_tags", {}) or {})
        return str(flow_map.get(agent_name, "default") or "default")

    def _selected_turns(
        self,
        *,
        game_master: Any,
        candidate_agents: Sequence[Agent],
    ) -> list[tuple[Agent, ActionSpec]]:
        by_name = {agent.name: agent for agent in candidate_agents}
        acting_fn = _ensure_gm_method(game_master, "acting_agents")
        prompt_fn = _ensure_gm_method(game_master, "action_prompt")
        selected: list[tuple[Agent, ActionSpec]] = []
        for raw_name in acting_fn(candidate_agents):
            name = str(raw_name).strip()
            if name not in by_name:
                _LOGGER.warning(
                    "Ignoring unknown acting agent '%s' from game master '%s'.",
                    name,
                    getattr(game_master, "name", "<unknown>"),
                )
                continue
            selected.append((by_name[name], cast(ActionSpec, prompt_fn(name))))
        return selected

    @staticmethod
    def _run_tasks_with_limit(
        tasks: Mapping[str, Callable[[], str]],
        worker_limit: int,
        failed_tasks: list[str] | None = None,
    ) -> dict[str, str]:
        if not tasks:
            return {}
        with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, worker_limit)) as executor:
            future_to_name = {
                executor.submit(task_fn): task_name for task_name, task_fn in tasks.items()
            }
            results: dict[str, str] = {}
            for future in concurrent.futures.as_completed(future_to_name):
                task_name = future_to_name[future]
                try:
                    results[task_name] = str(future.result() or "")
                except Exception:
                    _LOGGER.exception("Agent turn failed (isolated): %s", task_name)
                    SimMetricsCollector.get().increment_counter("agent_turn_failures")
                    if failed_tasks is not None:
                        failed_tasks.append(task_name)
                    results[task_name] = ""
            return results

    @staticmethod
    def _empty_step_result() -> StepResult:
        return StepResult(
            skipped=True,
            primary_game_master="",
            probe_phase=probe_empty(0),
            action_phase={"active_agents": 0, "duration_s": 0.0, "retry": retry_empty()},
        )

    def _batch_tasks(
        self, batch: StepBatch
    ) -> tuple[dict[str, Callable[[], str]], list[str], list[Any]]:
        """Build the {task_name: thunk} map, acting names, and models for one batch."""
        # Precedence: per-flow override (set by the step strategy) > per-GM default
        # (keyed by GM name, lets a backend dictate its own action cadence) > global.
        batch_policy = (
            batch.turn_policy
            or self.gm_turn_policies.get(batch.game_master.name)
            or self.turn_policy
        )
        tasks: dict[str, Callable[[], str]] = {}
        names: list[str] = []
        for agent, spec in batch.turns:
            names.append(agent.name)
            task_name = f"{batch.game_master.name}::{agent.name}"
            tasks[task_name] = functools.partial(
                batch_policy.run,
                engine=self,
                game_master=batch.game_master,
                agent=agent,
                action_spec=spec,
                verbose=False,
            )
        models = list(collect_unique_models(batch.game_master, [agent for agent, _ in batch.turns]))
        return tasks, names, models

    def _run_action_phase(
        self,
        *,
        requested_workers: int,
        active_names: set[str],
        models: list[Any],
        primary_gm_name: str,
        runner: Callable[[int, list[str]], None],
    ) -> StepResult:
        """Wrap an action-phase ``runner`` with the once-per-step telemetry envelope.

        ``runner(worker_limit, failed_tasks)`` executes the turns however it likes
        (serial batches or the concurrent chain scheduler); worker-limit sizing,
        retry-phase bracketing, the adaptive cap update, and StepResult assembly are
        identical regardless of how the turns were scheduled.
        """
        dynamic_cap, worker_limit = compute_dynamic_worker_limit(
            requested_workers=requested_workers,
            phase_cap=self._action_phase_cap,
            configured_worker_cap=self._configured_worker_cap,
        )
        before = capture_retry_counters(models)
        set_model_retry_phase(models, "action")
        action_start = time.time()
        failed_tasks: list[str] = []
        try:
            runner(worker_limit, failed_tasks)
        finally:
            set_model_retry_phase(models, "other")
        action_duration = time.time() - action_start
        after = capture_retry_counters(models)
        retry_delta = summarize_retry_delta(before, after)
        self._action_phase_cap = update_adaptive_worker_cap(
            previous_cap=self._action_phase_cap,
            requested_workers=requested_workers,
            calls=retry_delta["calls"],
            retry_per_call=retry_delta["retry_per_call"],
            failure_ratio=retry_delta["failure_ratio"],
        )
        retry_telemetry = collect_retry_telemetry(models, requested_workers, phase="action")
        return StepResult(
            active_agent_names=tuple(sorted(active_names)),
            skipped=not bool(active_names),
            requested_workers=requested_workers,
            worker_limit=worker_limit,
            dynamic_worker_cap=dynamic_cap,
            configured_worker_cap=self._configured_worker_cap,
            phase_timings={"step_action": action_duration},
            retry_telemetry=retry_telemetry,
            action_phase={
                "active_agents": len(active_names),
                "duration_s": round(action_duration, 4),
                "retry": retry_delta,
                "failed_turns": len(failed_tasks),
            },
            probe_phase=probe_empty(len(active_names)),
            primary_game_master=primary_gm_name,
            # Sorted so the failure set is deterministic across concurrent driver
            # threads, mirroring the sorted active_agent_names above.
            failed_turns=tuple(sorted(failed_tasks)),
        )

    def _execute_batches(
        self,
        *,
        step_index: int,
        batches: Sequence[StepBatch],
        verbose: bool,
    ) -> StepResult:
        """Run a flat batch list strictly serially (one batch fully drained at a time).

        This is the legacy execution path used by the base/sequential/flow step
        strategies and by multi-GM ``chain_execution: sequential``.
        """
        del verbose, step_index
        if not batches:
            return self._empty_step_result()

        task_groups: list[dict[str, Callable[[], str]]] = []
        model_pool: dict[int, Any] = {}
        active_names: set[str] = set()
        requested_workers = 0
        for batch in batches:
            tasks, names, models = self._batch_tasks(batch)
            task_groups.append(tasks)
            active_names.update(names)
            requested_workers += len(tasks)
            for model_obj in models:
                model_pool[id(model_obj)] = model_obj

        def runner(worker_limit: int, failed_tasks: list[str]) -> None:
            for tasks in task_groups:
                self._run_tasks_with_limit(tasks, worker_limit, failed_tasks=failed_tasks)

        return self._run_action_phase(
            requested_workers=max(1, requested_workers),
            active_names=active_names,
            models=list(model_pool.values()),
            primary_gm_name=batches[0].game_master.name,
            runner=runner,
        )

    @staticmethod
    def _drain_tasks_on_pool(
        pool: concurrent.futures.ThreadPoolExecutor,
        tasks: dict[str, Callable[[], str]],
        failed_tasks: list[str],
    ) -> None:
        """Submit one group of turn thunks to a shared pool and wait for completion.

        Mirrors ``_run_tasks_with_limit``'s per-turn failure isolation (a raising
        turn is logged, counted, and recorded in ``failed_tasks``; it never aborts
        siblings) but reuses an externally owned executor so independent chains share
        a single worker budget. CPython's GIL makes the ``failed_tasks.append`` and
        the metrics counter increment safe across concurrent driver threads.
        """
        if not tasks:
            return
        future_to_name = {pool.submit(task_fn): task_name for task_name, task_fn in tasks.items()}
        for future in concurrent.futures.as_completed(future_to_name):
            task_name = future_to_name[future]
            try:
                future.result()
            except Exception:
                _LOGGER.exception("Agent turn failed (isolated): %s", task_name)
                SimMetricsCollector.get().increment_counter("agent_turn_failures")
                failed_tasks.append(task_name)

    def _run_chain_pipeline(
        self,
        pool: concurrent.futures.ThreadPoolExecutor,
        hop_task_groups: list[dict[str, Callable[[], str]]],
        failed_tasks: list[str],
    ) -> None:
        """Drive one flow's GM chain: drain each hop in order before the next.

        Runs on a driver thread (not a turn-pool worker), so blocking on a hop's
        futures keeps that flow's hops serial — preserving per-agent chain order —
        without consuming a turn-worker slot or stalling sibling chains. A failed
        turn at one hop is isolated and counted but does not abort later hops (same
        as the legacy serial path); a downstream hop may then observe a GM the failed
        hop never resolved into.
        """
        for tasks in hop_task_groups:
            self._drain_tasks_on_pool(pool, tasks, failed_tasks)

    def _execute_chain_groups(
        self,
        *,
        ordered_batches: Sequence[StepBatch],
        concurrent_chains: Sequence[Sequence[StepBatch]],
    ) -> StepResult:
        """Concurrent multi-GM execution.

        ``ordered_batches`` (flow_order-listed flows) run first as a strict serial
        prefix, preserving declared cross-flow precedence (e.g. seed-then-act). The
        remaining ``concurrent_chains`` — one per flow, each a GM-chain of hop
        batches — then run as independent pipelines. Turns execute on one shared,
        worker-limit-bounded turn pool (different GMs proceed in parallel; turns on
        the same GM serialize on the per-GM lock in ``run_agent_step``). The chain
        drivers run on a SEPARATE pool capped at ``worker_limit`` so the live driver
        thread count stays bounded regardless of how many flows there are — the turn
        pool is the throughput bottleneck, so capping drivers is throughput-neutral.
        """
        active_names: set[str] = set()
        model_pool: dict[int, Any] = {}
        requested_workers = 0
        primary_gm_name = ""

        def prep(batch: StepBatch) -> dict[str, Callable[[], str]]:
            nonlocal requested_workers, primary_gm_name
            tasks, names, models = self._batch_tasks(batch)
            active_names.update(names)
            requested_workers += len(tasks)
            for model_obj in models:
                model_pool[id(model_obj)] = model_obj
            if not primary_gm_name:
                primary_gm_name = batch.game_master.name
            return tasks

        ordered_groups = [prep(batch) for batch in ordered_batches]
        concurrent_groups = [[prep(batch) for batch in chain] for chain in concurrent_chains]
        active_chains = [groups for groups in concurrent_groups if groups]

        # Mirror _execute_batches: short-circuit only when there are genuinely no
        # batches, not merely no selected turns, so an all-inactive step still emits
        # the full telemetry envelope identically to the serial path.
        if not primary_gm_name:
            return self._empty_step_result()

        def runner(worker_limit: int, failed_tasks: list[str]) -> None:
            with concurrent.futures.ThreadPoolExecutor(
                max_workers=max(1, worker_limit)
            ) as turn_pool:
                for tasks in ordered_groups:
                    self._drain_tasks_on_pool(turn_pool, tasks, failed_tasks)
                if not active_chains:
                    return
                driver_count = min(max(1, worker_limit), len(active_chains))
                with concurrent.futures.ThreadPoolExecutor(max_workers=driver_count) as driver_pool:
                    driver_futures = [
                        driver_pool.submit(
                            self._run_chain_pipeline, turn_pool, groups, failed_tasks
                        )
                        for groups in active_chains
                    ]
                    for future in concurrent.futures.as_completed(driver_futures):
                        # Per-turn failures are already isolated inside the driver;
                        # this only surfaces an unexpected driver-level error (e.g. a
                        # pool/scheduling fault) so it is logged rather than swallowed.
                        exc = future.exception()
                        if exc is not None:
                            _LOGGER.error("Chain pipeline driver failed", exc_info=exc)

        return self._run_action_phase(
            requested_workers=max(1, requested_workers),
            active_names=active_names,
            models=list(model_pool.values()),
            primary_gm_name=primary_gm_name,
            runner=runner,
        )

    def run_step(
        self,
        *,
        step_index: int,
        game_masters: list[Any],
        agents: list[Any],
        verbose: bool,
    ) -> StepResult:
        for game_master in game_masters:
            _set_gm_episode_index(game_master, step_index)
        for game_master in game_masters:
            update_fn = _ensure_gm_method(game_master, "update")
            with self._gm_lock(game_master):
                update_fn(step=step_index, agents=agents, context=self._initialization_context)
        return self.step_strategy.run(
            engine=self,
            step_index=step_index,
            game_masters=game_masters,
            agents=agents,
            verbose=verbose,
        )

    def run_loop(
        self,
        *,
        game_masters: list[Any],
        agents: list[Any],
        max_steps: int,
        start_step: int,
        verbose: bool,
        checkpoint_callback: Any | None,
    ) -> None:
        self.loop_strategy.run(
            engine=self,
            game_masters=game_masters,
            agents=agents,
            max_steps=max_steps,
            start_step=start_step,
            verbose=verbose,
            checkpoint_callback=checkpoint_callback,
        )


def _extract_flow_order(cfg: Any | None) -> tuple[str, ...]:
    """Read sim.engine.step.params.flow_order from config with sensible default."""
    default = ["fixed_pre", "default"]
    raw = (
        OmegaConf.select(cfg, "sim.engine.step.params.flow_order", default=default)
        if cfg is not None
        else default
    )
    return tuple(str(item) for item in (raw or default))


def _extract_flow_turn_policies(cfg: Any | None) -> dict[str, Any]:
    """Resolve per-flow turn policies from sim.engine.step.params.flow_turn_policies.

    Returns an empty map when unset, so the engine's single global turn policy
    applies to every flow (current behavior).
    """
    if cfg is None:
        return {}
    raw = OmegaConf.select(cfg, "sim.engine.step.params.flow_turn_policies", default=None)
    return build_flow_turn_policies(raw)


def _extract_gm_turn_policies(cfg: Any | None) -> dict[str, Any]:
    """Resolve per-GM turn policies from sim.engine.step.params.gm_turn_policies.

    A ``{gm_name: turn_policy_slot}`` map (same slot shape as flow_turn_policies),
    letting a backend/GM set its own action cadence. Empty when unset, so the global
    turn policy applies everywhere (current behavior).
    """
    if cfg is None:
        return {}
    raw = OmegaConf.select(cfg, "sim.engine.step.params.gm_turn_policies", default=None)
    return build_flow_turn_policies(raw)


def _extract_chain_execution(cfg: Any | None) -> str:
    """Read sim.engine.step.params.chain_execution (multi-GM chain scheduling mode).

    ``concurrent`` (default) runs flow chains as independent pipelines gated only by
    shared-GM overlap; ``sequential`` reproduces the legacy strictly-serial,
    flow-by-flow execution. Only the multi-GM step strategy consults this.
    """
    default = "concurrent"
    if cfg is None:
        return default
    raw = OmegaConf.select(cfg, "sim.engine.step.params.chain_execution", default=default)
    return str(raw or default).strip().lower()


def _apply_engine_defaults(kwargs: dict[str, Any], step_strategy: Any) -> None:
    """Set shared defaults (loop strategy, step strategy, turn policy) on kwargs."""
    cfg = kwargs.get("config")
    kwargs.setdefault("loop_strategy", FixedStepsLoopStrategy())
    kwargs.setdefault("step_strategy", step_strategy)
    kwargs.setdefault("turn_policy", build_turn_policy(_engine_turn_policy_cfg(cfg)))
    kwargs.setdefault("gm_turn_policies", _extract_gm_turn_policies(cfg))


class BaseRuntimeEngine(RuntimeEngine):
    """Runtime engine preset wrapper using base step strategy."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        _apply_engine_defaults(kwargs, BaseStepStrategy())
        super().__init__(*args, **kwargs)


class FlowRuntimeEngine(RuntimeEngine):
    """Runtime engine preset wrapper using a flow-ordered step strategy.

    Subclasses select their step strategy via ``step_strategy_class``; the flow
    order is read from config and passed to it.
    """

    step_strategy_class: ClassVar[Callable[..., StepStrategy]] = FlowStepStrategy

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        cfg = kwargs.get("config")
        flow_order = _extract_flow_order(cfg)
        flow_turn_policies = _extract_flow_turn_policies(cfg)
        chain_execution = _extract_chain_execution(cfg)
        _apply_engine_defaults(
            kwargs,
            self.step_strategy_class(
                flow_order=flow_order,
                flow_turn_policies=flow_turn_policies,
                chain_execution=chain_execution,
            ),
        )
        super().__init__(*args, **kwargs)


class MultiGMRuntimeEngine(FlowRuntimeEngine):
    """Runtime engine preset wrapper using the flow-first multi-GM step strategy."""

    step_strategy_class: ClassVar[Callable[..., StepStrategy]] = MultiGMStepStrategy


def _engine_turn_policy_cfg(cfg: Any | None) -> Mapping[str, Any] | None:
    if cfg is None:
        return {"built_in": "single_action", "params": {}}
    slot = OmegaConf.select(cfg, "sim.engine.turn_policy", default=None)
    if slot is None:
        return {"built_in": "single_action", "params": {}}
    return cast(Mapping[str, Any], OmegaConf.to_container(slot, resolve=True))
