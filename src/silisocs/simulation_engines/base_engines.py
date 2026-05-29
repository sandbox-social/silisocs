"""Native runtime engine and built-in strategies."""

from __future__ import annotations

import concurrent.futures
import functools
import logging
import threading
import time
from collections import OrderedDict
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, cast

from omegaconf import OmegaConf

from silisocs.agents.base_agent import Agent
from silisocs.runtime.telemetry import (
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
from silisocs.simulation_engines.recorders import DefaultEngineRecorder, probe_empty, retry_empty
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


@dataclass
class _StepBatch:
    flow_name: str
    game_master: Any
    turns: list[tuple[Agent, ActionSpec]]


class RuntimeEngine(RuntimeEngineBase):
    """Strategy-driven runtime engine."""

    def __init__(
        self,
        *,
        config: Any | None = None,
        loop_strategy: LoopStrategy | None = None,
        step_strategy: StepStrategy | None = None,
        turn_policy: TurnPolicy | None = None,
        probe_runner: ProbeRunner | None = None,
        recorder: EngineRecorder | None = None,
    ) -> None:
        self.config = config
        self.loop_strategy = loop_strategy or FixedStepsLoopStrategy()
        self.step_strategy = step_strategy or BaseStepStrategy()
        self.turn_policy = turn_policy or build_turn_policy(
            {"built_in": "single_action", "params": {}}
        )
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
        skip_actions: bool,
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

            if skip_actions:
                return AgentStepResult(
                    agent_name=agent.name,
                    rendered_action="",
                    raw_action=ActionOutput.skip(),
                    resolved_result="",
                )

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
        engine_map: Mapping[str, Any] = {}
        if self.config is not None:
            selected = OmegaConf.select(
                self.config,
                "sim.engine.step.params.agent_to_flow",
                default={},
            )
            if isinstance(selected, Mapping):
                engine_map = cast(Mapping[str, Any], selected)
        if isinstance(engine_map, Mapping):
            for key, value in engine_map.items():
                key_name = str(key).strip()
                if key_name:
                    flow_map[key_name] = str(value).strip() or "default"
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
                    results[task_name] = ""
            return results

    def _execute_batches(
        self,
        *,
        step_index: int,
        batches: Sequence[_StepBatch],
        verbose: bool,
    ) -> StepResult:
        del verbose
        if not batches:
            return StepResult(
                skipped=True,
                primary_game_master="",
                probe_phase=probe_empty(0),
                action_phase={"active_agents": 0, "duration_s": 0.0, "retry": retry_empty()},
            )

        tasks_by_flow: list[tuple[str, dict[str, Callable[[], str]]]] = []
        model_pool: dict[int, Any] = {}
        active_names: set[str] = set()
        for batch in batches:
            flow_tasks: dict[str, Callable[[], str]] = {}
            for agent, spec in batch.turns:
                active_names.add(agent.name)
                task_name = f"{batch.game_master.name}::{agent.name}"
                flow_tasks[task_name] = functools.partial(
                    self.turn_policy.run,
                    engine=self,
                    game_master=batch.game_master,
                    agent=agent,
                    action_spec=spec,
                    skip_actions=False,
                    verbose=False,
                )
            tasks_by_flow.append((f"{batch.game_master.name}:{batch.flow_name}", flow_tasks))
            for model_obj in collect_unique_models(
                batch.game_master, [agent for agent, _ in batch.turns]
            ):
                model_pool[id(model_obj)] = model_obj

        requested_workers = max(1, sum(len(tasks) for _, tasks in tasks_by_flow))
        dynamic_cap, worker_limit = compute_dynamic_worker_limit(
            requested_workers=requested_workers,
            phase_cap=self._action_phase_cap,
            configured_worker_cap=self._configured_worker_cap,
        )
        models = list(model_pool.values())
        before = capture_retry_counters(models)
        set_model_retry_phase(models, "action")
        action_start = time.time()
        try:
            for _flow_name, tasks in tasks_by_flow:
                self._run_tasks_with_limit(tasks, worker_limit)
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
            },
            probe_phase=probe_empty(len(active_names)),
            primary_game_master=batches[0].game_master.name if batches else "",
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


