"""Explicit Concordia-style agent prefab for compatibility worlds."""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping
from typing import Any, cast

from silisocs.adapters.concordia import (
    EntityAgentWithLogging,
    SocialConcatActComponent,
    constant,
    instructions,
    language_model,
    memory_comp,
    observation,
    prefab_lib,
)
from silisocs.agents.native import history_size

OBSERVATION_TO_MEMORY_KEY = "__observation_to_memory__"
WORLD_CONTEXT_KEY = "__World Context__"
INSTRUCTIONS_COMPONENT_KEY = "__Roleplaying Instructions__"
PERSONA_INFORMATION_KEY = "__Persona Information__"
DEFAULT_GOAL_COMPONENT_KEY = "Goal"

DEFAULT_ROLEPLAYING_INSTRUCTIONS = (
    "You are {name}. Act naturally and stay consistent with your persona and current goal."
)


@dataclasses.dataclass
class ConcordiaAgent(prefab_lib.Prefab):
    """Legacy Concordia-style agent prefab.

    This class is intentionally not a native ``Agent``. Runtime configs must set
    ``compat: concordia`` so construction wraps the built Concordia object with
    ``ConcordiaAgentAdapter`` before it reaches the engine.
    """

    description: str = "A Concordia-compatible social-simulation agent"
    params: Mapping[str, Any] = dataclasses.field(
        default_factory=lambda: {
            "name": "Alice",
            "role": "",
            "goal": "",
            "world_context": "",
        }
    )

    def build(
        self,
        model: language_model.LanguageModel,
        memory_bank: Any,
    ) -> EntityAgentWithLogging:
        agent_name = str(self.params.get("name", "Alice"))
        world_context = str(
            self.params.get("world_context", "") or self.params.get("election_info", "")
        )
        persona_context = str(self.params.get("context", ""))
        roleplaying = str(
            self.params.get("roleplaying_instructions", DEFAULT_ROLEPLAYING_INSTRUCTIONS)
            or DEFAULT_ROLEPLAYING_INSTRUCTIONS
        )
        # Same resolution as NativeAgent: an explicit ``null`` takes the default
        # (``int(None)`` would raise), an explicit number is honoured and clamped
        # to at least 1 rather than reverting to the default.
        observation_history = history_size(self.params.get("observation_history"), 100)

        role_instructions = instructions.Instructions(agent_name=agent_name)
        role_instructions._state = roleplaying.format(name=agent_name)
        observation_to_memory = observation.ObservationToMemory(
            memory_component_key=memory_comp.DEFAULT_MEMORY_COMPONENT_KEY
        )
        recent_observation = observation.LastNObservations(
            history_length=observation_history,
            pre_act_label="Observation",
        )

        components = {
            INSTRUCTIONS_COMPONENT_KEY: role_instructions,
            OBSERVATION_TO_MEMORY_KEY: observation_to_memory,
            observation.DEFAULT_OBSERVATION_COMPONENT_KEY: recent_observation,
            memory_comp.DEFAULT_MEMORY_COMPONENT_KEY: memory_comp.AssociativeMemory(
                memory_bank=memory_bank
            ),
        }
        component_order = list(components.keys())

        if world_context:
            components[WORLD_CONTEXT_KEY] = constant.Constant(
                state=world_context,
                pre_act_label="World Information",
            )
            component_order.insert(1, WORLD_CONTEXT_KEY)

        if persona_context:
            components[PERSONA_INFORMATION_KEY] = constant.Constant(
                state=persona_context,
                pre_act_label="Information of Persona to Simulate",
            )
            component_order.insert(1, PERSONA_INFORMATION_KEY)

        goal = str(self.params.get("goal", "") or "")
        if goal:
            components[DEFAULT_GOAL_COMPONENT_KEY] = constant.Constant(
                state=goal,
                pre_act_label="Overarching goal",
            )
            component_order.insert(1, DEFAULT_GOAL_COMPONENT_KEY)

        act_component = cast(Any, SocialConcatActComponent)(
            model=model,
            component_order=component_order,
            randomize_choices=False,
        )

        return EntityAgentWithLogging(
            agent_name=agent_name,
            act_component=act_component,
            context_components=components,
        )
