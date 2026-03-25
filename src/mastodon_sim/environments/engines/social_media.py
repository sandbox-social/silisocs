# Make sure to import the original engine and any other tools you need
import concurrent.futures
import functools
import logging
import os
import threading
import time
from collections import OrderedDict
from collections.abc import Callable, Mapping, Sequence
from typing import Any, Literal, cast

import termcolor
from concordia.components.game_master import event_resolution as event_resolution_components
from concordia.components.game_master import next_acting as next_acting_components
from concordia.components.game_master import switch_act as switch_act_component
from concordia.environment import engine as engine_lib
from concordia.environment.engines import simultaneous
from concordia.typing import entity as entity_lib
from omegaconf import OmegaConf
from typing_extensions import override

from mastodon_sim.environments.engines.policies.factory import (
    build_action_loop_policy,
    build_probe_schedule_policy,
)
from mastodon_sim.evaluations.probes.deployment import ProbeDeploymentOrchestrator
from mastodon_sim.runtime.config import ConfigStore
from mastodon_sim.runtime.telemetry import (
    append_episode_run_stats,
    capture_retry_counters,
    collect_retry_telemetry,
    collect_unique_models,
    compute_dynamic_worker_limit,
    resolve_configured_worker_cap,
    set_model_retry_phase,
    summarize_retry_delta,
    update_adaptive_worker_cap,
)
from mastodon_sim.utils.misc import EventLogger, SimMetricsCollector

DEFAULT_CALL_TO_MAKE_OBSERVATION = "{name}"
DEFAULT_CALL_TO_NEXT_ACTING = "Which entities act next?"
DEFAULT_CALL_TO_NEXT_ACTION_SPEC = next_acting_components.DEFAULT_CALL_TO_NEXT_ACTION_SPEC
DEFAULT_CALL_TO_RESOLVE = "Because of all that came before, what happens next?"
DEFAULT_CALL_TO_CHECK_TERMINATION = "Is the game/simulation finished?"
DEFAULT_CALL_TO_NEXT_GAME_MASTER = "Which rule set should we use for the next step?"

DEFAULT_ACT_COMPONENT_KEY = switch_act_component.DEFAULT_ACT_COMPONENT_KEY

PUTATIVE_EVENT_TAG = event_resolution_components.PUTATIVE_EVENT_TAG
EVENT_TAG = event_resolution_components.EVENT_TAG

_PRINT_COLOR: Literal["cyan"] = "cyan"
_LOGGER = logging.getLogger(__name__)


def _get_empty_log_entry() -> dict[str, dict[str, Any]]:
    """Returns a dictionary to store a single log entry."""
    return {
        "terminate": {},
        "next_game_master": {},
        "make_observation": {},
        "next_acting": {},
        "next_action_spec": {},
        "resolve": {},
    }


