# Make sure to import the original engine and any other tools you need
import concurrent.futures
import functools
import logging
import os
import time
from collections.abc import Callable, Mapping, Sequence
from typing import Any, Literal, cast

import termcolor
from concordia.components.game_master import event_resolution as event_resolution_components
from concordia.components.game_master import next_acting as next_acting_components
from concordia.components.game_master import switch_act as switch_act_component
from concordia.environment.engines import simultaneous
from concordia.typing import entity as entity_lib
from omegaconf import OmegaConf
from typing_extensions import override

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


def _get_empty_log_entry():
    """Returns a dictionary to store a single log entry."""
    return {
        "terminate": {},
        "next_game_master": {},
        "make_observation": {},
        "next_acting": {},
        "next_action_spec": {},
        "resolve": {},
    }


class SocialMediaEngine(simultaneous.Simultaneous):
    """
    A custom engine to implement parallel social media sessions, where agents can act parallely
    """

    def agent_resolve(self, game_master, action, verbose=False):
        """Resolve an entity's action."""
        result = game_master.act(
            action_spec=entity_lib.ActionSpec(
                call_to_action=action,
                output_type=entity_lib.OutputType.RESOLVE,
            )
        )
        if verbose:
            print(termcolor.colored(f"The resolved event was: {result}", _PRINT_COLOR))
        return result

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
        verbose: bool = False,
        log: list[Mapping[str, Any]] | None = None,
        checkpoint_callback: Callable[[int], None] | None = None,
    ) -> None:
        """Run a game loop."""
        if not game_masters:
            raise ValueError("No game masters provided.")

        log_entry = _get_empty_log_entry()
        game_master = game_masters[0]
        steps = 0
        if premise:
            premise = f"{EVENT_TAG} {premise}"
            game_master.observe(premise)

        # logging setup
        cfg = ConfigStore.get_config()
        configured_worker_cap = resolve_configured_worker_cap(cfg)
        probe_event_logger = EventLogger(
            "probe", os.path.join(cfg.sim.output_rootname, "probe_events.jsonl")
        )
        probes_config = cast(
            Mapping[str, Any] | None,
            OmegaConf.to_container(cfg.scenario.probes, resolve=True),
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

            t0 = time.time()
            next_entities, next_action_specs = self.next_acting(
                game_master, entities, log_entry=log_entry, log=log
            )
            ep_timings["next_acting"] = time.time() - t0

            if next_action_specs[0].output_type == entity_lib.OutputType.SKIP_THIS_STEP:
                if verbose:
                    print(
                        termcolor.colored(
                            "\nSkipping the action phase for the current time step.\n"
                        )
                    )
                skip_actions = True
                if checkpoint_callback is not None:
                    print(f"Calling checkpoint callback at step {steps}")
                    checkpoint_callback(steps)
            else:
                skip_actions = False
            _LOGGER.info(
                "Episode %d actor selection: active_agents=%d skip_actions=%s",
                steps,
                len(next_entities),
                skip_actions,
            )

            def _entity_act(
                entity: entity_lib.Entity,
                action_spec: entity_lib.ActionSpec,
                skip_actions: bool = False,
            ) -> str:
                """Make initial observation, then conduct social-media actions till agent decides to terminate step or max_actions is reached"""
                observation = self.make_observation(game_master, entity)
                if log is not None and hasattr(game_master, "get_last_log"):
                    assert hasattr(game_master, "get_last_log")  # Assertion for pytype
                    log_entry["make_observation"][entity.name] = game_master.get_last_log()
                # Only observe if the observation is not an empty or whitespace string
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
                    return ""

                if verbose:
                    print(
                        termcolor.colored(
                            f"Entity {entity.name} is next to act. They must respond "
                            f' in the format: "{action_spec}".',
                            _PRINT_COLOR,
                        )
                    )

                raw_action = entity.act(action_spec)
                action = f"{entity.name}: {raw_action}"
                if verbose:
                    print(
                        termcolor.colored(
                            f"Entity {entity.name} chose action: {action}", _PRINT_COLOR
                        )
                    )

                result = self.agent_resolve(game_master, action, verbose=verbose)
                entity.observe(result)

                return action

            tasks = {}
            entities_to_process = entities if skip_actions else next_entities
            for i, entity in enumerate(entities_to_process):
                if skip_actions:
                    action_spec = entity_lib.ActionSpec(
                        call_to_action="",
                        output_type=entity_lib.OutputType.SKIP_THIS_STEP,
                    )
                else:
                    action_spec = next_action_specs[i]
                tasks[entity.name] = functools.partial(
                    _entity_act, entity, action_spec, skip_actions
                )

            # Run entity actions concurrently with adaptive worker throttling.
            requested_workers = len(tasks)
            models = collect_unique_models(game_master, entities_to_process)
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
                actions = self._run_tasks_with_limit(tasks, worker_limit)
            finally:
                set_model_retry_phase(models, "other")
            action_duration = time.time() - t0
            ep_timings["entity_actions"] = action_duration
            action_after = capture_retry_counters(models)
            action_retry = summarize_retry_delta(action_before, action_after)
            action_phase = {
                "active_agents": 0 if skip_actions else len(next_entities),
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
                entity = entity_by_name.get(entity_name)
                if entity is not None and hasattr(entity, "get_last_log"):
                    entity_logs[entity.name] = entity.get_last_log()

            steps += 1
            if log is not None:
                game_master_key = game_master.name
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
            active_names = [e.name for e in next_entities]
            metrics.log_episode(
                episode=steps - 1,
                duration_s=round(duration, 4),
                total_agents=len(entities),
                active_agents=len(next_entities),
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
                len(next_entities),
                phase_summary,
            )
            metrics.snapshot_resources(label=f"episode_{steps - 1}_end")
