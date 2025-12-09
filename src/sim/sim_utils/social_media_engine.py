# Make sure to import the original engine and any other tools you need
import functools
import os
import random
from collections.abc import Callable, Mapping, Sequence
from typing import Any

import termcolor
from concordia.components.game_master import event_resolution as event_resolution_components
from concordia.components.game_master import next_acting as next_acting_components
from concordia.components.game_master import switch_act as switch_act_component
from concordia.environment import engine as engine_lib
from concordia.environment.engines import simultaneous
from concordia.typing import entity as entity_lib
from concordia.utils import concurrency
from omegaconf import OmegaConf
from typing_extensions import override

from sim.sim_utils.agent_speech_utils import (
    deploy_probes,
)
from sim.sim_utils.misc_sim_utils import ConfigStore, EventLogger

DEFAULT_CALL_TO_MAKE_OBSERVATION = "{name}"
DEFAULT_CALL_TO_NEXT_ACTING = "Which entities act next?"
DEFAULT_CALL_TO_NEXT_ACTION_SPEC = next_acting_components.DEFAULT_CALL_TO_NEXT_ACTION_SPEC
DEFAULT_CALL_TO_RESOLVE = "Because of all that came before, what happens next?"
DEFAULT_CALL_TO_CHECK_TERMINATION = "Is the game/simulation finished?"
DEFAULT_CALL_TO_NEXT_GAME_MASTER = "Which rule set should we use for the next step?"

DEFAULT_ACT_COMPONENT_KEY = switch_act_component.DEFAULT_ACT_COMPONENT_KEY

PUTATIVE_EVENT_TAG = event_resolution_components.PUTATIVE_EVENT_TAG
EVENT_TAG = event_resolution_components.EVENT_TAG

from typing import Literal

_PRINT_COLOR: Literal["cyan"] = "cyan"


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
        probe_event_logger = EventLogger(
            "probe", os.path.join(cfg.sim.output_rootname, "probe_events.jsonl")
        )
        probes_config = OmegaConf.to_container(cfg.probes, resolve=True)

        # while not self.terminate(game_master, verbose) and steps < max_steps:
        while steps < max_steps:
            if log is not None and hasattr(game_master, "get_last_log"):
                assert hasattr(game_master, "get_last_log")  # Assertion for pytype
                if (
                    log is not None
                    and log_entry is not None
                    and hasattr(game_master, "get_last_log")
                ):
                    log_entry["next_acting"] = game_master.get_last_log()

            game_master = self.next_game_master(game_master, game_masters, verbose)
            if log is not None and hasattr(game_master, "get_last_log"):
                assert hasattr(game_master, "get_last_log")  # Assertion for pytype
                if (
                    log is not None
                    and log_entry is not None
                    and hasattr(game_master, "get_last_log")
                ):
                    log_entry["next_acting"] = game_master.get_last_log()
            print(game_master.name)
            if steps > 0:
                game_master._act_component.sm_app.action_logger.episode_idx = steps
                game_master._act_component._model.agent_names = [
                    agent._agent_name for agent in entities
                ]
                game_master._act_component._model.meta_data["episode_idx"] = steps
                probe_event_logger.episode_idx = steps
                print(f"Episode: {steps}. Deploying survey...", end="")
                deploy_probes(
                    entities,  # [agent for agent in entities if roles[agent._agent_name] != "exogenous"], # python src/sim/main_new.py "use_news_agent=None"
                    probes_config,
                    probe_event_logger,
                )
                print("complete")

            next_entities, next_action_specs = self.next_acting(
                game_master, entities, log_entry=log_entry, log=log
            )

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

                self.agent_resolve(game_master, action, verbose=verbose)

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

            # Run entity actions concurrently
            actions = concurrency.run_tasks(tasks)

            if skip_actions:
                steps += 1
                continue

            entity_logs = {}
            for entity_name in actions:
                entity = next(e for e in entities if e.name == entity_name)
                if hasattr(entity, "get_last_log"):
                    assert hasattr(entity, "get_last_log")  # Assertion for pytype
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
            if checkpoint_callback is not None:
                checkpoint_callback(steps)

    @override
    def next_acting(
        self,
        game_master: entity_lib.Entity,
        entities: Sequence[entity_lib.Entity],
        log_entry: dict[str, Any] | None = None,
        log: list[Mapping[str, Any]] | None = None,
    ) -> tuple[
        Sequence[entity_lib.Entity], Sequence[entity_lib.ActionSpec]
    ]:  # pytype: disable=signature-mismatch
        """Return the next action spec for an entity."""
        entities_by_name = {entity.name: entity for entity in entities}
        if game_master._agent_name == "social_media":
            next_entity_names = [
                agent._agent_name
                for agent in entities
                if game_master.active_rates[agent._agent_name] > random.random()
            ]
        else:
            next_object_names_string = game_master.act(
                action_spec=entity_lib.ActionSpec(
                    call_to_action=self._call_to_next_acting,
                    output_type=entity_lib.OutputType.NEXT_ACTING,
                    options=tuple(entities_by_name.keys()),
                )
            )
        next_entity_names = next_object_names_string.split(",")
        if log is not None and hasattr(game_master, "get_last_log"):
            assert hasattr(game_master, "get_last_log")  # Assertion for pytype
            if log is not None and log_entry is not None and hasattr(game_master, "get_last_log"):
                log_entry["next_acting"] = game_master.get_last_log()

        action_spec_by_name = {}
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

            if log is not None and hasattr(game_master, "get_last_log"):
                assert hasattr(game_master, "get_last_log")  # Assertion for pytype
                if (
                    log is not None
                    and log_entry is not None
                    and hasattr(game_master, "get_last_log")
                ):
                    log_entry["next_acting"] = game_master.get_last_log()
        return (
            [entities_by_name[entity_name] for entity_name in next_entity_names],
            [action_spec_by_name[entity_name] for entity_name in next_entity_names],
        )