class BaseSocialMediaEngine(simultaneous.Simultaneous):
    """
    A custom engine to implement parallel social media sessions, where agents can act parallely
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._gm_action_locks: dict[int, threading.Lock] = {}
        self._gm_action_locks_guard = threading.Lock()

    def _gm_lock(self, game_master: entity_lib.Entity) -> threading.Lock:
        key = id(game_master)
        with self._gm_action_locks_guard:
            lock = self._gm_action_locks.get(key)
            if lock is None:
                lock = threading.Lock()
                self._gm_action_locks[key] = lock
        return lock

    def agent_resolve(
        self,
        game_master: entity_lib.Entity,
        action: str,
        verbose: bool = False,
    ) -> str:
        """Resolve an entity's action."""
        # SwitchAct formats call_to_action with `.format(name=...)`; raw braces
        # from model text (e.g. JSON-like `{id: ...}`) must be escaped first.
        safe_action = action.replace("{", "{{").replace("}", "}}")
        result = game_master.act(
            action_spec=entity_lib.ActionSpec(
                call_to_action=safe_action,
                output_type=entity_lib.OutputType.RESOLVE,
            )
        )
        if verbose:
            print(termcolor.colored(f"The resolved event was: {result}", _PRINT_COLOR))
        return str(result)

    @override
    def next_acting(
        self,
        game_master: entity_lib.Entity,
        entities: Sequence[entity_lib.Entity],
        log_entry: Mapping[str, Any] | None = None,
        log: list[Mapping[str, Any]] | None = None,
    ) -> tuple[Sequence[entity_lib.Entity], Sequence[entity_lib.ActionSpec]]:
        """Return action specs for next actors while tolerating malformed name lists."""
        entities_by_name = {entity.name: entity for entity in entities}
        next_object_names_string = game_master.act(
            action_spec=entity_lib.ActionSpec(
                call_to_action=self._call_to_next_acting,
                output_type=entity_lib.OutputType.NEXT_ACTING,
                options=tuple(entities_by_name.keys()),
            )
        )
        raw_names = [name.strip() for name in str(next_object_names_string).split(",")]
        next_entity_names: list[str] = []
        for name in raw_names:
            if not name:
                continue
            if name not in entities_by_name:
                _LOGGER.warning(
                    "Ignoring unknown next_acting entity '%s' from game master '%s'.",
                    name,
                    game_master.name,
                )
                continue
            next_entity_names.append(name)

        if log is not None and isinstance(log_entry, dict) and hasattr(game_master, "get_last_log"):
            assert hasattr(game_master, "get_last_log")
            log_entry["next_acting"] = game_master.get_last_log()

        action_spec_by_name: dict[str, entity_lib.ActionSpec] = {}
        for next_entity_name in next_entity_names:
            next_action_spec_string = game_master.act(
                action_spec=entity_lib.ActionSpec(
                    call_to_action=self._call_to_next_action_spec.format(name=next_entity_name),
                    output_type=entity_lib.OutputType.NEXT_ACTION_SPEC,
                )
            )
            action_spec_by_name[next_entity_name] = engine_lib.action_spec_parser(
                next_action_spec_string
            )

            if (
                log is not None
                and isinstance(log_entry, dict)
                and hasattr(game_master, "get_last_log")
            ):
                assert hasattr(game_master, "get_last_log")
                log_entry["next_action_spec"] = game_master.get_last_log()

        return (
            [entities_by_name[entity_name] for entity_name in next_entity_names],
            [action_spec_by_name[entity_name] for entity_name in next_entity_names],
        )

    def _run_single_entity_action(
        self,
        *,
        game_master: entity_lib.Entity,
        entity: entity_lib.Entity,
        action_spec: entity_lib.ActionSpec,
        skip_actions: bool,
        verbose: bool,
        observe_before_action: bool = True,
        return_raw_action: bool = False,
    ) -> str | dict[str, str]:
        """Execute one observe/act/resolve cycle for a single entity."""
        with self._gm_lock(game_master):
            if observe_before_action:
                observation = self.make_observation(game_master, entity)
                if observation and observation.strip():
                    if verbose:
                        print(
                            termcolor.colored(
                                f"Entity {entity.name} observed: {observation}",
                                _PRINT_COLOR,
                            )
                        )
                    entity.observe(observation)

            if skip_actions:
                return {"raw": "", "rendered": ""} if return_raw_action else ""

            if verbose:
                print(
                    termcolor.colored(
                        f"Entity {entity.name} is next to act. They must respond"
                        f' in the format: "{action_spec}".',
                        _PRINT_COLOR,
                    )
                )

            raw_action = entity.act(action_spec)
            raw_text = str(raw_action)
            action = f"{entity.name}: {raw_text}"
            if verbose:
                print(
                    termcolor.colored(f"Entity {entity.name} chose action: {action}", _PRINT_COLOR)
                )

            result = self.agent_resolve(game_master, action, verbose=verbose)
            entity.observe(result)
            if return_raw_action:
                return {"raw": raw_text, "rendered": action}
            return action

    @staticmethod
    def _is_social_media_game_master(game_master: entity_lib.Entity) -> bool:
        act_component = getattr(game_master, "_act_component", None)
        return hasattr(act_component, "sm_app")

    @staticmethod
    def _sync_social_game_master_runtime_state(
        game_master: entity_lib.Entity,
        entities: Sequence[entity_lib.Entity],
        step: int,
    ) -> None:
        """Keep GM-side runtime metadata in sync with current episode state."""
        act_component = getattr(game_master, "_act_component", None)
        sm_app = getattr(act_component, "sm_app", None)
        action_logger = getattr(sm_app, "action_logger", None)
        model = getattr(act_component, "_model", None)

        if action_logger is not None:
            action_logger.episode_idx = step
        if model is not None:
            model.agent_names = [getattr(agent, "_agent_name", agent.name) for agent in entities]
            if callable(getattr(model, "_rebuild_agent_name_index", None)):
                model._rebuild_agent_name_index()
            if isinstance(getattr(model, "meta_data", None), dict):
                model.meta_data["episode_idx"] = step

    @staticmethod
    def _entity_flow_type(game_master: entity_lib.Entity, entity_name: str, cfg: Any) -> str:
        act_component = getattr(game_master, "_act_component", None)
        flow_map = dict(getattr(act_component, "entity_flow_tags", {}) or {})

        configured_map = getattr(
            getattr(getattr(cfg.sim, "engine", object()), "flow_routing", object()),
            "entity_to_flow",
            None,
        )
        if isinstance(configured_map, Mapping):
            for key, value in configured_map.items():
                if str(key).strip():
                    flow_map[str(key).strip()] = str(value).strip() or "default"

        return str(flow_map.get(entity_name, "default")).strip() or "default"

    @classmethod
    def _group_entities_by_flow(
        cls,
        *,
        cfg: Any,
        game_master: entity_lib.Entity,
        entities: Sequence[entity_lib.Entity],
        action_specs: Sequence[entity_lib.ActionSpec],
    ) -> list[tuple[str, list[tuple[entity_lib.Entity, entity_lib.ActionSpec]]]]:
        flow_groups: OrderedDict[str, list[tuple[entity_lib.Entity, entity_lib.ActionSpec]]] = (
            OrderedDict()
        )
        for entity, spec in zip(entities, action_specs, strict=False):
            flow = cls._entity_flow_type(game_master, entity.name, cfg)
            flow_groups.setdefault(flow, []).append((entity, spec))

        configured_order = getattr(
            getattr(getattr(cfg.sim, "engine", object()), "flow_routing", object()),
            "flow_order",
            [],
        )
        if not configured_order:
            return list(flow_groups.items())

        ordered: list[tuple[str, list[tuple[entity_lib.Entity, entity_lib.ActionSpec]]]] = []
        used: set[str] = set()
        for flow in configured_order:
            name = str(flow).strip()
            if name and name in flow_groups and name not in used:
                ordered.append((name, flow_groups[name]))
                used.add(name)

        for name, members in flow_groups.items():
            if name not in used:
                ordered.append((name, members))
        return ordered

    @staticmethod
    def _gm_sequence(game_master: entity_lib.Entity) -> int:
        act_component = getattr(game_master, "_act_component", None)
        orchestration = getattr(act_component, "gm_orchestration", {})
        if not isinstance(orchestration, Mapping):
            return 0
        try:
            return int(orchestration.get("sequence", 0))
        except (TypeError, ValueError):
            return 0

    @staticmethod
    def _gm_owned_flows(game_master: entity_lib.Entity) -> set[str]:
        act_component = getattr(game_master, "_act_component", None)
        orchestration = getattr(act_component, "gm_orchestration", {})
        if not isinstance(orchestration, Mapping):
            return set()
        flows = orchestration.get("owned_flows", [])
        if not isinstance(flows, Sequence):
            return set()
        return {str(flow).strip() for flow in flows if str(flow).strip()}

    @classmethod
    def _phase_game_masters(
        cls,
        *,
        current_game_master: entity_lib.Entity,
        game_masters: Sequence[entity_lib.Entity],
    ) -> list[entity_lib.Entity]:
        del game_masters
        return [current_game_master]

    def _build_flow_task_groups(
        self,
        *,
        cfg: Any,
        phase_batches: Sequence[
            tuple[
                entity_lib.Entity,
                Sequence[entity_lib.Entity],
                Sequence[entity_lib.ActionSpec],
                bool,
            ]
        ],
        entities: Sequence[entity_lib.Entity],
        skip_actions: bool,
        entity_act_fn: Callable[
            [entity_lib.Entity, entity_lib.Entity, entity_lib.ActionSpec, bool], str
        ],
    ) -> tuple[list[tuple[str, dict[str, Callable[[], str]]]], dict[int, Any]]:
        del cfg
        flow_task_groups: list[tuple[str, dict[str, Callable[[], str]]]] = []
        model_pool: dict[int, Any] = {}
        if skip_actions:
            for phase_gm, _, _, _ in phase_batches:
                skip_tasks: dict[str, Callable[[], str]] = {}
                for entity in entities:
                    action_spec = entity_lib.ActionSpec(
                        call_to_action="",
                        output_type=entity_lib.OutputType.SKIP_THIS_STEP,
                    )
                    task_name = f"{phase_gm.name}::{entity.name}"
                    skip_tasks[task_name] = functools.partial(
                        entity_act_fn, phase_gm, entity, action_spec, True
                    )
                flow_task_groups.append((f"{phase_gm.name}:default", skip_tasks))
                for model_obj in collect_unique_models(phase_gm, entities):
                    model_pool[id(model_obj)] = model_obj
            return flow_task_groups, model_pool

        for phase_gm, gm_entities, gm_action_specs, gm_skip in phase_batches:
            entities_to_process = entities if gm_skip else gm_entities
            tasks: dict[str, Callable[[], str]] = {}
            if gm_skip:
                action_iter = [
                    entity_lib.ActionSpec(
                        call_to_action="",
                        output_type=entity_lib.OutputType.SKIP_THIS_STEP,
                    )
                    for _ in entities_to_process
                ]
            else:
                action_iter = list(gm_action_specs)
            for entity, action_spec in zip(entities_to_process, action_iter, strict=False):
                task_name = f"{phase_gm.name}::{entity.name}"
                tasks[task_name] = functools.partial(
                    entity_act_fn, phase_gm, entity, action_spec, False
                )
            flow_task_groups.append((f"{phase_gm.name}:default", tasks))
            for model_obj in collect_unique_models(phase_gm, entities_to_process):
                model_pool[id(model_obj)] = model_obj
        return flow_task_groups, model_pool

    def _build_flow_action_loop_policies(
        self,
        *,
        engine_cfg: Mapping[str, Any],
        default_policy: Any,
    ) -> dict[str, Any]:
        """Build per-flow action-loop policy overrides.

        Base engine intentionally ignores flow-level overrides and uses a single
        global action_loop policy.
        """
        del default_policy
        flow_policies = engine_cfg.get("flow_policies")
        if isinstance(flow_policies, Mapping) and flow_policies:
            _LOGGER.warning(
                "engine.flow_policies is configured but engine preset is base; "
                "per-flow policy overrides are ignored."
            )
        return {}

    @staticmethod
    def _flow_name_for_group(group_name: str) -> str:
        """Extract flow tag from a task-group label."""
        label = str(group_name or "").strip()
        if not label:
            return "default"
        if ":" in label:
            _, flow = label.rsplit(":", 1)
            flow_name = flow.strip()
            return flow_name or "default"
        return label

    def _action_loop_policy_for_group(
        self,
        *,
        group_name: str,
        default_policy: Any,
        flow_policies: Mapping[str, Any],
    ) -> Any:
        """Resolve action-loop policy for a flow task-group label."""
        flow_name = self._flow_name_for_group(group_name)
        return flow_policies.get(flow_name, flow_policies.get("default", default_policy))

    @staticmethod
    def _run_tasks_with_limit(
        tasks: Mapping[str, Callable[[], str]],
        worker_limit: int,
    ) -> dict[str, str]:
        """Run tasks with a bounded thread pool.

        Individual task failures are logged and skipped rather than crashing
        the entire episode — this prevents one agent exhausting its retry
        budget from killing all other in-flight agents.
        """
        if worker_limit >= len(tasks):
            # Fast path: no throttling needed, but still catch per-task errors.
            results: dict[str, str] = {}
            with concurrent.futures.ThreadPoolExecutor(max_workers=len(tasks)) as executor:
                future_to_name = {
                    executor.submit(task_fn): task_name for task_name, task_fn in tasks.items()
                }
                for future in concurrent.futures.as_completed(future_to_name):
                    task_name = future_to_name[future]
                    try:
                        results[task_name] = future.result()
                    except Exception:
                        _LOGGER.exception(
                            "Agent task failed (isolated): agent=%s",
                            task_name,
                        )
                        results[task_name] = ""
            return results

        results = {}
        with concurrent.futures.ThreadPoolExecutor(max_workers=worker_limit) as executor:
            future_to_name = {
                executor.submit(task_fn): task_name for task_name, task_fn in tasks.items()
            }
            for future in concurrent.futures.as_completed(future_to_name):
                task_name = future_to_name[future]
                try:
                    results[task_name] = future.result()
                except Exception:
                    _LOGGER.exception(
                        "Agent task failed (isolated): agent=%s",
                        task_name,
                    )
                    results[task_name] = ""

        return results

    @override
    def run_loop(  # type: ignore[misc]
        self,
        game_masters: Sequence[entity_lib.Entity],
        entities: Sequence[entity_lib.Entity],
        premise: str = "",
        max_steps: int = 100,
        start_step: int = 0,
        verbose: bool = False,
        log: list[Mapping[str, Any]] | None = None,
        checkpoint_callback: Callable[[int], None] | None = None,
    ) -> None:
        """Run a game loop."""
        if not game_masters:
            raise ValueError("No game masters provided.")

        log_entry = _get_empty_log_entry()
        game_master = game_masters[0]
        steps = max(0, int(start_step))
        if premise:
            premise = f"{EVENT_TAG} {premise}"
            game_master.observe(premise)

        # logging setup
        cfg = ConfigStore.get_config()
        engine_cfg = {}
        if hasattr(cfg.sim, "engine") and cfg.sim.engine is not None:
            engine_cfg = cast(dict[str, Any], OmegaConf.to_container(cfg.sim.engine, resolve=True))
        default_action_loop_policy = build_action_loop_policy(engine_cfg.get("action_loop"))
        current_action_loop_policy = default_action_loop_policy
        flow_action_loop_policies = self._build_flow_action_loop_policies(
            engine_cfg=engine_cfg,
            default_policy=default_action_loop_policy,
        )
        probe_schedule_policy = build_probe_schedule_policy(engine_cfg.get("probe_schedule"))
        configured_worker_cap = resolve_configured_worker_cap(cfg)
        probe_event_logger = EventLogger(
            "probe", os.path.join(cfg.sim.output_rootname, "probe_events.jsonl")
        )
        probes_config: Mapping[str, Any] | None = None
        scenario_probes = OmegaConf.select(cfg.scenario, "probes")
        if scenario_probes is not None:
            probes_config = cast(
                Mapping[str, Any],
                OmegaConf.to_container(scenario_probes, resolve=True),
            )
        probe_orchestrator = ProbeDeploymentOrchestrator(probes_config, probe_event_logger)
        _LOGGER.info(
            "Engine run initialized: max_steps=%d total_agents=%d configured_worker_cap=%s",
            max_steps,
            len(entities),
            configured_worker_cap if configured_worker_cap is not None else "none",
        )
        phase_worker_caps: dict[str, int | None] = {"probe": None, "action": None}

        # while not self.terminate(game_master, verbose) and steps < max_steps:
        while steps < max_steps:
            start_time = time.time()
            metrics = SimMetricsCollector.get()
            ep_timings: dict[str, float] = {}
            probe_phase = {
                "deployed": False,
                "total_agents": len(entities),
                "selected_agents": 0,
                "duration_s": 0.0,
                "retry": {
                    "calls": 0,
                    "failed_calls": 0,
                    "retries": 0,
                    "retry_per_call": 0.0,
                    "failure_ratio": 0.0,
                    "models_with_activity": 0,
                },
            }
            _LOGGER.info(
                "Episode %d start: total_agents=%d game_master=%s",
                steps,
                len(entities),
                game_master.name,
            )

            if log is not None and hasattr(game_master, "get_last_log"):
                assert hasattr(game_master, "get_last_log")  # Assertion for pytype
                if (
                    log is not None
                    and log_entry is not None
                    and hasattr(game_master, "get_last_log")
                ):
                    log_entry["next_acting"] = game_master.get_last_log()

            t0 = time.time()
            game_master = self.next_game_master(game_master, game_masters, verbose)
            ep_timings["next_game_master"] = time.time() - t0

            if log is not None and hasattr(game_master, "get_last_log"):
                assert hasattr(game_master, "get_last_log")  # Assertion for pytype
                if (
                    log is not None
                    and log_entry is not None
                    and hasattr(game_master, "get_last_log")
                ):
                    log_entry["next_acting"] = game_master.get_last_log()
            if self._is_social_media_game_master(game_master):
                self._sync_social_game_master_runtime_state(game_master, entities, steps)
                run_probe_phase = probe_schedule_policy.should_run_probe_phase(
                    step=steps,
                    orchestrator=probe_orchestrator,
                )
                if run_probe_phase:
                    probe_models = collect_unique_models(game_master, entities)
                    probe_requested_workers = len(entities)
                    probe_dynamic_cap, probe_worker_limit = compute_dynamic_worker_limit(
                        requested_workers=probe_requested_workers,
                        phase_cap=phase_worker_caps["probe"],
                        configured_worker_cap=configured_worker_cap,
                    )
                    probe_before = capture_retry_counters(probe_models)
                    set_model_retry_phase(probe_models, "probe")
                    _LOGGER.info("Episode %d probe phase start", steps)
                    t0 = time.time()
                    try:
                        deployed, selected_probe_agents = probe_orchestrator.maybe_deploy(
                            step=steps,
                            agents=entities,
                            worker_limit=probe_worker_limit,
                        )
                    finally:
                        set_model_retry_phase(probe_models, "other")
                    probe_duration = time.time() - t0
                    ep_timings["probe_deployment"] = probe_duration
                    probe_after = capture_retry_counters(probe_models)
                    probe_retry = summarize_retry_delta(probe_before, probe_after)
                    probe_phase = {
                        "deployed": deployed,
                        "total_agents": len(entities),
                        "selected_agents": selected_probe_agents,
                        "requested_workers": probe_requested_workers,
                        "dynamic_worker_cap": probe_dynamic_cap,
                        "worker_limit": probe_worker_limit,
                        "duration_s": round(probe_duration, 4),
                        "retry": probe_retry,
                    }
                    _LOGGER.info(
                        (
                            "Episode %d probe phase: deployed=%s selected_agents=%d dynamic_cap=%d effective_workers=%d duration=%.2fs "
                            "calls=%d retries=%d retry_per_call=%.3f failures=%d"
                        ),
                        steps,
                        deployed,
                        selected_probe_agents,
                        probe_dynamic_cap,
                        probe_worker_limit,
                        probe_duration,
                        probe_retry["calls"],
                        probe_retry["retries"],
                        probe_retry["retry_per_call"],
                        probe_retry["failed_calls"],
                    )
                    phase_worker_caps["probe"] = update_adaptive_worker_cap(
                        previous_cap=phase_worker_caps["probe"],
                        requested_workers=max(1, selected_probe_agents),
                        calls=probe_retry["calls"],
                        retry_per_call=probe_retry["retry_per_call"],
                        failure_ratio=probe_retry["failure_ratio"],
                    )
                    if deployed:
                        print(f"Episode: {steps}. Probe deployment complete")
                        _LOGGER.info("Episode %d probe deployment complete", steps)
                else:
                    ep_timings["probe_deployment"] = 0.0
                    probe_phase["deployed"] = False
                    probe_phase["selected_agents"] = 0
                    probe_phase["duration_s"] = 0.0

            phase_game_masters = [game_master]
            if self._is_social_media_game_master(game_master):
                phase_game_masters = self._phase_game_masters(
                    current_game_master=game_master,
                    game_masters=game_masters,
                )

            t0 = time.time()
            phase_batches: list[
                tuple[
                    entity_lib.Entity,
                    Sequence[entity_lib.Entity],
                    Sequence[entity_lib.ActionSpec],
                    bool,
                ]
            ] = []
            for phase_gm in phase_game_masters:
                if phase_gm is not game_master and self._is_social_media_game_master(phase_gm):
                    self._sync_social_game_master_runtime_state(phase_gm, entities, steps)
                gm_entities, gm_action_specs = self.next_acting(
                    phase_gm, entities, log_entry=log_entry, log=log
                )
                gm_skip = bool(gm_action_specs) and (
                    gm_action_specs[0].output_type == entity_lib.OutputType.SKIP_THIS_STEP
                )
                phase_batches.append((phase_gm, gm_entities, gm_action_specs, gm_skip))
            ep_timings["next_acting"] = time.time() - t0

            skip_actions = any(batch[3] for batch in phase_batches)
            active_agents = sum(len(batch[1]) for batch in phase_batches if not batch[3])
            _LOGGER.info(
                "Episode %d actor selection: active_agents=%d skip_actions=%s phase_gms=%d",
                steps,
                active_agents,
                skip_actions,
                len(phase_game_masters),
            )

            def _entity_act(
                target_game_master: entity_lib.Entity,
                entity: entity_lib.Entity,
                action_spec: entity_lib.ActionSpec,
                skip_actions: bool = False,
            ) -> str:
                """Execute entity action chunk via configured action-loop policy."""
                if log is not None and hasattr(target_game_master, "get_last_log"):
                    assert hasattr(target_game_master, "get_last_log")  # Assertion for pytype
                    log_entry["make_observation"][entity.name] = target_game_master.get_last_log()

                return cast(
                    str,
                    current_action_loop_policy.run(
                        engine=self,
                        game_master=target_game_master,
                        entity=entity,
                        action_spec=action_spec,
                        skip_actions=skip_actions,
                        verbose=verbose,
                    ),
                )

            flow_task_groups, model_pool = self._build_flow_task_groups(
                cfg=cfg,
                phase_batches=phase_batches,
                entities=entities,
                skip_actions=skip_actions,
                entity_act_fn=_entity_act,
            )

            # Run entity actions concurrently with adaptive worker throttling.
            requested_workers = max(1, sum(len(tasks) for _, tasks in flow_task_groups))
            models = list(model_pool.values())
            dynamic_worker_limit, worker_limit = compute_dynamic_worker_limit(
                requested_workers=requested_workers,
                phase_cap=phase_worker_caps["action"],
                configured_worker_cap=configured_worker_cap,
            )

            retry_telemetry = collect_retry_telemetry(
                models,
                requested_workers,
                phase="action",
            )
            _LOGGER.info(
                (
                    "Episode %d workers: requested=%d dynamic_cap=%d configured_cap=%s "
                    "effective=%d model_count=%d retry_avg=%.3f failure_ratio=%.3f"
                ),
                steps,
                requested_workers,
                dynamic_worker_limit,
                configured_worker_cap if configured_worker_cap is not None else "none",
                worker_limit,
                retry_telemetry["model_count"],
                retry_telemetry["retry_avg"],
                retry_telemetry["failure_ratio"],
            )
            top_models = sorted(
                retry_telemetry["per_model"],
                key=lambda item: (item["failure_ratio"], item["retry_avg"]),
                reverse=True,
            )[:3]
            for model_snapshot in top_models:
                _LOGGER.info(
                    (
                        "Episode %d retry_model: model=%s samples=%d retry_avg=%.3f "
                        "failure_ratio=%.3f max_retries=%s"
                    ),
                    steps,
                    model_snapshot["model"],
                    model_snapshot["retry_samples"],
                    model_snapshot["retry_avg"],
                    model_snapshot["failure_ratio"],
                    model_snapshot["max_retries"]
                    if model_snapshot["max_retries"] is not None
                    else "n/a",
                )

            action_before = capture_retry_counters(models)
            set_model_retry_phase(models, "action")
            _LOGGER.info("Episode %d action phase start", steps)
            t0 = time.time()
            try:
                actions: dict[str, str] = {}
                for flow_name, tasks in flow_task_groups:
                    if not tasks:
                        continue
                    current_action_loop_policy = self._action_loop_policy_for_group(
                        group_name=flow_name,
                        default_policy=default_action_loop_policy,
                        flow_policies=flow_action_loop_policies,
                    )
                    policy_name = getattr(
                        current_action_loop_policy,
                        "name",
                        current_action_loop_policy.__class__.__name__,
                    )
                    _LOGGER.info(
                        "Episode %d flow '%s': executing %d entities (policy=%s)",
                        steps,
                        flow_name,
                        len(tasks),
                        policy_name,
                    )
                    actions.update(self._run_tasks_with_limit(tasks, worker_limit))
            finally:
                set_model_retry_phase(models, "other")
            action_duration = time.time() - t0
            ep_timings["entity_actions"] = action_duration
            action_after = capture_retry_counters(models)
            action_retry = summarize_retry_delta(action_before, action_after)
            action_phase = {
                "active_agents": 0 if skip_actions else active_agents,
                "duration_s": round(action_duration, 4),
                "retry": action_retry,
            }
            retry_telemetry = collect_retry_telemetry(
                models,
                requested_workers,
                phase="action",
            )
            _LOGGER.info(
                (
                    "Episode %d action phase: duration=%.2fs active_agents=%d "
                    "calls=%d retries=%d retry_per_call=%.3f failures=%d"
                ),
                steps,
                action_duration,
                action_phase["active_agents"],
                action_retry["calls"],
                action_retry["retries"],
                action_retry["retry_per_call"],
                action_retry["failed_calls"],
            )
            phase_worker_caps["action"] = update_adaptive_worker_cap(
                previous_cap=phase_worker_caps["action"],
                requested_workers=requested_workers,
                calls=action_retry["calls"],
                retry_per_call=action_retry["retry_per_call"],
                failure_ratio=action_retry["failure_ratio"],
            )
            _LOGGER.info(
                "Episode %d action dynamic worker cap updated: %s",
                steps,
                phase_worker_caps["action"] if phase_worker_caps["action"] is not None else "none",
            )

            if worker_limit != requested_workers:
                print(f"Dynamic worker throttle active: {worker_limit}/{requested_workers} workers")
                _LOGGER.info(
                    "Episode %d worker throttle active: %d/%d",
                    steps,
                    worker_limit,
                    requested_workers,
                )

            if skip_actions:
                steps += 1
                duration = time.time() - start_time
                print(f"Episode {steps - 1} finished in {duration:.2f}s")
                append_episode_run_stats(
                    output_rootname=cfg.sim.output_rootname,
                    episode=steps - 1,
                    duration_s=duration,
                    requested_workers=requested_workers,
                    dynamic_worker_cap=dynamic_worker_limit,
                    configured_worker_cap=configured_worker_cap,
                    worker_limit=worker_limit,
                    probe_dynamic_worker_cap=int(
                        cast(Any, probe_phase.get("dynamic_worker_cap", 0))
                    ),
                    probe_worker_limit=int(cast(Any, probe_phase.get("worker_limit", 0))),
                    retry_telemetry=retry_telemetry,
                    phase_timings=ep_timings,
                    probe_phase=probe_phase,
                    action_phase=action_phase,
                )
                # Log metrics for skip episode
                metrics.log_episode(
                    episode=steps - 1,
                    duration_s=round(duration, 4),
                    total_agents=len(entities),
                    active_agents=0,
                    skipped=True,
                    game_master=game_master.name,
                    worker_limit=worker_limit,
                    requested_workers=requested_workers,
                    phase_timings=ep_timings,
                    configured_worker_cap=configured_worker_cap,
                    retry_telemetry=retry_telemetry,
                    probe_phase=probe_phase,
                    action_phase=action_phase,
                )
                phase_summary = ", ".join(
                    f"{phase}={elapsed:.2f}s" for phase, elapsed in sorted(ep_timings.items())
                )
                _LOGGER.info(
                    (
                        "Episode %d complete (skip): duration=%.2fs total_agents=%d "
                        "active_agents=0 timings=[%s]"
                    ),
                    steps - 1,
                    duration,
                    len(entities),
                    phase_summary,
                )
                metrics.snapshot_resources(label=f"episode_{steps - 1}_end")
                continue

            entity_logs = {}
            entity_by_name = {e.name: e for e in entities}
            for entity_name in actions:
                raw_entity_name = entity_name.split("::", 1)[-1]
                entity = entity_by_name.get(raw_entity_name)
                if entity is not None and hasattr(entity, "get_last_log"):
                    entity_logs[entity.name] = entity.get_last_log()

            steps += 1
            if log is not None:
                game_master_key = "+".join(gm.name for gm in phase_game_masters)
                self._log(
                    log=log,
                    steps=steps,
                    entity_logs=entity_logs,
                    game_master_key=game_master_key,
                    game_master_log=log_entry,
                )
                log_entry = _get_empty_log_entry()

            t0 = time.time()
            if checkpoint_callback is not None:
                checkpoint_callback(steps)
            ep_timings["checkpoint"] = time.time() - t0

            duration = time.time() - start_time
            print(f"Episode {steps - 1} finished in {duration:.2f}s")
            append_episode_run_stats(
                output_rootname=cfg.sim.output_rootname,
                episode=steps - 1,
                duration_s=duration,
                requested_workers=requested_workers,
                dynamic_worker_cap=dynamic_worker_limit,
                configured_worker_cap=configured_worker_cap,
                worker_limit=worker_limit,
                probe_dynamic_worker_cap=int(cast(Any, probe_phase.get("dynamic_worker_cap", 0))),
                probe_worker_limit=int(cast(Any, probe_phase.get("worker_limit", 0))),
                retry_telemetry=retry_telemetry,
                phase_timings=ep_timings,
                probe_phase=probe_phase,
                action_phase=action_phase,
            )

            # Log comprehensive per-episode metrics
            active_name_set: set[str] = set()
            for _phase_gm, gm_entities, _gm_action_specs, gm_skip in phase_batches:
                if gm_skip:
                    continue
                for entity in gm_entities:
                    active_name_set.add(entity.name)
            active_names = sorted(active_name_set)
            metrics.log_episode(
                episode=steps - 1,
                duration_s=round(duration, 4),
                total_agents=len(entities),
                active_agents=len(active_names),
                active_agent_names=active_names,
                skipped=False,
                game_master=game_master.name,
                worker_limit=worker_limit,
                requested_workers=requested_workers,
                phase_timings=ep_timings,
                configured_worker_cap=configured_worker_cap,
                retry_telemetry=retry_telemetry,
                probe_phase=probe_phase,
                action_phase=action_phase,
            )
            phase_summary = ", ".join(
                f"{phase}={elapsed:.2f}s" for phase, elapsed in sorted(ep_timings.items())
            )
            _LOGGER.info(
                (
                    "Episode %d complete: duration=%.2fs total_agents=%d active_agents=%d "
                    "timings=[%s]"
                ),
                steps - 1,
                duration,
                len(entities),
                len(active_names),
                phase_summary,
            )
            metrics.snapshot_resources(label=f"episode_{steps - 1}_end")


