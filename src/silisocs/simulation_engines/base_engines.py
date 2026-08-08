"""Native strategy-driven runtime engine.

The engine keeps the *lifecycle* concerns — construction, the per-GM lock, the
observe/act/resolve turn body, per-step GM updates, participation gating, and the
loop hand-off. Everything about *how* a step's turns get scheduled and drained lives
in :class:`~silisocs.simulation_engines.scheduling.SchedulingMixin`, which is mixed
in below; see that module for the split rationale.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import threading
from collections.abc import Iterator, Mapping
from typing import Any

from silisocs.runtime.concurrency import EventLoopThread
from silisocs.runtime.telemetry import resolve_configured_worker_cap
from silisocs.runtime.types import ActionOutput, ActionSpec
from silisocs.simulation_engines.policies.factory import build_turn_policy
from silisocs.simulation_engines.policies.loops import FixedStepsLoopStrategy
from silisocs.simulation_engines.policies.steps import BaseStepStrategy
from silisocs.simulation_engines.recorders import DefaultEngineRecorder
from silisocs.simulation_engines.runtime_base import (
    AgentStepResult,
    EngineRecorder,
    LoopStrategy,
    ProbeRunner,
    RuntimeEngineBase,
    StepResult,
    StepStrategy,
    TurnPolicy,
    set_gm_episode_index,
)
from silisocs.simulation_engines.scheduling import SchedulingMixin, _ensure_gm_method

_LOGGER = logging.getLogger(__name__)


def _gm_episode_index(game_master: Any) -> int:
    backend = getattr(game_master, "backend", None)
    action_logger = getattr(backend, "action_logger", None)
    raw = getattr(action_logger, "episode_idx", -1)
    try:
        return int(raw)
    except (TypeError, ValueError):
        return -1


class RuntimeEngine(SchedulingMixin, RuntimeEngineBase):
    """Strategy-driven runtime engine.

    Turn-scheduling behavior comes from :class:`SchedulingMixin`; this class owns the
    lifecycle: the per-GM lock, the single-turn observe/act/resolve body, per-step GM
    updates, participation gating, and the loop hand-off.
    """

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
        interventions: Any | None = None,
        seed: int = 0,
        probe_runner: ProbeRunner | None = None,
        recorder: EngineRecorder | None = None,
        executor: str = "threads",
    ) -> None:
        self.config = config
        self.loop_strategy = loop_strategy or FixedStepsLoopStrategy()
        self.step_strategy = step_strategy or BaseStepStrategy()
        self.turn_policy = turn_policy or build_turn_policy(
            {"built_in": "single_action", "params": {}}
        )
        # Per-GM turn policies (keyed by GM name) let a backend set its own action
        # cadence; consulted in _batch_tasks (SchedulingMixin) per-flow override but
        # above global.
        self.gm_turn_policies: dict[str, TurnPolicy] = dict(gm_turn_policies or {})
        # Per-GM concurrency caps (keyed by GM name): a per-GM BoundedSemaphore caps
        # how many of that GM's turns run concurrently; empty = global cap only.
        self.gm_concurrency_caps: dict[str, int] = dict(gm_concurrency_caps or {})
        # Sim-level participation policy: filters each step's agent roster before
        # any scheduling or GM next_acting runs. None = every agent participates.
        self.participation = participation
        # Declarative mid-run interventions fired at step boundaries by the loop
        # strategy (None/empty = no-op). See simulation_engines/interventions.py.
        self.interventions = interventions
        self.seed = int(seed)
        self.probe_runner = probe_runner
        output_dir = ""
        record_active_agent_names = False
        if config is not None:
            output_dir = str(getattr(config, "output_dir", "") or "")
            telemetry_cfg = getattr(getattr(config, "sim", None), "telemetry", None)
            record_active_agent_names = bool(
                getattr(telemetry_cfg, "record_active_agent_names", False)
            )
        self.recorder = recorder or DefaultEngineRecorder(
            output_dir=output_dir,
            record_active_agent_names=record_active_agent_names,
        )
        self._configured_worker_cap = (
            resolve_configured_worker_cap(config) if config is not None else None
        )
        self._action_phase_cap: int | None = None
        self._gm_locks: dict[int, threading.Lock] = {}
        self._gm_locks_guard = threading.Lock()
        self._gm_sems: dict[int, threading.BoundedSemaphore] = {}
        self._gm_async_sems: dict[int, asyncio.Semaphore] = {}
        self._gm_sems_guard = threading.Lock()
        self._initialization_context: Any | None = None
        # Turn executor: "threads" (one pool worker per in-flight turn, the
        # default) or "asyncio" (turns are coroutines on one background event
        # loop; sync agents/policies hop to helper threads via to_thread). The
        # worker-limit envelope keeps its meaning — max concurrent turns —
        # enforced as an asyncio.Semaphore instead of pool size.
        executor_mode = str(executor or "threads").strip().lower()
        if executor_mode not in {"threads", "asyncio"}:
            raise ValueError(
                f"Unknown sim.engine.executor={executor!r}; use 'threads' or 'asyncio'."
            )
        self._async_turns = executor_mode == "asyncio"
        self._loop_runner: EventLoopThread | None = None
        self._loop_runner_guard = threading.Lock()
        self._async_gate: asyncio.Semaphore | None = None

    def _gm_lock(self, game_master: Any) -> threading.Lock:
        key = id(game_master)
        # Lock-free hot path: dict reads are GIL-atomic, and this is called twice
        # per agent turn, so taking the guard here would convoy every worker
        # thread on one process-wide lock. The guard only covers first creation.
        lock = self._gm_locks.get(key)
        if lock is None:
            with self._gm_locks_guard:
                lock = self._gm_locks.setdefault(key, threading.Lock())
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

    @contextlib.contextmanager
    def _model_runtime_context(
        self, game_master: Any, agent: Any, action_spec: ActionSpec
    ) -> Iterator[None]:
        """Stamp the agent's model with this turn's telemetry context, then clear it."""
        model = getattr(agent, "model", None)
        set_ctx = getattr(model, "set_runtime_context", None)
        clear_ctx = getattr(model, "clear_runtime_context", None)
        if callable(set_ctx):
            set_ctx(
                agent_name=agent.name,
                episode_idx=_gm_episode_index(game_master),
                phase="action",
                action_tag=action_spec.tag,
            )
        try:
            yield
        finally:
            if callable(clear_ctx):
                clear_ctx()

    def _observe_phase(self, game_master: Any, agent: Any) -> None:
        """Deliver the GM's pre-action observation to the agent (turn phase 1)."""
        observe_fn = _ensure_gm_method(game_master, "make_observation")
        # Observation components that declare themselves read-only (see
        # ObservationComponent.read_only) run WITHOUT the per-GM lock:
        # backend reads use thread-local WAL connections and event logging
        # is thread-safe, so the step's timeline reads proceed concurrently
        # instead of serializing every turn on one lock.
        lock_free_fn = getattr(game_master, "observation_is_lock_free", None)
        if callable(lock_free_fn) and lock_free_fn(agent.name):
            observation = str(observe_fn(agent.name))
        else:
            with self._gm_lock(game_master):
                observation = str(observe_fn(agent.name))
        if observation.strip():
            agent.observe(observation)

    def _resolve_phase(self, game_master: Any, agent: Any, action_output: ActionOutput) -> str:
        """Resolve the agent's action against the GM and feed back the result (phase 3)."""
        with self._gm_lock(game_master):
            resolve_fn = _ensure_gm_method(game_master, "resolve_action")
            raw_resolved = resolve_fn(agent.name, action_output)
        # Keep a ResolveReport (str subclass with commit counts) intact for the turn
        # policy; coerce any non-str return to str. resolved stays a str either way.
        resolved = raw_resolved if isinstance(raw_resolved, str) else str(raw_resolved)
        if resolved.strip():
            agent.observe(resolved)
        return resolved

    @staticmethod
    def _as_action_output(raw_action: Any) -> ActionOutput:
        return (
            raw_action
            if isinstance(raw_action, ActionOutput)
            else ActionOutput.from_text(str(raw_action or ""))
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
        with self._model_runtime_context(game_master, agent, action_spec):
            if observe_before_action:
                self._observe_phase(game_master, agent)
            action_output = self._as_action_output(agent.act(action_spec))
            rendered = str(action_output)
            resolved = self._resolve_phase(game_master, agent, action_output)
            return AgentStepResult(
                agent_name=agent.name,
                rendered_action=rendered,
                raw_action=action_output,
                resolved_result=resolved,
            )

    async def run_agent_step_async(
        self,
        *,
        game_master: Any,
        agent: Any,
        action_spec: ActionSpec,
        verbose: bool,
        observe_before_action: bool = True,
    ) -> AgentStepResult:
        """Async twin of :meth:`run_agent_step`, used by the asyncio turn executor.

        The act phase awaits ``agent.act_async`` — loop-native for agents that
        override it, a helper thread for sync-only agents — so both kinds mix in
        one step. The observe/resolve phases stay synchronous and hop to helper
        threads: they are short, but may contend on the per-GM ``threading.Lock``,
        which must never block the shared event loop. The model runtime context
        is task-scoped (see ``ContextLocal``), so ``to_thread`` propagates it.
        """
        del verbose
        with self._model_runtime_context(game_master, agent, action_spec):
            if observe_before_action:
                await asyncio.to_thread(self._observe_phase, game_master, agent)
            # Duck-typed agents that don't subclass the Agent ABC may lack
            # act_async entirely; their sync act runs on a helper thread, the
            # same floor the ABC's default provides.
            act_async = getattr(agent, "act_async", None)
            if callable(act_async):
                raw_action = await act_async(action_spec)
            else:
                raw_action = await asyncio.to_thread(agent.act, action_spec)
            action_output = self._as_action_output(raw_action)
            rendered = str(action_output)
            resolved = await asyncio.to_thread(
                self._resolve_phase, game_master, agent, action_output
            )
            return AgentStepResult(
                agent_name=agent.name,
                rendered_action=rendered,
                raw_action=action_output,
                resolved_result=resolved,
            )

    def _participating_agents(self, agents: list[Any], step_index: int) -> list[Any]:
        """Apply the sim-level participation filter to this step's roster.

        The policy can only remove agents: its returned names are intersected with
        the live roster (order preserved), so it never adds or reorders. The active
        roster gates both scheduling AND per-step GM updates (so per-step backend
        work like recsys refresh is O(active)); probes and GM initialization still
        see the full roster, and an update component that needs the population
        declares ``requires_full_roster = True`` (see ComponentGameMaster.update).
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
            set_gm_episode_index(game_master, step_index)
        # Participation FIRST: GM updates and scheduling both receive the step's
        # active roster, so per-step GM work scales with who acts, not with the
        # population (the GM keeps the full roster from initialize()).
        active_agents = self._participating_agents(agents, step_index)
        for game_master in game_masters:
            update_fn = _ensure_gm_method(game_master, "update")
            with self._gm_lock(game_master):
                update_fn(
                    step=step_index, agents=active_agents, context=self._initialization_context
                )
        return self.step_strategy.run(
            engine=self,
            step_index=step_index,
            game_masters=game_masters,
            agents=active_agents,
            verbose=verbose,
        )

    def _ensure_loop_runner(self) -> EventLoopThread:
        """Lazily start (or restart) the asyncio executor's event-loop thread.

        Double-checked under ``_loop_runner_guard`` (mirroring ``_gm_lock`` /
        ``_gm_semaphore``): the concurrent multi-GM path drains from several
        driver threads at once, and without the guard two of them could each
        build a separate ``EventLoopThread`` on the first action phase — the
        shared ``_async_gate`` / per-GM async semaphores would then be awaited on
        two loops (a "bound to a different event loop" error) and the losing loop
        thread would leak. The guard makes exactly one runner win.
        """
        runner = self._loop_runner
        if runner is None or not runner.alive:
            with self._loop_runner_guard:
                runner = self._loop_runner
                if runner is None or not runner.alive:
                    runner = EventLoopThread()
                    self._loop_runner = runner
        return runner

    def _shutdown_loop_runner(self) -> None:
        with self._loop_runner_guard:
            runner = self._loop_runner
            self._loop_runner = None
        if runner is not None:
            runner.shutdown()

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
        try:
            self.loop_strategy.run(
                engine=self,
                game_masters=game_masters,
                agents=agents,
                max_steps=max_steps,
                start_step=start_step,
                verbose=verbose,
                checkpoint_callback=checkpoint_callback,
            )
        finally:
            # The asyncio executor's loop thread (if one was started) is daemon,
            # but shut it down deterministically with the run. Steps driven
            # outside run_loop (tests, custom callers) rely on the daemon flag.
            self._shutdown_loop_runner()
