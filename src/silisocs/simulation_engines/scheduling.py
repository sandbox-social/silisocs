"""Turn-scheduling machinery for the native runtime engine.

``SchedulingMixin`` owns everything about *how* a step's turns get scheduled and
drained — batch prep, per-GM concurrency semaphores, the worker-limit/retry
telemetry envelope, and the serial / concurrent-chain / staged-barrier traversal
paths. It is mixed into :class:`~silisocs.simulation_engines.base_engines.RuntimeEngine`,
which keeps the lifecycle concerns (construction, the per-GM lock, the observe/act/
resolve turn body, per-step GM updates, the loop). The two are split for
readability, not runtime isolation: the mixin methods run with ``self`` bound to the
engine and rely on the small, fixed slice of engine state annotated below (the host
class owns its initialization plus the per-GM lock and ``run_agent_step`` that the
turn thunks re-enter).
"""

from __future__ import annotations

import concurrent.futures
import functools
import logging
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, cast

from silisocs.agents.base_agent import Agent
from silisocs.runtime.telemetry import (
    SimMetricsCollector,
    capture_retry_counters,
    collect_retry_telemetry,
    collect_unique_models,
    compute_dynamic_worker_limit,
    set_model_retry_phase,
    summarize_retry_delta,
    update_adaptive_worker_cap,
)
from silisocs.runtime.types import ActionSpec
from silisocs.simulation_engines.policies.routers import RouteInfo
from silisocs.simulation_engines.recorders import probe_empty, retry_empty
from silisocs.simulation_engines.runtime_base import (
    BranchHop,
    Hop,
    StepBatch,
    StepResult,
    TurnPolicy,
    expand_hop,
)

_LOGGER = logging.getLogger(__name__)

# One prepped agent batch (a GM and its {task_name: thunk} map) and one chain stage
# (the groups that drain together — a single group for a normal hop, several for a
# resolved branch). Used by the multi-GM grouped/staged execution paths.
_Group = tuple[Any, dict[str, Callable[[], str]]]
_Stage = list[_Group]


@dataclass
class _BranchStage:
    """A branch stage whose routing is resolved at drain time.

    Carried through prep in place of a concrete ``_Stage`` so the router runs only
    after the flow's earlier hops have drained (concurrent path: on the flow's driver
    thread; staged path: after the prior stage barrier; serial path: when the traversal
    reaches it). Stage-width stats are estimated optimistically from the branch's
    candidate count in ``prep_stage``.
    """

    hop: BranchHop


# One prepped chain stage: concrete groups, or a branch resolved at drain time.
_ChainStage = _Stage | _BranchStage


def _stage_width(stage: _ChainStage) -> int:
    """Turns in a stage: the sum of its group sizes, or a branch's candidate count
    (every candidate acts on one chosen GM, so the count bounds the resolved width).
    """
    if isinstance(stage, _BranchStage):
        return len(stage.hop.candidates)
    return sum(len(tasks) for _, tasks in stage)


def _ensure_gm_method(game_master: Any, method: str) -> Callable[..., Any]:
    fn = getattr(game_master, method, None)
    if not callable(fn):
        raise TypeError(f"Runtime game masters must expose {method}(...).")
    return fn


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


