"""Formative memory initializer.

Generates LLM-driven backstory episodes for each agent before handing off
to the main game master.  Extends ``InitializerGM`` — only the custom
``_create_memory_component`` override and the ``FormativeMemoriesInitializer``
component are defined here; all standard GM wiring lives in the base class.
"""

import dataclasses
import functools
import re
import types
from collections.abc import Mapping, Sequence
from typing import Any

from concordia.components.agent import action_spec_ignored
from concordia.components.agent import memory as memory_comp
from concordia.components.game_master import make_observation as make_observation_component
from concordia.document import interactive_document
from concordia.language_model import language_model
from concordia.typing import entity as entity_lib
from concordia.typing import entity_component
from concordia.utils import concurrency

from mastodon_sim.agents.initialization.base import InitializerGM

# --------------------------------------------------------------------------- #
# Component: FormativeMemoriesInitializer
# --------------------------------------------------------------------------- #


class FormativeMemoriesInitializer(
    entity_component.ContextComponent, entity_component.ComponentWithLogging
):
    """Generate formative backstory episodes then hand off to the next GM."""

    def __init__(
        self,
        model: language_model.LanguageModel,
        next_game_master_name: str,
        player_names: Sequence[str],
        shared_memories: Sequence[str] = (),
        player_specific_memories: Mapping[str, Sequence[str]] = types.MappingProxyType({}),
        player_specific_context: Mapping[str, str] = types.MappingProxyType({}),
        components: Sequence[str] = (),
        delimiter_symbol: str = "***",
        memory_component_key: str = memory_comp.DEFAULT_MEMORY_COMPONENT_KEY,
        make_observation_component_key: str = (
            make_observation_component.DEFAULT_MAKE_OBSERVATION_COMPONENT_KEY
        ),
        sentences_per_episode: int = 5,
    ):
        super().__init__()
        self._model = model
        self._next_gm_name = next_game_master_name
        self._player_names = player_names
        self._shared_memories = list(shared_memories)
        self._player_specific_memories = dict(player_specific_memories)
        self._player_specific_context = dict(player_specific_context)
        self._components = components
        self._delimiter = delimiter_symbol
        self._memory_key = memory_component_key
        self._observation_key = make_observation_component_key
        self._sentences_per_episode = sentences_per_episode
        self._initialized = False

    def _component_display(self, key: str) -> str:
        comp = self.get_entity().get_component(
            key,
            type_=action_spec_ignored.ActionSpecIgnored,
        )
        return f"{comp.get_pre_act_label()}:\n{comp.get_pre_act_value()}"

    def _generate_backstory(self, player_name: str) -> list[str]:
        prompt = interactive_document.InteractiveDocument(self._model)
        if self._components:
            prompt.statement("\n".join(self._component_display(k) for k in self._components) + "\n")
        prompt.statement("----- Role Playing Master Class -----\n")
        prompt.statement(f"Question: What is the protagonist's name?\nAnswer: {player_name}\n")
        prompt.statement(
            "Question: Describe the setting or background.\n"
            f"Answer: {chr(10).join(self._shared_memories)}\n"
        )
        ctx = self._player_specific_context.get(player_name, "")
        if ctx:
            prompt.statement(
                f"Question: Describe the personal context of the protagonist.\nAnswer: {ctx}\n"
            )

        gender = prompt.open_question("What is the protagonist's gender?")
        dob = prompt.open_question(
            "What year was protagonist born? Respond with just the year, e.g. '1990'."
        )
        backstory = prompt.open_question(
            f"Write a life story for a {gender} character named {player_name} "
            f"who was born in {dob}. Begin the story when {player_name} is very "
            "young and end it when they are quite old. Keep it to no more than "
            "four paragraphs.",
            max_tokens=4500,
            terminators=["\nQuestion", "-----"],
        )
        backstory = re.sub(r"\.\s", ".\n", backstory)

        inner = interactive_document.InteractiveDocument(self._model)
        inner.statement("Creative Writing Master Class\n")
        inner.statement("Character background story:\n\n" + backstory)
        result = prompt.open_question(
            f"Given the life story above, invent formative episodes from the "
            f"life of {player_name}. Keep each to <= {self._sentences_per_episode} "
            f"sentences. Separate episodes with '{self._delimiter}'.",
            max_tokens=6000,
            terminators=[],
        )
        episodes = [e.strip() for e in result.split(self._delimiter) if e.strip()]
        self._logging_channel(
            {
                "Key": "formative_backstory",
                "Episodes": episodes,
                "Inner Prompt": inner.view().text(),
                "Prompt": prompt.view().text(),
            }
        )
        return episodes

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

        def _process(name: str) -> None:
            for m in self._shared_memories:
                observe.add_to_queue(name, m)
            for ep in self._generate_backstory(name):
                observe.add_to_queue(name, ep)
                memory.add(f'{name} remembers: "{ep}"')
            for m in self._player_specific_memories.get(name, []):
                observe.add_to_queue(name, m)
                memory.add(f'{name} remembers: "{m}"')

        concurrency.run_tasks({n: functools.partial(_process, n) for n in self._player_names})
        self._initialized = True
        return self.get_entity().name

    def get_state(self) -> entity_component.ComponentState:
        return {"initialized": self._initialized}

    def set_state(self, state: entity_component.ComponentState) -> None:
        self._initialized = bool(state.get("initialized", self._initialized))


# --------------------------------------------------------------------------- #
# Prefab: GameMaster
# --------------------------------------------------------------------------- #


@dataclasses.dataclass
class GameMaster(InitializerGM):
    """Initializer that generates LLM formative memories before handing off."""

    description: str = "Formative memory initializer game master"
    params: Mapping[str, Any] = dataclasses.field(
        default_factory=lambda: {
            "name": "initial setup rules",
            "next_game_master_name": "default rules",
            "shared_memories": [],
            "player_specific_context": {},
            "player_specific_memories": {},
        }
    )

    def _create_memory_component(self, model, player_names, shared_memories):
        return FormativeMemoriesInitializer(
            model=model,
            next_game_master_name=str(self.params.get("next_game_master_name", "default rules")),
            player_names=player_names,
            shared_memories=shared_memories,
            player_specific_memories=self.params.get("player_specific_memories", {}),
            player_specific_context=self.params.get("player_specific_context", {}),
        )