class FlowSocialMediaEngine(BaseSocialMediaEngine):
    """Flow-enabled social media engine with multi-GM phase orchestration."""

    @classmethod
    def _phase_game_masters(
        cls,
        *,
        current_game_master: entity_lib.Entity,
        game_masters: Sequence[entity_lib.Entity],
    ) -> list[entity_lib.Entity]:
        sequence = cls._gm_sequence(current_game_master)
        peers = [
            gm
            for gm in game_masters
            if cls._gm_sequence(gm) == sequence and cls._is_social_media_game_master(gm)
        ]
        if not peers:
            return [current_game_master]
        peers.sort(key=lambda gm: gm.name)
        return peers

    def _build_flow_task_groups(
        self,
        *,
        cfg: Any,
        phase_batches: Sequence[
            tuple[
                entity_lib.Entity,
                Sequence[entity_lib.Entity],
                Sequence[entity_lib.ActionSpec],
                bool,
            ]
        ],
        entities: Sequence[entity_lib.Entity],
        skip_actions: bool,
        entity_act_fn: Callable[
            [entity_lib.Entity, entity_lib.Entity, entity_lib.ActionSpec, bool], str
        ],
    ) -> tuple[list[tuple[str, dict[str, Callable[[], str]]]], dict[int, Any]]:
        flow_task_groups: list[tuple[str, dict[str, Callable[[], str]]]] = []
        model_pool: dict[int, Any] = {}
        if skip_actions:
            return super()._build_flow_task_groups(
                cfg=cfg,
                phase_batches=phase_batches,
                entities=entities,
                skip_actions=skip_actions,
                entity_act_fn=entity_act_fn,
            )

        for phase_gm, gm_entities, gm_action_specs, gm_skip in phase_batches:
            entities_to_process = entities if gm_skip else gm_entities
            owned_flows = self._gm_owned_flows(phase_gm)
            grouped_entities = self._group_entities_by_flow(
                cfg=cfg,
                game_master=phase_gm,
                entities=entities_to_process,
                action_specs=gm_action_specs,
            )
            for flow_name, members in grouped_entities:
                if owned_flows and flow_name not in owned_flows:
                    continue
                flow_tasks: dict[str, Callable[[], str]] = {}
                for entity, action_spec in members:
                    task_name = f"{phase_gm.name}::{entity.name}"
                    flow_tasks[task_name] = functools.partial(
                        entity_act_fn,
                        phase_gm,
                        entity,
                        action_spec,
                        False,
                    )
                flow_task_groups.append((f"{phase_gm.name}:{flow_name}", flow_tasks))
            for model_obj in collect_unique_models(phase_gm, entities_to_process):
                model_pool[id(model_obj)] = model_obj
        return flow_task_groups, model_pool

    @override
    def _build_flow_action_loop_policies(
        self,
        *,
        engine_cfg: Mapping[str, Any],
        default_policy: Any,
    ) -> dict[str, Any]:
        """Build per-flow action-loop policies from engine.flow_policies."""
        del default_policy
        flow_policies_cfg = engine_cfg.get("flow_policies")
        if not isinstance(flow_policies_cfg, Mapping) or not flow_policies_cfg:
            return {}

        policies: dict[str, Any] = {}
        for flow_name, slot_cfg in flow_policies_cfg.items():
            key = str(flow_name).strip()
            if not key:
                continue
            if not isinstance(slot_cfg, Mapping):
                _LOGGER.warning(
                    "Skipping engine.flow_policies['%s']: expected mapping, got %s.",
                    key,
                    type(slot_cfg).__name__,
                )
                continue
            try:
                policies[key] = build_action_loop_policy(cast(Mapping[str, Any], slot_cfg))
            except Exception:
                _LOGGER.exception(
                    "Failed building engine.flow_policies['%s']; default action_loop will be used.",
                    key,
                )
        return policies