class SchedulingMixin:
    """Turn-scheduling machinery mixed into ``RuntimeEngine`` (see module docstring)."""

    # State provided by RuntimeEngine.__init__ that the scheduling methods read.
    # The host class owns their construction; annotated here so the mixin type-checks
    # in isolation. ``run_agent_step`` stays on the host and is reached only via the
    # engine handle the turn thunks close over. The per-GM lock (``_gm_lock``) is
    # normally reached the same way, but branch resolution calls it directly to
    # serialize mid-step ``selected_turns`` against concurrent turns.
    turn_policy: TurnPolicy
    gm_turn_policies: dict[str, TurnPolicy]
    gm_concurrency_caps: dict[str, int]
    _configured_worker_cap: int | None
    _action_phase_cap: int | None
    _gm_sems: dict[int, threading.BoundedSemaphore]
    _gm_sems_guard: threading.Lock
    _gm_lock: Callable[[Any], Any]  # host: gm -> threading.Lock (per-GM turn serialization)

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

        Operational envelope: a gated turn blocks on the permit *while occupying a
        turn-pool worker*, so a heavily-capped GM with many queued turns can hold
        up to ``worker_limit`` workers waiting on its permits while other GMs' turns
        get no thread (a throughput cliff, not a deadlock). Keep per-GM caps well
        below ``sim.max_concurrent_actions`` so the pool is never dominated by one
        capped GM's blocked turns.
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

    def agent_flow_tag(self, game_master: Any, agent_name: str) -> str:
        """Public step-strategy API: the flow tag a game master assigns an agent."""
        flow_map = dict(getattr(game_master, "agent_flow_tags", {}) or {})
        return str(flow_map.get(agent_name, "default") or "default")

    def selected_turns(
        self,
        *,
        game_master: Any,
        candidate_agents: Sequence[Agent],
    ) -> list[tuple[Agent, ActionSpec]]:
        """Public step-strategy API: (agent, ActionSpec) turns a GM selects to act."""
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

    # --- Branch routing (resolved when the flow's chain reaches the branch) ---

    def _resolve_branch(self, hop: BranchHop) -> list[StepBatch]:
        """Run a branch's router, validate its assignment, and select turns per chosen GM.

        The router call runs unlocked — it may take real time (call agents, read live
        backend state). The per-chosen-GM ``selected_turns`` that follows mutates GM
        next_acting state, so it runs under that GM's lock — the same lock
        ``run_agent_step`` takes — so mid-step selection cannot race a concurrent turn.
        Iterating ``hop.gms`` (config order) and candidate order keeps a deterministic
        router's resolution deterministic.
        """
        info = RouteInfo(flow=hop.flow_name, step=hop.step_index, seed=hop.seed)
        route = hop.router(hop.candidates, hop.gms, info)
        if not isinstance(route, Mapping):
            raise TypeError(
                f"Router for flow '{hop.flow_name}' must return a mapping of agent name "
                f"-> GM name; got {type(route).__name__}."
            )
        assigned = {str(name): str(gm_name).strip() for name, gm_name in route.items()}
        by_name = {agent.name: agent for agent in hop.candidates}
        unknown = sorted(set(assigned) - set(by_name))
        if unknown:
            raise ValueError(
                f"Router for flow '{hop.flow_name}' routed unknown agent(s) {unknown}; "
                f"this stage's agents are {sorted(by_name)}."
            )
        missing = sorted(set(by_name) - set(assigned))
        if missing:
            raise ValueError(
                f"Router for flow '{hop.flow_name}' left agent(s) {missing} unrouted; "
                "every agent must be assigned one of the branch's GMs."
            )
        bad = sorted({gm for gm in assigned.values() if gm not in hop.gms})
        if bad:
            raise ValueError(
                f"Router for flow '{hop.flow_name}' returned {bad}, not among its "
                f"choices {list(hop.gms)}."
            )
        batches: list[StepBatch] = []
        for gm_name, gm in hop.gms.items():
            chosen = [agent for agent in hop.candidates if assigned[agent.name] == gm_name]
            if not chosen:
                continue
            with self._gm_lock(gm):
                turns = self.selected_turns(game_master=gm, candidate_agents=chosen)
            batches.append(
                StepBatch(
                    flow_name=hop.flow_name,
                    game_master=gm,
                    turns=turns,
                    turn_policy=hop.turn_policy,
                )
            )
        return batches

    def _resolve_branch_stage(self, hop: BranchHop) -> _Stage:
        """Resolve a branch and prep its per-GM batches into a drain-ready stage."""
        stage: _Stage = []
        for batch in self._resolve_branch(hop):
            tasks, _names, _models = self._batch_tasks(batch)
            stage.append((batch.game_master, tasks))
        return stage

    @staticmethod
    def _run_tasks_with_limit(
        tasks: Mapping[str, Callable[[], str]],
        worker_limit: int,
        failed_tasks: list[str] | None = None,
    ) -> dict[str, str]:
        # Production callers drive turns via side effects (resolve_action) and ignore
        # the returned map, but returning it keeps the per-turn failure-isolation
        # behavior directly unit-testable (see test_engine_batch_failure).
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
                    _record_isolated_failure(task_name, failed_tasks)
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

    def execute_batches(
        self,
        *,
        step_index: int,
        batches: Sequence[Hop],
        verbose: bool,
    ) -> StepResult:
        """Public step-strategy API: run hops strictly serially, one batch at a time.

        Accepts full hops: ``None`` (idle) slots are skipped, and a ``BranchHop`` is
        resolved IN PLACE when the traversal reaches it — its router runs after every
        earlier batch has drained (the serial analogue of the concurrent/staged paths'
        stage-time routing) and its per-GM batches then drain one at a time. This is the
        execution path used by the base/sequential/flow step strategies and by the
        ``multi_gm_serial`` traversal.
        """
        del verbose, step_index
        work: list[_Group | _BranchStage] = []
        model_pool: dict[int, Any] = {}
        active_names: set[str] = set()
        requested_workers = 0
        primary_gm_name = ""
        for hop in batches:
            if hop is None:
                continue
            if isinstance(hop, BranchHop):
                # Optimistic prep (mirrors the grouped paths): every candidate acts on
                # one of the choice GMs, so all candidates count and every choice GM's
                # models are unioned in.
                active_names.update(agent.name for agent in hop.candidates)
                requested_workers += len(hop.candidates)
                for gm in hop.gms.values():
                    for model_obj in collect_unique_models(gm, list(hop.candidates)):
                        model_pool[id(model_obj)] = model_obj
                    if not primary_gm_name:
                        primary_gm_name = str(gm.name)
                work.append(_BranchStage(hop))
                continue
            tasks, names, models = self._batch_tasks(hop)
            work.append((hop.game_master, tasks))
            active_names.update(names)
            # Total turns this step. Batches drain one at a time so the peak is the
            # widest batch, but requested_workers is reported as the sum across
            # batches (a step-size signal) — kept stable as observable telemetry.
            requested_workers += len(tasks)
            for model_obj in models:
                model_pool[id(model_obj)] = model_obj
            if not primary_gm_name:
                primary_gm_name = hop.game_master.name

        if not work:
            return self._empty_step_result()

        def runner(worker_limit: int, failed_tasks: list[str]) -> None:
            for item in work:
                groups = (
                    self._resolve_branch_stage(item.hop)
                    if isinstance(item, _BranchStage)
                    else [item]
                )
                for game_master, tasks in groups:
                    self._run_tasks_with_limit(
                        self._wrap_group(game_master, worker_limit, tasks),
                        worker_limit,
                        failed_tasks=failed_tasks,
                    )

        return self._run_action_phase(
            requested_workers=max(1, requested_workers),
            active_names=active_names,
            models=list(model_pool.values()),
            primary_gm_name=primary_gm_name,
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
        stages: list[_ChainStage],
        failed_tasks: list[str],
        worker_limit: int,
    ) -> None:
        """Drive one flow's GM chain: drain each stage in order before the next.

        A normal hop is a one-group stage; a resolved branch is a multi-group stage
        whose groups (distinct agent subsets on different GMs) drain together as one
        barrier before the chain advances. A ``_BranchStage`` is resolved HERE, after
        the flow's earlier stages have drained — so the router observes their effects
        (or asks the agent) — then drains like any other stage. Runs on a driver thread
        (not a turn-pool worker), so blocking on a stage's futures keeps that flow's
        stages serial — preserving per-agent chain order — without consuming a
        turn-worker slot or stalling sibling chains. A failed turn at one stage is
        isolated and counted but does not abort later stages (same as the legacy serial
        path); a downstream stage may then observe a GM the failed stage never resolved
        into.
        """
        for stage in stages:
            concrete = (
                self._resolve_branch_stage(stage.hop) if isinstance(stage, _BranchStage) else stage
            )
            stage_tasks: dict[str, Callable[[], str]] = {}
            for game_master, tasks in concrete:
                stage_tasks.update(self._wrap_group(game_master, worker_limit, tasks))
            self._drain_tasks_on_pool(pool, stage_tasks, failed_tasks)

    def _run_grouped_action_phase(
        self,
        *,
        ordered_batches: Sequence[StepBatch],
        rest_chains: Sequence[Sequence[Hop]],
        build_runner: Callable[..., Callable[[int, list[str]], None]],
    ) -> StepResult:
        """Shared prep + telemetry envelope for the concurrent and staged multi-GM paths.

        ``ordered_batches`` is the flow_order serial prefix; ``rest_chains`` is the
        remaining flows' hop lists IN FLOW ORDER. A hop is a normal ``StepBatch``,
        ``None`` (an idle slot), or a ``BranchHop`` (routing resolved at drain time).
        Each hop is prepped into a *stage*: concrete ``(game_master, tasks)`` groups, or
        a ``_BranchStage`` marker. A branch stage estimates its stats optimistically —
        every candidate acts on one of the choice GMs, so all candidates count and every
        choice GM's models are unioned in — so ``primary_game_master`` and the worker
        envelope stay identical whether or not a hop is a branch.
        ``build_runner(ordered_groups, rest_groups)`` returns the action-phase runner.
        """
        active_names: set[str] = set()
        model_pool: dict[int, Any] = {}
        primary_gm_name = ""

        def prep_stage(hop: Hop) -> _ChainStage:
            nonlocal primary_gm_name
            if isinstance(hop, BranchHop):
                active_names.update(agent.name for agent in hop.candidates)
                for gm in hop.gms.values():
                    for model_obj in collect_unique_models(gm, list(hop.candidates)):
                        model_pool[id(model_obj)] = model_obj
                    if not primary_gm_name:
                        primary_gm_name = str(gm.name)
                return _BranchStage(hop)
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

        # flow_order (prefix) flows cannot contain a branch (rejected at build), so every
        # prefix stage is concrete; guard rather than iterate a branch marker.
        ordered_groups: list[_Group] = []
        for hop in ordered_batches:
            prefix_stage = prep_stage(hop)
            if isinstance(prefix_stage, _BranchStage):
                raise RuntimeError("A flow_order prefix flow cannot contain a branch.")
            ordered_groups.extend(prefix_stage)
        rest_groups: list[list[_ChainStage]] = [
            [prep_stage(hop) for hop in chain] for chain in rest_chains
        ]

        # Short-circuit only when there are genuinely no batches, not merely no selected
        # turns, so an all-inactive step still emits the full telemetry envelope.
        if not primary_gm_name:
            return self._empty_step_result()

        # Size the pool to PEAK concurrent turns, not the sum across sequential work: the
        # flow_order prefix runs before the rest (they never overlap), and within a flow
        # chain only one stage runs at a time. The widest single wave is the prefix's widest
        # group vs. the sum over flows of each flow's widest stage (a branch's groups drain
        # together, so a stage's width is the total turns across its groups; a deferred
        # branch is bounded by its candidate count); that sum also upper-bounds the staged
        # path's widest stage column, so one formula sizes both paths without undersizing.
        prefix_peak = max((len(tasks) for _, tasks in ordered_groups), default=0)
        rest_peak = sum(
            max((_stage_width(stage) for stage in chain), default=0) for chain in rest_groups
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

    def execute_chain_groups(
        self,
        *,
        ordered_batches: Sequence[StepBatch],
        rest_chains: Sequence[Sequence[Hop]],
    ) -> StepResult:
        """Public step-strategy API: concurrent multi-GM execution.

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
            rest_groups: list[list[_ChainStage]],
        ) -> Callable[[int, list[str]], None]:
            # Drop empty concrete stages (idle slots); a _BranchStage is truthy and kept.
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

    def execute_staged_groups(
        self,
        *,
        ordered_batches: Sequence[StepBatch],
        rest_chains: Sequence[Sequence[Hop]],
    ) -> StepResult:
        """Public step-strategy API: staged multi-GM execution with a per-stage barrier.

        ``ordered_batches`` (flow_order-listed flows) run first as a strict serial
        prefix, exactly as in ``execute_chain_groups``. The remaining flows' per-flow
        hop lists are then advanced one stage column at a time: every hop in a stage is
        submitted to the shared turn pool together (different GMs run in parallel; turns
        on the same GM serialize on the per-GM lock in ``run_agent_step``), and the next
        stage does not start until ALL of the current stage's turns finish — the global
        barrier. A ``_BranchStage`` in a column is resolved when the column runs — after
        the prior stage's barrier — so its router observes the earlier stages' effects.
        Unlike the concurrent path there is no driver pool: the engine sequences the
        stages directly. A flow that idles (``None``) or ends at a stage contributes no
        hop to that column.
        """

        def build_runner(
            ordered_groups: list[_Group],
            rest_groups: list[list[_ChainStage]],
        ) -> Callable[[int, list[str]], None]:
            depth = max((len(chain) for chain in rest_groups), default=0)

            def runner(worker_limit: int, failed_tasks: list[str]) -> None:
                with concurrent.futures.ThreadPoolExecutor(
                    max_workers=max(1, worker_limit)
                ) as turn_pool:
                    self._drain_prefix(turn_pool, ordered_groups, worker_limit, failed_tasks)
                    for stage_index in range(depth):
                        # Build stage column N (transpose across flows), resolving any
                        # branch AFTER the prior column's barrier so its router sees
                        # earlier stages' effects. One global barrier per column: task
                        # names are f"{gm}::{agent}" and each agent is in one flow, so keys
                        # never collide.
                        column_tasks: dict[str, Callable[[], str]] = {}
                        for chain in rest_groups:
                            if stage_index >= len(chain):
                                continue
                            stage = chain[stage_index]
                            concrete = (
                                self._resolve_branch_stage(stage.hop)
                                if isinstance(stage, _BranchStage)
                                else stage
                            )
                            for game_master, tasks in concrete:
                                column_tasks.update(
                                    self._wrap_group(game_master, worker_limit, tasks)
                                )
                        if column_tasks:
                            self._drain_tasks_on_pool(turn_pool, column_tasks, failed_tasks)

            return runner

        return self._run_grouped_action_phase(
            ordered_batches=ordered_batches, rest_chains=rest_chains, build_runner=build_runner
        )