class FixedStepsLoopStrategy(LoopStrategy):
    """Default loop strategy: run episodes from start_step up to max_steps."""

    name = "fixed_steps"

    def run(
        self,
        *,
        engine: RuntimeEngine,
        game_masters: list[Any],
        agents: list[Any],
        max_steps: int,
        start_step: int,
        verbose: bool,
        checkpoint_callback: Any | None,
    ) -> None:
        step = max(0, int(start_step))
        while step < int(max_steps):
            t0 = time.time()
            probe_phase = probe_empty(len(agents))
            if engine.probe_runner is not None:
                deployed, selected = engine.probe_runner.maybe_run(
                    step=step,
                    agents=agents,
                    worker_limit=None,
                )
                probe_phase["deployed"] = deployed
                probe_phase["selected_agents"] = selected
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


class BaseStepStrategy(StepStrategy):
    """Single-GM, no flow grouping."""

    name = "base"

    def run(
        self,
        *,
        engine: RuntimeEngine,
        step_index: int,
        game_masters: list[Any],
        agents: list[Any],
        verbose: bool,
    ) -> StepResult:
        del step_index, verbose
        if not game_masters:
            raise ValueError("No game masters configured.")
        gm = game_masters[0]
        turns = engine._selected_turns(game_master=gm, candidate_agents=cast(list[Agent], agents))
        return engine._execute_batches(
            step_index=0,
            batches=[_StepBatch(flow_name="default", game_master=gm, turns=turns)],
            verbose=False,
        )


@dataclass
class FlowStepStrategy(StepStrategy):
    """Flow-aware step scheduling for a single game master."""

    flow_order: tuple[str, ...] = ("fixed_pre", "default")
    name: str = "flow"

    def run(
        self,
        *,
        engine: RuntimeEngine,
        step_index: int,
        game_masters: list[Any],
        agents: list[Any],
        verbose: bool,
    ) -> StepResult:
        del step_index, verbose
        if not game_masters:
            raise ValueError("No game masters configured.")
        groups: OrderedDict[str, list[tuple[Agent, ActionSpec]]] = OrderedDict()
        gm = game_masters[0]
        agents_by_flow: OrderedDict[str, list[Agent]] = OrderedDict()
        for agent in cast(list[Agent], agents):
            flow = engine._agent_flow_tag(gm, agent.name)
            agents_by_flow.setdefault(flow, []).append(agent)
        for flow, flow_agents in agents_by_flow.items():
            turns = engine._selected_turns(game_master=gm, candidate_agents=flow_agents)
            groups.setdefault(flow, []).extend(turns)
        batches: list[_StepBatch] = []
        used: set[str] = set()
        for flow in self.flow_order:
            flow_name = str(flow).strip()
            if flow_name and flow_name in groups:
                batches.append(
                    _StepBatch(flow_name=flow_name, game_master=gm, turns=groups[flow_name])
                )
                used.add(flow_name)
        for flow_name, flow_turns in groups.items():
            if flow_name not in used:
                batches.append(_StepBatch(flow_name=flow_name, game_master=gm, turns=flow_turns))
        return engine._execute_batches(step_index=0, batches=batches, verbose=False)


