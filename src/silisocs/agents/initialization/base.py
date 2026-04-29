"""Base initializer game master — shared wiring for all memory initializers.

The ``InitializerGM`` prefab wires all Concordia GM components and runs a
single initialization pass that injects memories into each agent's
observation stream.

**To create a custom initializer**, subclass ``InitializerGM`` and override
``generate_memories()``.  That's it — one class, one method::

    @dataclasses.dataclass
    class GameMaster(InitializerGM):
        def generate_memories(self, model, player_name, shared_memories, context):
            # Use `model` for LLM calls, return list of memory strings.
            return [f"{player_name} once climbed a mountain."]

The base class handles all Concordia component wiring, shared/specific
memory injection, concurrent player processing, and GM lifecycle.
"""

import dataclasses
import functools
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from concordia.agents import entity_agent_with_logging
from concordia.associative_memory import basic_associative_memory
from concordia.components import agent as actor_components
from concordia.components import game_master as gm_components
from concordia.components.agent import memory as memory_comp
from concordia.components.game_master import make_observation as make_observation_component
from concordia.language_model import language_model
from concordia.typing import entity as entity_lib
from concordia.typing import entity_component
from concordia.typing import prefab as prefab_lib
from concordia.utils import concurrency

from silisocs.environments.gm.components.initializer_observe import SimpleMakeObservation

# --------------------------------------------------------------------------- #
# Prefab: InitializerGM
# --------------------------------------------------------------------------- #


@dataclasses.dataclass
class InitializerGM(prefab_lib.Prefab):
    """Base initializer game master.

    Wires all Concordia GM components. On the first step it:

    1. Adds shared memories to the GM memory bank.
    2. For each player (concurrently):
       a. Queues shared memories as observations.
       b. Calls ``generate_memories()`` and queues the results.
       c. Queues player-specific memories from config.
    3. Hands off to the main simulation game master.

    **Override ``generate_memories()``** to customize step 2b.
    Everything else is handled automatically.
    """

    description: str = "Initializer game master"
    params: Mapping[str, Any] = dataclasses.field(
        default_factory=lambda: {
            "name": "initial setup rules",
            "next_game_master_name": "default rules",
            "shared_memories": [],
            "player_specific_context": {},
            "player_specific_memories": {},
        }
    )
    entities: Sequence[entity_agent_with_logging.EntityAgentWithLogging] = ()

    # -- Override this ----------------------------------------------------- #

    def generate_memories(
        self,
        model: language_model.LanguageModel,
        player_name: str,
        shared_memories: list[str],
        player_context: str,
    ) -> list[str]:
        """Generate extra memories for a player. Override in subclasses.

        Called once per player during initialization. The default returns
        an empty list (raw injection — only config memories are used).

        Args:
            model: Language model (for LLM-based generators).
            player_name: The agent's display name.
            shared_memories: Shared memory strings from config.
            player_context: Per-player context string from config.

        Returns
        -------
            List of memory strings to inject for this player.
        """
        return []

    # -- Build ------------------------------------------------------------- #

    def build(
        self,
        model: language_model.LanguageModel,
        memory_bank: basic_associative_memory.AssociativeMemoryBank,
    ) -> entity_agent_with_logging.EntityAgentWithLogging:
        name = str(self.params.get("name", "initial setup rules"))
        player_names = [e.name for e in self.entities]
        shared_memories = _normalize_memories(self.params.get("shared_memories", []))
        player_context = dict(self.params.get("player_specific_context", {}))
        player_memories = dict(self.params.get("player_specific_memories", {}))

        # Create the internal component, passing our generate_memories as hook.
        memory_component = _MemoryInitComponent(
            next_game_master_name=str(self.params.get("next_game_master_name", "default rules")),
            player_names=player_names,
            shared_memories=shared_memories,
            player_specific_memories=player_memories,
            generate_fn=lambda pn: self.generate_memories(
                model,
                pn,
                shared_memories,
                player_context.get(pn, ""),
            ),
        )

        components = {
            "instructions": gm_components.instructions.Instructions(),
            "examples": gm_components.instructions.ExamplesSynchronous(),
            "player_characters": gm_components.instructions.PlayerCharacters(
                player_characters=player_names,
            ),
            actor_components.observation.DEFAULT_OBSERVATION_COMPONENT_KEY: (
                actor_components.observation.LastNObservations(history_length=1000)
            ),
            "observation_to_memory": actor_components.observation.ObservationToMemory(),
            actor_components.memory.DEFAULT_MEMORY_COMPONENT_KEY: (
                actor_components.memory.AssociativeMemory(memory_bank=memory_bank)
            ),
            gm_components.next_game_master.DEFAULT_NEXT_GAME_MASTER_COMPONENT_KEY: (
                memory_component
            ),
            gm_components.make_observation.DEFAULT_MAKE_OBSERVATION_COMPONENT_KEY: (
                SimpleMakeObservation(model=model, player_names=player_names)
            ),
            gm_components.next_acting.DEFAULT_NEXT_ACTING_COMPONENT_KEY: (
                gm_components.next_acting.NextActingAllEntities(player_names=player_names)
            ),
            gm_components.next_acting.DEFAULT_NEXT_ACTION_SPEC_COMPONENT_KEY: (
                gm_components.next_acting.FixedActionSpec(
                    action_spec=entity_lib.skip_this_step_action_spec(),
                )
            ),
        }

        return entity_agent_with_logging.EntityAgentWithLogging(
            agent_name=name,
            act_component=gm_components.switch_act.SwitchAct(
                model=model,
                entity_names=player_names,
                component_order=list(components.keys()),
            ),
            context_components=components,
        )


