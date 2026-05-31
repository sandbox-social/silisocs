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

"""A simple acting component that outputs scripted text directly."""

import dataclasses
from collections.abc import Mapping

from concordia.agents.entity_agent_with_logging import EntityAgentWithLogging as ComponentEntity
from concordia.associative_memory.basic_associative_memory import AssociativeMemoryBank
from concordia.components.agent.scripted_act import ScriptedActComponent
from concordia.typing import entity as concordia_entity
from concordia.typing import entity_component
from concordia.typing.prefab import Prefab
from typing_extensions import override

from silisocs.runtime.language_models import LanguageModel

ComponentContextMapping = dict[str, entity_component.ContextComponent]
ActionSpec = concordia_entity.ActionSpec

DEFAULT_INSTRUCTIONS_COMPONENT_KEY = "Instructions"
DEFAULT_INSTRUCTIONS_PRE_ACT_LABEL = "\nInstructions"
DEFAULT_GOAL_COMPONENT_KEY = "Goal"


@dataclasses.dataclass
class Entity(Prefab):
    """A prefab implementing an entity with a minimal set of components."""

    description: str = "A minimalist entity"
    params: Mapping[str, str] = dataclasses.field(
        default_factory=lambda: {
            "name": "Alice",
            "goal": "",
            "election_info": "",
        }
    )

    def build(
        self,
        model: LanguageModel,
        memory_bank: AssociativeMemoryBank,
    ) -> ComponentEntity:
        """Build an agent.

        Args:
          model: The language model to use.
          memory_bank: The agent's memory_bank object.

        Returns
        -------
          An entity.
        """
        agent_name = self.params.get("name", "Alice")
        randomize_choices = self.params.get("randomize_choices", True)
        election_info = self.params.get("election_info", "")

        instructions = agent_components.instructions.Instructions(
            agent_name=agent_name,
            pre_act_label=DEFAULT_INSTRUCTIONS_PRE_ACT_LABEL,
        )

        election_info_key = "Election Information"
        election_information = agent_components.constant.Constant(
            state=(election_info),
            pre_act_label="Scenario Information\n",
        )

        observation_to_memory = agent_components.observation.ObservationToMemory()

        observation_label = "\nObservation"
        observation = agent_components.observation.LastNObservations(
            history_length=100, pre_act_label=observation_label
        )

        components_of_agent = {
            DEFAULT_INSTRUCTIONS_COMPONENT_KEY: instructions,
            election_info_key: election_information,
            "observation_to_memory": observation_to_memory,
            agent_components.observation.DEFAULT_OBSERVATION_COMPONENT_KEY: (observation),
            agent_components.memory.DEFAULT_MEMORY_COMPONENT_KEY: (
                agent_components.memory.AssociativeMemory(memory_bank=memory_bank)
            ),
        }

        component_order = list(components_of_agent.keys())

        if self.params.get("goal", ""):
            goal_key = DEFAULT_GOAL_COMPONENT_KEY
            goal = agent_components.constant.Constant(
                state=self.params.get("goal", ""),
                pre_act_label="Overarching goal",
            )
            components_of_agent[goal_key] = goal
            # Place goal after the instructions.
            component_order.insert(1, goal_key)

        act_component = SimpleScriptedActComponent(
            model=model,
            component_order=component_order,
            script=self.params.get("script", []),
        )

        agent = ComponentEntity(
            agent_name=agent_name,
            act_component=act_component,
            context_components=components_of_agent,
        )

        return agent


class SimpleScriptedActComponent(ScriptedActComponent):
    """An acting component that outputs scripted text directly without LLM calls.

    This component simplifies the parent class by removing LLM calls and context
    aggregation, directly returning the next scripted line for the entity.
    """

    @override
    def get_action_attempt(  # type: ignore[misc]
        self,
        contexts: ComponentContextMapping,
        action_spec: ActionSpec,
    ) -> str:
        # Initialize lines from script if not already done
        if not self._lines:
            for line in self._script:
                if line["name"] == self.get_entity().name:
                    self._lines.append(line["line"])

        # Prepare output with optional entity name prefix
        output = ""
        if self._prefix_entity_name:
            output = self.get_entity().name + " "

        # Return the next scripted line or empty string if script is exhausted
        if self._line_index < len(self._lines):
            output += self._lines[self._line_index]
            self._line_index += 1
            return output
        return ""
