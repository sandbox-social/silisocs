"""Native strategy-driven runtime engine."""

from __future__ import annotations

import concurrent.futures
import functools
import logging
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from typing import Any, cast

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
from silisocs.simulation_engines.policies.factory import build_turn_policy
from silisocs.simulation_engines.policies.loops import FixedStepsLoopStrategy
from silisocs.simulation_engines.policies.steps import BaseStepStrategy
from silisocs.simulation_engines.recorders import DefaultEngineRecorder, probe_empty, retry_empty
from silisocs.simulation_engines.runtime_base import (
    AgentStepResult,
    BranchHop,
    EngineRecorder,
    LoopStrategy,
    ProbeRunner,
    RuntimeEngineBase,
    StepBatch,
    StepResult,
    StepStrategy,
    TurnPolicy,
    expand_hop,
)

_LOGGER = logging.getLogger(__name__)

# One prepped agent batch (a GM and its {task_name: thunk} map) and one chain stage
# (the groups that drain together — a single group for a normal hop, several for a
# resolved branch). Used by the multi-GM grouped/staged execution paths.
_Group = tuple[Any, dict[str, Callable[[], str]]]
_Stage = list[_Group]


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


def _record_isolated_failure(task_name: str, failed_tasks: list[str] | None) -> None:
    """Log, count, and record one isolated turn failure (shared by every drain loop).

    Call only from inside an ``except`` block — ``logger.exception`` reads the live
    exception context. CPython's GIL keeps the metrics increment and the
    ``failed_tasks.append`` safe across concurrent driver threads.
    """
    _LOGGER.exception("Agent turn failed (isolated): %s", task_name)
    SimMetricsCollector.get().increment_counter("agent_turn_failures")
    if failed_tasks is not None:
        failed_tasks.append(task_name)


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
        gm_concurrency_caps: Mapping[str, int] | None = None,
        participation: Any | None = None,
        seed: int = 0,
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
        # Per-GM concurrency caps (keyed by GM name): a per-GM BoundedSemaphore caps
        # how many of that GM's turns run concurrently; empty = global cap only.
        self.gm_concurrency_caps: dict[str, int] = dict(gm_concurrency_caps or {})
        # Sim-level participation policy: filters each step's agent roster before
        # any scheduling or GM next_acting runs. None = every agent participates.
        self.participation = participation
        self.seed = int(seed)
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
        self._gm_sems: dict[int, threading.BoundedSemaphore] = {}
        self._gm_sems_guard = threading.Lock()
        self._initialization_context: Any | None = None

    def _gm_lock(self, game_master: Any) -> threading.Lock:
        key = id(game_master)
        with self._gm_locks_guard:
            lock = self._gm_locks.get(key)
            if lock is None:
                lock = threading.Lock()
                self._gm_locks[key] = lock
        return lock

    def _reset_gm_semaphores(self) -> None:
        """Drop cached per-GM semaphores so they re-size against the active phase cap."""
        with self._gm_sems_guard:
            self._gm_sems.clear()

    def _gm_semaphore(
        self, game_master: Any, worker_limit: int
    ) -> threading.BoundedSemaphore | None:
        """Return this GM's concurrency semaphore, or None when uncapped.

        The permit count is the effective per-GM cap: min(configured cap, the phase
        worker_limit), so a GM never gets more concurrency than the global ceiling.
        """
        name = str(getattr(game_master, "name", "") or "")
        cap = self.gm_concurrency_caps.get(name)
        if cap is None:
            return None
        key = id(game_master)
        with self._gm_sems_guard:
            sem = self._gm_sems.get(key)
            if sem is None:
                permits = max(1, min(int(cap), max(1, worker_limit)))
                sem = threading.BoundedSemaphore(permits)
                self._gm_sems[key] = sem
        return sem

    def _wrap_turn(
        self, game_master: Any, worker_limit: int, thunk: Callable[[], str]
    ) -> Callable[[], str]:
        """Wrap a turn thunk so it holds one per-GM permit for its whole duration.

        Single acquire/release seam for the per-GM cap. The permit wraps the WHOLE
        turn (so a multi-action turn holds one permit). Invariant: acquire the per-GM
        semaphore BEFORE the per-GM lock — permit-holders only take the brief per-GM
        lock afterward and always make progress, so there is no pool-starvation
        deadlock.
        """
        if not self.gm_concurrency_caps:
            return thunk
        sem = self._gm_semaphore(game_master, worker_limit)
        if sem is None:
            return thunk

        def _gated() -> str:
            with sem:
                return thunk()

        return _gated

    def _wrap_group(
        self, game_master: Any, worker_limit: int, tasks: dict[str, Callable[[], str]]
    ) -> dict[str, Callable[[], str]]:
        """Wrap every thunk in a batch with the per-GM cap, gating on ``game_master``.

        Returns the same dict unchanged when no caps are configured, so the empty-map
        path stays byte-identical (the static drain helpers see the original thunks).
        """
        if not self.gm_concurrency_caps:
            return tasks
        return {
            name: self._wrap_turn(game_master, worker_limit, thunk) for name, thunk in tasks.items()
        }

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
    ) -> None:
        # Turn results are consumed via side effects (resolve_action), not returned;
        # the caller only needs completion + per-turn failure isolation.
        if not tasks:
            return
        with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, worker_limit)) as executor:
            future_to_name = {
                executor.submit(task_fn): task_name for task_name, task_fn in tasks.items()
            }
            for future in concurrent.futures.as_completed(future_to_name):
                task_name = future_to_name[future]
                try:
                    future.result()
                except Exception:
                    _record_isolated_failure(task_name, failed_tasks)

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
        # Size per-GM semaphores against THIS phase's worker_limit (reset before and
        # after so a later phase with a different limit re-creates them). Skipped
        # entirely when no caps are configured, so the default path is untouched.
        if self.gm_concurrency_caps:
            self._reset_gm_semaphores()
        try:
            runner(worker_limit, failed_tasks)
        finally:
            set_model_retry_phase(models, "other")
            if self.gm_concurrency_caps:
                self._reset_gm_semaphores()
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
        strategies and by the ``multi_gm_serial`` traversal.
        """
        del verbose, step_index
        if not batches:
            return self._empty_step_result()

        task_groups: list[tuple[Any, dict[str, Callable[[], str]]]] = []
        model_pool: dict[int, Any] = {}
        active_names: set[str] = set()
        requested_workers = 0
        for batch in batches:
            tasks, names, models = self._batch_tasks(batch)
            task_groups.append((batch.game_master, tasks))
            active_names.update(names)
            # Batches run strictly one at a time here, so peak concurrency is the
            # widest single batch, not the sum (matches the grouped paths' sizing).
            requested_workers = max(requested_workers, len(tasks))
            for model_obj in models:
                model_pool[id(model_obj)] = model_obj

        def runner(worker_limit: int, failed_tasks: list[str]) -> None:
            for game_master, tasks in task_groups:
                self._run_tasks_with_limit(
                    self._wrap_group(game_master, worker_limit, tasks),
                    worker_limit,
                    failed_tasks=failed_tasks,
                )

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
                _record_isolated_failure(task_name, failed_tasks)

    def _run_chain_pipeline(
        self,
        pool: concurrent.futures.ThreadPoolExecutor,
        stages: list[_Stage],
        failed_tasks: list[str],
        worker_limit: int,
    ) -> None:
        """Drive one flow's GM chain: drain each stage in order before the next.

        A normal hop is a one-group stage; a resolved branch is a multi-group stage
        whose groups (distinct agent subsets on different GMs) drain together as one
        barrier before the chain advances. Runs on a driver thread (not a turn-pool
        worker), so blocking on a stage's futures keeps that flow's stages serial —
        preserving per-agent chain order — without consuming a turn-worker slot or
        stalling sibling chains. A failed turn at one stage is isolated and counted but
        does not abort later stages (same as the legacy serial path); a downstream stage
        may then observe a GM the failed stage never resolved into.
        """
        for stage in stages:
            stage_tasks: dict[str, Callable[[], str]] = {}
            for game_master, tasks in stage:
                stage_tasks.update(self._wrap_group(game_master, worker_limit, tasks))
            self._drain_tasks_on_pool(pool, stage_tasks, failed_tasks)

    def _run_grouped_action_phase(
        self,
        *,
        ordered_batches: Sequence[StepBatch],
        rest_chains: Sequence[Sequence[StepBatch | BranchHop | None]],
        build_runner: Callable[..., Callable[[int, list[str]], None]],
    ) -> StepResult:
        """Shared prep + telemetry envelope for the concurrent and staged multi-GM paths.

        ``ordered_batches`` is the flow_order serial prefix; ``rest_chains`` is the
        remaining flows' hop lists IN FLOW ORDER. A hop is a normal ``StepBatch``,
        ``None`` (an idle slot), or a ``BranchHop`` (a resolved branch); each hop is
        prepped into a *stage* — a list of ``(game_master, tasks)`` groups (empty for an
        idle slot, one group for a normal hop, several for a branch). Stages are prepped
        in flow order, so ``primary_game_master`` and the rest of the telemetry envelope
        are identical across both paths regardless of how the caller later schedules
        them. ``build_runner(ordered_groups, rest_groups)`` receives the flattened prefix
        groups and the per-flow list of stages, and returns the action-phase runner.
        """
        active_names: set[str] = set()
        model_pool: dict[int, Any] = {}
        primary_gm_name = ""

        def prep_stage(hop: StepBatch | BranchHop | None) -> _Stage:
            nonlocal primary_gm_name
            stage: _Stage = []
            for batch in expand_hop(hop):
                tasks, names, models = self._batch_tasks(batch)
                active_names.update(names)
                for model_obj in models:
                    model_pool[id(model_obj)] = model_obj
                if not primary_gm_name:
                    primary_gm_name = batch.game_master.name
                stage.append((batch.game_master, tasks))
            return stage

        ordered_groups = [group for hop in ordered_batches for group in prep_stage(hop)]
        rest_groups = [[prep_stage(hop) for hop in chain] for chain in rest_chains]

        # Short-circuit only when there are genuinely no batches, not merely no selected
        # turns, so an all-inactive step still emits the full telemetry envelope.
        if not primary_gm_name:
            return self._empty_step_result()

        # Size the pool to PEAK concurrent turns, not the sum across sequential work: the
        # flow_order prefix runs before the rest (they never overlap), and within a flow
        # chain only one stage runs at a time. The widest single wave is the prefix's widest
        # group vs. the sum over flows of each flow's widest stage (a branch's groups drain
        # together, so a stage's width is the total turns across its groups); that sum also
        # upper-bounds the staged path's widest stage column, so one formula sizes both
        # paths without ever undersizing.
        prefix_peak = max((len(tasks) for _, tasks in ordered_groups), default=0)
        rest_peak = sum(
            max((sum(len(tasks) for _, tasks in stage) for stage in chain), default=0)
            for chain in rest_groups
        )

        return self._run_action_phase(
            requested_workers=max(1, prefix_peak, rest_peak),
            active_names=active_names,
            models=list(model_pool.values()),
            primary_gm_name=primary_gm_name,
            runner=build_runner(ordered_groups, rest_groups),
        )

    def _drain_prefix(
        self,
        turn_pool: concurrent.futures.ThreadPoolExecutor,
        ordered_groups: list[tuple[Any, dict[str, Callable[[], str]]]],
        worker_limit: int,
        failed_tasks: list[str],
    ) -> None:
        """Drain the flow_order serial prefix on the shared turn pool, one group at a
        time, before the per-strategy traversal begins (shared by both paths).
        """
        for game_master, tasks in ordered_groups:
            self._drain_tasks_on_pool(
                turn_pool, self._wrap_group(game_master, worker_limit, tasks), failed_tasks
            )

    def _execute_chain_groups(
        self,
        *,
        ordered_batches: Sequence[StepBatch],
        rest_chains: Sequence[Sequence[StepBatch | BranchHop | None]],
    ) -> StepResult:
        """Concurrent multi-GM execution.

        ``ordered_batches`` (flow_order-listed flows) run first as a strict serial
        prefix, preserving declared cross-flow precedence (e.g. seed-then-act). Each
        remaining flow's chain (idle ``None`` slots dropped) then runs as an
        independent pipeline. Turns execute on one shared, worker-limit-bounded turn
        pool (different GMs proceed in parallel; turns on the same GM serialize on the
        per-GM lock in ``run_agent_step``). The chain drivers run on a SEPARATE pool
        capped at ``worker_limit`` so the live driver thread count stays bounded
        regardless of how many flows there are — the turn pool is the throughput
        bottleneck, so capping drivers is throughput-neutral.
        """

        def build_runner(
            ordered_groups: list[_Group],
            rest_groups: list[list[_Stage]],
        ) -> Callable[[int, list[str]], None]:
            active_chains = [[stage for stage in chain if stage] for chain in rest_groups]
            active_chains = [chain for chain in active_chains if chain]

            def runner(worker_limit: int, failed_tasks: list[str]) -> None:
                with concurrent.futures.ThreadPoolExecutor(
                    max_workers=max(1, worker_limit)
                ) as turn_pool:
                    self._drain_prefix(turn_pool, ordered_groups, worker_limit, failed_tasks)
                    if not active_chains:
                        return
                    driver_count = min(max(1, worker_limit), len(active_chains))
                    with concurrent.futures.ThreadPoolExecutor(
                        max_workers=driver_count
                    ) as driver_pool:
                        driver_futures = [
                            driver_pool.submit(
                                self._run_chain_pipeline,
                                turn_pool,
                                groups,
                                failed_tasks,
                                worker_limit,
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

            return runner

        return self._run_grouped_action_phase(
            ordered_batches=ordered_batches, rest_chains=rest_chains, build_runner=build_runner
        )

    def _execute_staged_groups(
        self,
        *,
        ordered_batches: Sequence[StepBatch],
        rest_chains: Sequence[Sequence[StepBatch | BranchHop | None]],
    ) -> StepResult:
        """Staged multi-GM execution with a global per-stage barrier.

        ``ordered_batches`` (flow_order-listed flows) run first as a strict serial
        prefix, exactly as in ``_execute_chain_groups``. The remaining flows' per-flow
        hop lists are then transposed into stage columns and advanced one column at a
        time: every hop in a stage is submitted to the shared turn pool together
        (different GMs run in parallel; turns on the same GM serialize on the per-GM
        lock in ``run_agent_step``), and the next stage does not start until ALL of the
        current stage's turns finish — the global barrier. Unlike the concurrent path
        there is no driver pool: the engine sequences the stages directly, so each
        stage is one flat concurrent batch drained to completion. A flow that idles
        (``None``) or ends at a stage simply contributes no hop to that column.
        """

        def build_runner(
            ordered_groups: list[_Group],
            rest_groups: list[list[_Stage]],
        ) -> Callable[[int, list[str]], None]:
            # Transpose per-flow stages into stage columns; idle slots (empty stages)
            # contribute nothing, and a branch stage flattens its groups into the column.
            depth = max((len(chain) for chain in rest_groups), default=0)
            stages: list[_Stage] = []
            for stage_index in range(depth):
                column: _Stage = [
                    group
                    for chain in rest_groups
                    if stage_index < len(chain)
                    for group in chain[stage_index]
                ]
                if column:
                    stages.append(column)

            def runner(worker_limit: int, failed_tasks: list[str]) -> None:
                with concurrent.futures.ThreadPoolExecutor(
                    max_workers=max(1, worker_limit)
                ) as turn_pool:
                    self._drain_prefix(turn_pool, ordered_groups, worker_limit, failed_tasks)
                    for groups in stages:
                        # One global barrier per stage: gather every hop in the column
                        # into a single submission so all GMs in the stage run together,
                        # then block on the whole column before advancing. Task names
                        # are f"{gm}::{agent}" and each agent is in exactly one flow, so
                        # a column's keys never collide.
                        column_tasks: dict[str, Callable[[], str]] = {}
                        for game_master, tasks in groups:
                            column_tasks.update(self._wrap_group(game_master, worker_limit, tasks))
                        self._drain_tasks_on_pool(turn_pool, column_tasks, failed_tasks)

            return runner

        return self._run_grouped_action_phase(
            ordered_batches=ordered_batches, rest_chains=rest_chains, build_runner=build_runner
        )

    def _participating_agents(self, agents: list[Any], step_index: int) -> list[Any]:
        """Apply the sim-level participation filter to this step's roster.

        The policy can only remove agents: its returned names are intersected with
        the live roster (order preserved), so it never adds or reorders. GM updates
        and probes still see the full roster — participation gates only who is
        scheduled to act.
        """
        if self.participation is None:
            return agents
        names = self.participation.participating_agents(
            agent_names=[agent.name for agent in agents],
            step_index=step_index,
            seed=self.seed,
        )
        allowed = {str(name).strip() for name in names}
        return [agent for agent in agents if agent.name in allowed]

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
            agents=self._participating_agents(agents, step_index),
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
