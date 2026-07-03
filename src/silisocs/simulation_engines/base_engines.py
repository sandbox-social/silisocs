"""Native strategy-driven runtime engine.

The engine keeps the *lifecycle* concerns — construction, the per-GM lock, the
observe/act/resolve turn body, per-step GM updates, participation gating, and the
loop hand-off. Everything about *how* a step's turns get scheduled and drained lives
in :class:`~silisocs.simulation_engines.scheduling.SchedulingMixin`, which is mixed
in below; see that module for the split rationale.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Mapping
from typing import Any

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
)
from silisocs.simulation_engines.scheduling import SchedulingMixin, _ensure_gm_method

_LOGGER = logging.getLogger(__name__)


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
        # cadence; consulted in _batch_tasks (SchedulingMixin) per-flow override but
        # above global.
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
