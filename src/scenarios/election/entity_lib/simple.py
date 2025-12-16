"""A prefab implementing an entity with a minimal set of components."""

import dataclasses
from collections.abc import Mapping

from concordia.agents import entity_agent_with_logging
from concordia.associative_memory import basic_associative_memory
from concordia.components import agent as agent_components
from concordia.language_model import language_model
from concordia.typing import prefab as prefab_lib

DEFAULT_INSTRUCTIONS_COMPONENT_KEY = "Instructions"
DEFAULT_INSTRUCTIONS_PRE_ACT_LABEL = "\nInstructions"
DEFAULT_GOAL_COMPONENT_KEY = "Goal"


@dataclasses.dataclass
class Entity(prefab_lib.Prefab):
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
        model: language_model.LanguageModel,
        memory_bank: basic_associative_memory.AssociativeMemoryBank,
    ) -> entity_agent_with_logging.EntityAgentWithLogging:
        """Build an agent.

        Args:
          model: The language model to use.
          memory_bank: The agent's memory_bank object.

        Returns
        -------
          An entity.
        """
        agent_name = self.params.get("name", "Alice")

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

        act_component = agent_components.concat_act_component.ConcatActComponent(
            model=model,
            component_order=component_order,
            randomize_choices=False,
        )

        agent = entity_agent_with_logging.EntityAgentWithLogging(
            agent_name=agent_name,
            act_component=act_component,
            context_components=components_of_agent,
        )

        return agent