@dataclass
class MultiGMStepStrategy(StepStrategy):
    """Flow-first multi-GM routing strategy using flow_to_gms chains."""

    flow_order: tuple[str, ...] = ("fixed_pre", "default")
    name: str = "multi_gm"

    def run(
        self,
        *,
        engine: RuntimeEngine,
        step_index: int,
        game_masters: list[Any],
        agents: list[Any],
        verbose: bool,
    ) -> StepResult:
        del step_index, verbose
        if not game_masters:
            raise ValueError("No game masters configured.")
        gm_by_name = {str(gm.name): gm for gm in game_masters}
        flow_to_agents: OrderedDict[str, list[Agent]] = OrderedDict()
        default_gm = game_masters[0]
        for agent in cast(list[Agent], agents):
            flow = engine._agent_flow_tag(default_gm, agent.name)
            flow_to_agents.setdefault(flow, []).append(agent)
        flow_chains = dict(getattr(default_gm, "flow_chains", {}) or {})

        batches: list[_StepBatch] = []
        ordered_flows: list[str] = []
        seen: set[str] = set()
        for flow in self.flow_order:
            name = str(flow).strip()
            if name and name in flow_to_agents:
                ordered_flows.append(name)
                seen.add(name)
        for flow_name in flow_to_agents:
            if flow_name not in seen:
                ordered_flows.append(flow_name)
        for flow_name in ordered_flows:
            candidates = flow_to_agents.get(flow_name, [])
            if not candidates:
                continue
            chain = flow_chains.get(flow_name) or [default_gm.name]
            chain_names = [str(name).strip() for name in chain if str(name).strip()]
            for gm_name in chain_names:
                gm = gm_by_name.get(gm_name)
                if gm is None:
                    raise ValueError(
                        f"Unknown GM '{gm_name}' in flow chain for flow '{flow_name}'."
                    )
                turns = engine._selected_turns(game_master=gm, candidate_agents=candidates)
                batches.append(_StepBatch(flow_name=flow_name, game_master=gm, turns=turns))
        return engine._execute_batches(step_index=0, batches=batches, verbose=False)


def _extract_flow_order(cfg: Any | None) -> tuple[str, ...]:
    """Read sim.engine.step.params.flow_order from config with sensible default."""
    default = ["fixed_pre", "default"]
    raw = (
        OmegaConf.select(cfg, "sim.engine.step.params.flow_order", default=default)
        if cfg is not None
        else default
    )
    return tuple(str(item) for item in (raw or default))


def _apply_engine_defaults(kwargs: dict[str, Any], step_strategy: Any) -> None:
    """Set shared defaults (loop strategy, step strategy, turn policy) on kwargs."""
    cfg = kwargs.get("config")
    kwargs.setdefault("loop_strategy", FixedStepsLoopStrategy())
    kwargs.setdefault("step_strategy", step_strategy)
    kwargs.setdefault("turn_policy", build_turn_policy(_engine_turn_policy_cfg(cfg)))


class BaseRuntimeEngine(RuntimeEngine):
    """Runtime engine preset wrapper using base step strategy."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        flow_order = _extract_flow_order(kwargs.get("config"))
        _apply_engine_defaults(kwargs, BaseStepStrategy())
        super().__init__(*args, **kwargs)
        self._flow_order = flow_order


class FlowRuntimeEngine(RuntimeEngine):
    """Runtime engine preset wrapper using flow step strategy."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        flow_order = _extract_flow_order(kwargs.get("config"))
        _apply_engine_defaults(kwargs, FlowStepStrategy(flow_order=flow_order))
        super().__init__(*args, **kwargs)


class MultiGMRuntimeEngine(FlowRuntimeEngine):
    """Runtime engine preset wrapper using flow-first multi-GM step strategy."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        flow_order = _extract_flow_order(kwargs.get("config"))
        _apply_engine_defaults(kwargs, MultiGMStepStrategy(flow_order=flow_order))
        super().__init__(*args, **kwargs)


def _engine_turn_policy_cfg(cfg: Any | None) -> Mapping[str, Any] | None:
    if cfg is None:
        return {"built_in": "single_action", "params": {}}
    slot = OmegaConf.select(cfg, "sim.engine.turn_policy", default=None)
    if slot is None:
        return {"built_in": "single_action", "params": {}}
    return cast(Mapping[str, Any], OmegaConf.to_container(slot, resolve=True))