# --------------------------------------------------------------------------- #
# Internal component (users never touch this)
# --------------------------------------------------------------------------- #


class _MemoryInitComponent(
    entity_component.ContextComponent, entity_component.ComponentWithLogging
):
    """Internal component that executes the memory initialization flow."""

    def __init__(
        self,
        next_game_master_name: str,
        player_names: Sequence[str],
        shared_memories: Sequence[str],
        player_specific_memories: Mapping[str, Sequence[str]],
        generate_fn: Callable[[str], list[str]],
    ):
        super().__init__()
        self._next_gm_name = next_game_master_name
        self._player_names = list(player_names)
        self._shared_memories = list(shared_memories)
        self._player_specific_memories = dict(player_specific_memories)
        self._generate_fn = generate_fn
        self._initialized = False

    def pre_act(self, action_spec: entity_lib.ActionSpec) -> str:
        if action_spec.output_type != entity_lib.OutputType.NEXT_GAME_MASTER:
            return ""
        if self._initialized:
            return self._next_gm_name

        memory = self.get_entity().get_component(
            memory_comp.DEFAULT_MEMORY_COMPONENT_KEY,
            type_=memory_comp.Memory,
        )
        observe = self.get_entity().get_component(
            make_observation_component.DEFAULT_MAKE_OBSERVATION_COMPONENT_KEY,
            type_=make_observation_component.MakeObservation,
        )

        for m in self._shared_memories:
            memory.add(m)

        def _init_player(name: str) -> None:
            for m in self._shared_memories:
                observe.add_to_queue(name, m)
            for m in self._generate_fn(name):
                observe.add_to_queue(name, m)
                memory.add(f'{name} remembers: "{m}"')
            for m in _normalize_memories(self._player_specific_memories.get(name, [])):
                observe.add_to_queue(name, m)
                memory.add(f'{name} remembers: "{m}"')

        concurrency.run_tasks({n: functools.partial(_init_player, n) for n in self._player_names})

        self._initialized = True
        self._logging_channel(
            {
                "Key": "memory_init",
                "Value": f"{len(self._shared_memories)} shared, {len(self._player_names)} players",
            }
        )
        return self.get_entity().name

    def get_state(self) -> entity_component.ComponentState:
        return {"initialized": self._initialized}

    def set_state(self, state: entity_component.ComponentState) -> None:
        self._initialized = bool(state.get("initialized", self._initialized))


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _normalize_memories(memories: object) -> list[str]:
    """Coerce various memory formats into a flat list of strings."""
    if memories is None:
        return []
    if isinstance(memories, str):
        lines = [line.strip() for line in memories.splitlines() if line.strip()]
        return lines or ([memories.strip()] if memories.strip() else [])
    if isinstance(memories, (list, tuple)):
        return [str(x).strip() for x in memories if str(x).strip()]
    return [str(memories).strip()] if str(memories).strip() else []


# Backward-compatibility aliases.
BaseInitializerGM = InitializerGM
