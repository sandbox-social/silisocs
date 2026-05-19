"""LLM-generated formative agent initializer."""

from __future__ import annotations

import functools
import re
from collections.abc import Sequence

from silisocs.agents.base_agent import Agent
from silisocs.initialization.agents.runtime import (
    AgentInitializer,
    build_agent_initialization_context,
)
from silisocs.initialization.context import InitializationContext
from silisocs.runtime.execution import concurrency
from silisocs.runtime.language_models import LanguageModel


class FormativeAgentInitializer(AgentInitializer):
    """Generate formative backstory memories before the simulation loop."""

    def initialize(
        self,
        *,
        agents: Sequence[Agent],
        model: LanguageModel,
        context: InitializationContext,
    ) -> None:
        """Generate memories, then call every agent's initialize hook."""

        def _init_agent(agent: Agent) -> None:
            generated = self.generate_memories(
                model=model,
                player_name=agent.name,
                shared_memories=list(context.shared_memories),
                player_context=str(context.player_specific_context.get(agent.name, "")),
            )
            agent.initialize(
                build_agent_initialization_context(
                    agent.name,
                    context,
                    generated_memories=generated,
                )
            )

        concurrency.run_tasks(
            {agent.name: functools.partial(_init_agent, agent) for agent in agents}
        )

    def generate_memories(
        self,
        *,
        model: LanguageModel,
        player_name: str,
        shared_memories: list[str],
        player_context: str,
    ) -> list[str]:
        """Generate formative backstory episodes via multi-step LLM prompting."""
        delimiter = "***"
        sentences_per_episode = 5

        setup = (
            "----- Role Playing Master Class -----\n"
            f"Question: What is the protagonist's name?\nAnswer: {player_name}\n"
            "Question: Describe the setting or background.\n"
            f"Answer: {chr(10).join(shared_memories)}\n"
        )
        if player_context:
            setup += (
                f"Question: Describe the personal context of the protagonist.\n"
                f"Answer: {player_context}\n"
            )

        gender = model.sample_text(f"{setup}\nWhat is the protagonist's gender?")
        dob = model.sample_text(
            f"{setup}\nWhat year was protagonist born? Respond with just the year, e.g. '1990'."
        )
        backstory = model.sample_text(
            f"{setup}\nWrite a life story for a {gender} character named {player_name} "
            f"who was born in {dob}. Begin the story when {player_name} is very "
            "young and end it when they are quite old. Keep it to no more than "
            "four paragraphs.",
            max_tokens=4500,
            terminators=["\nQuestion", "-----"],
        )
        backstory = re.sub(r"\.\s", ".\n", backstory)

        result = model.sample_text(
            "Creative Writing Master Class\n"
            f"Character background story:\n\n{backstory}\n\n"
            f"Given the life story above, invent formative episodes from the "
            f"life of {player_name}. Keep each to <= {sentences_per_episode} "
            f"sentences. Separate episodes with '{delimiter}'.",
            max_tokens=6000,
            terminators=[],
        )
        return [episode.strip() for episode in result.split(delimiter) if episode.strip()]


FormativeMemoryInitializer = FormativeAgentInitializer
