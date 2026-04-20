# Copyright 2023 DeepMind Technologies Limited.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.


"""A component that runs the phone scene when a phone action is detected."""

from typing import Literal

from concordia.agents import entity_agent_with_logging
from concordia.associative_memory.deprecated import associative_memory, blank_memories
from concordia.clocks import game_clock
from concordia.language_model import language_model
from concordia.typing.deprecated import component
from sim.sim_utils.misc_sim_utils import ConfigStore

from mastodon_sim.concordia_deprecated.components import apps, logging, scene


class BasicSceneTriggeringComponent(component.Component):
    """Runs the phone scene when a phone action is detected."""

    def __init__(  # noqa: PLR0913
        self,
        player: entity_agent_with_logging.EntityAgentWithLogging,
        phone: apps.Phone,
        model: language_model.LanguageModel,
        memory: associative_memory.AssociativeMemory,
        clock: game_clock.MultiIntervalClock,
        memory_factory: blank_memories.MemoryFactory,
        log_color: Literal[
            "black",
            "grey",
            "red",
            "green",
            "yellow",
            "blue",
            "magenta",
            "cyan",
            "light_grey",
            "dark_grey",
            "light_red",
            "light_green",
            "light_yellow",
            "light_blue",
            "light_magenta",
            "light_cyan",
            "white",
        ]
        | None = "magenta",
        verbose: bool = False,
        semi_verbose: bool = True,
    ):
        self._player = player
        self._phone = phone
        self._model = model
        self._clock = clock
        self._memory_factory = memory_factory
        self._memory = memory
        self._logger = logging.Logger(log_color, verbose, semi_verbose)
        cfg = ConfigStore.get_config()
        self._max_steps = cfg.soc_sys.max_inepisode_tries

    def name(self):  # noqa: D102
        return "State of phone"

    def _run_phone_scene(self, player: entity_agent_with_logging.EntityAgentWithLogging):
        print("Starting phone scene")
        phone_scene = scene.build(
            player,
            self._phone,
            clock=self._clock,
            model=self._model,
            memory_factory=self._memory_factory,
        )
        print("Built phone scene")
        with self._clock.higher_gear():
            scene_output = phone_scene.run_episode(max_steps=self._max_steps)
        self._phone.apps[0].action_logger.log(
            [
                {"source_user": player._agent_name, "label": "inner_actions", "data": action}
                for action in phone_scene._components["PhoneComponent"]._state
            ]
        )
        for event in scene_output:
            player.observe(f"[Conducted action] {event}")
            self._memory.add(event)
        return scene_output

    def update_after_event(self, event_statement: str | None = None):  # noqa: D102
        player = self._player
        if event_statement:
            player.observe(f"[Planned Actions for upcoming Phone Usage]: {event_statement}")
        if player is not None:
            self._run_phone_scene(player)

    def partial_state(self, player_name: str):  # noqa: D102
        return self.phone.description()
