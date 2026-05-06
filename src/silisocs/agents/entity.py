"""Base agent entity prefab — minimal, scenario-agnostic agent.

This is the standard agent that all scenarios can use directly or extend.
It wires: Instructions, Observation, Memory, and optional Scenario Context,
Persona, Goal, and Tool-Calling components with :class:`SocialConcatActComponent`.
"""

import dataclasses
from collections.abc import Mapping

from concordia.agents import entity_agent_with_logging
from concordia.associative_memory import basic_associative_memory
from concordia.components import agent as agent_components
from concordia.language_model import language_model
from concordia.typing import prefab as prefab_lib

from silisocs.agents.components.concat_act import SocialConcatActComponent
from silisocs.runtime.config import ConfigStore

OBSERVATION_TO_MEMORY_KEY = "__observation_to_memory__"
SCENARIO_CONTEXT_KEY = "__Scenario Context__"
INSTRUCTIONS_COMPONENT_KEY = "__Roleplaying Instructions__"
PERSONA_INFORMATION_KEY = "__Persona Information__"
DEFAULT_GOAL_COMPONENT_KEY = "Goal"


@dataclasses.dataclass
class Entity(prefab_lib.Prefab):
    """A scenario-agnostic base entity with a minimal set of components.

    Expected ``params`` keys:
        name: Agent display name.
        context: Persona description text (used to build backstory / prompt).
        goal: Optional overarching goal text.
        election_info: *Deprecated alias* — mapped to ``scenario_context``.
        scenario_context: Scenario-specific context injected as a constant.
        style: (unused at build time, but passed through for downstream use).

    """

    description: str = "A base social-simulation entity"
    params: Mapping[str, str] = dataclasses.field(
        default_factory=lambda: {
            "name": "Alice",
            "role": "",
            "goal": "",
            "scenario_context": "",
        }
    )

    def build(
        self,
        model: language_model.LanguageModel,
        memory_bank: basic_associative_memory.AssociativeMemoryBank,
    ) -> entity_agent_with_logging.EntityAgentWithLogging:
        """Build.

        :param language_model.LanguageModel model:
        :type model: language_model.LanguageModel
        :param basic_associative_memory.AssociativeMemoryBank memory_bank:
        :type memory_bank: basic_associative_memory.AssociativeMemoryBank

        :returns: entity_agent_with_logging.EntityAgentWithLogging
        :rtype: entity_agent_with_logging.EntityAgentWithLogging
        """
        agent_name = self.params.get("name", "Alice")

        # Accept both new key and legacy alias.
        scenario_context = self.params.get("scenario_context", "") or self.params.get(
            "election_info", ""
        )
        persona_context = self.params.get("context", "")

        instructions = agent_components.instructions.Instructions(agent_name=agent_name)
        cfg = ConfigStore.get_config()
        instructions._state = cfg.sim.roleplaying_instructions.format(name=agent_name)

        observation_to_memory = agent_components.observation.ObservationToMemory(
            memory_component_key=agent_components.memory.DEFAULT_MEMORY_COMPONENT_KEY
        )

        obs_history = getattr(getattr(cfg, "env", object()), "observation_history", None)
        if obs_history is None:
            obs_history = 100
        observation = agent_components.observation.LastNObservations(
            history_length=obs_history, pre_act_label="Observation"
        )

        components_of_agent = {
            INSTRUCTIONS_COMPONENT_KEY: instructions,
            OBSERVATION_TO_MEMORY_KEY: observation_to_memory,
            agent_components.observation.DEFAULT_OBSERVATION_COMPONENT_KEY: observation,
            agent_components.memory.DEFAULT_MEMORY_COMPONENT_KEY: (
                agent_components.memory.AssociativeMemory(memory_bank=memory_bank)
            ),
        }
        component_order = list(components_of_agent.keys())

        # Optional scenario context (replaces old election_info).
        if scenario_context:
            ctx_component = agent_components.constant.Constant(
                state=scenario_context,
                pre_act_label="Scenario Information",
            )
            components_of_agent[SCENARIO_CONTEXT_KEY] = ctx_component
            component_order.insert(1, SCENARIO_CONTEXT_KEY)

        # Optional persona description.
        if persona_context:
            persona_component = agent_components.constant.Constant(
                state=persona_context,
                pre_act_label="Information of Persona to Simulate",
            )
            components_of_agent[PERSONA_INFORMATION_KEY] = persona_component
            component_order.insert(1, PERSONA_INFORMATION_KEY)

        # Optional goal.
        if self.params.get("goal", ""):
            goal_component = agent_components.constant.Constant(
                state=self.params.get("goal", ""),
                pre_act_label="Overarching goal",
            )
            components_of_agent[DEFAULT_GOAL_COMPONENT_KEY] = goal_component
            component_order.insert(1, DEFAULT_GOAL_COMPONENT_KEY)

        act_component = SocialConcatActComponent(
            model=model,
            component_order=component_order,
            randomize_choices=False,
        )

        return entity_agent_with_logging.EntityAgentWithLogging(
            agent_name=agent_name,
            act_component=act_component,
            context_components=components_of_agent,
        )
