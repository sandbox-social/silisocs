"""Base initializer game master — shared wiring for all memory initializers.

The ``InitializerGM`` prefab wires all standard Concordia GM components and
provides **raw memory injection** by default.  Subclasses only need to
override ``_create_memory_component()`` to swap in a different memory
initialization strategy (e.g. LLM-generated backstories).

Quick-start for custom initializers::

    @dataclasses.dataclass
    class GameMaster(InitializerGM):
        def _create_memory_component(self, model, player_names, shared_memories):
            return MyCustomMemoryComponent(...)
"""

import dataclasses
import types
from collections.abc import Mapping, Sequence
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

from mastodon_sim.environments.gm_components.observe import SimpleMakeObservation

# --------------------------------------------------------------------------- #
# Component: RawMemoryInjector (default memory init strategy)
# --------------------------------------------------------------------------- #


class RawMemoryInjector(entity_component.ContextComponent, entity_component.ComponentWithLogging):
    """Inject shared and player-specific memories without LLM generation.

    This is the default memory initialization component used by
    ``InitializerGM``.  It writes memories to the GM's memory bank and
    queues observations for each player, then hands off to the next GM.
    """

    def __init__(
        self,
        next_game_master_name: str,
        player_names: Sequence[str],
        shared_memories: Sequence[str] = (),
        player_specific_memories: Mapping[str, Sequence[str]] = types.MappingProxyType({}),
        memory_component_key: str = memory_comp.DEFAULT_MEMORY_COMPONENT_KEY,
        make_observation_component_key: str = (
            make_observation_component.DEFAULT_MAKE_OBSERVATION_COMPONENT_KEY
        ),
    ):
        super().__init__()
        self._next_gm_name = next_game_master_name
        self._player_names = player_names
        self._shared_memories = list(shared_memories)
        self._player_specific_memories = dict(player_specific_memories)
        self._memory_key = memory_component_key
        self._observation_key = make_observation_component_key
        self._initialized = False

    @staticmethod
    def normalize_memories(memories: object) -> list[str]:
        """Coerce various memory formats into a flat list of strings."""
        if memories is None:
            return []
        if isinstance(memories, str):
            lines = [line.strip() for line in memories.splitlines() if line.strip()]
            return lines or ([memories.strip()] if memories.strip() else [])
        if isinstance(memories, list):
            return [str(x).strip() for x in memories if str(x).strip()]
        return [str(memories).strip()] if str(memories).strip() else []

    def pre_act(self, action_spec: entity_lib.ActionSpec) -> str:
        if action_spec.output_type != entity_lib.OutputType.NEXT_GAME_MASTER:
            return ""
        if self._initialized:
            return self._next_gm_name

        memory = self.get_entity().get_component(self._memory_key, type_=memory_comp.Memory)
        observe = self.get_entity().get_component(
            self._observation_key,
            type_=make_observation_component.MakeObservation,
        )

        for m in self._shared_memories:
            memory.add(m)
        for name in self._player_names:
            for m in self._shared_memories:
                observe.add_to_queue(name, m)
            for m in self.normalize_memories(self._player_specific_memories.get(name, [])):
                observe.add_to_queue(name, m)
                memory.add(f'{name} remembers: "{m}"')

        self._initialized = True
        self._logging_channel(
            {
                "Key": "memory_init",
                "Summary": "Raw memories initialized",
                "Value": f"{len(self._shared_memories)} shared, {len(self._player_names)} players",
            }
        )
        return self.get_entity().name

    def get_state(self) -> entity_component.ComponentState:
        return {"initialized": self._initialized}

    def set_state(self, state: entity_component.ComponentState) -> None:
        self._initialized = bool(state.get("initialized", self._initialized))


# --------------------------------------------------------------------------- #
# Prefab: InitializerGM — base game master with raw init by default
# --------------------------------------------------------------------------- #


@dataclasses.dataclass
class InitializerGM(prefab_lib.Prefab):
    """Base initializer game master.

    Wires all standard Concordia GM components (instructions, observation,
    memory, next-actor, etc.) and uses ``RawMemoryInjector`` by default.

    **To create a custom initializer**, subclass and override
    ``_create_memory_component``::

        @dataclasses.dataclass
        class GameMaster(InitializerGM):
            def _create_memory_component(self, model, player_names, shared_memories):
                return MyComponent(
                    next_game_master_name=self.params["next_game_master_name"],
                    player_names=player_names,
                    shared_memories=shared_memories,
                )
    """

    description: str = "Initializer game master (raw memory injection)"
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

    # -- Override point ---------------------------------------------------- #

    def _create_memory_component(
        self,
        model: language_model.LanguageModel,
        player_names: list[str],
        shared_memories: list[str],
    ) -> entity_component.ContextComponent:
        """Return the component that initializes agent memories.

        The default implementation injects raw memories (no LLM).
        Override this to provide a custom initialization strategy.

        Args:
            model: Language model (only needed by LLM-based initializers).
            player_names: Agent names.
            shared_memories: Shared memory strings.

        Returns
        -------
            A ``ContextComponent`` for the ``next_game_master`` slot.
        """
        return RawMemoryInjector(
            next_game_master_name=str(self.params.get("next_game_master_name", "default rules")),
            player_names=player_names,
            shared_memories=shared_memories,
            player_specific_memories=self.params.get("player_specific_memories", {}),
        )

    # -- Build ------------------------------------------------------------- #

    def build(
        self,
        model: language_model.LanguageModel,
        memory_bank: basic_associative_memory.AssociativeMemoryBank,
    ) -> entity_agent_with_logging.EntityAgentWithLogging:
        name = str(self.params.get("name", "initial setup rules"))
        player_names = [e.name for e in self.entities]

        # Normalize shared memories.
        raw = self.params.get("shared_memories", [])
        if isinstance(raw, str):
            shared_memories = [raw]
        elif isinstance(raw, Sequence):
            shared_memories = list(raw)
        else:
            shared_memories = []

        # Standard GM components.
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
                self._create_memory_component(model, player_names, shared_memories)
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

        act_component = gm_components.switch_act.SwitchAct(
            model=model,
            entity_names=player_names,
            component_order=list(components.keys()),
        )

        return entity_agent_with_logging.EntityAgentWithLogging(
            agent_name=name,
            act_component=act_component,
            context_components=components,
        )


# Backward-compatibility alias.
BaseInitializerGM = InitializerGM
