"""Formative memory initializer — LLM-generated backstory episodes.

Subclasses ``InitializerGM`` and overrides ``generate_memories()`` to
produce multi-episode backstories via LLM prompting.  All GM wiring,
shared/specific memory injection, and concurrency are handled by the
base class.
"""

import dataclasses
import re
from collections.abc import Mapping
from typing import Any

from concordia.document import interactive_document

from silisocs.agents.initialization.base import InitializerGM


@dataclasses.dataclass
class GameMaster(InitializerGM):
    """Initializer that generates LLM formative memories before handing off."""

    description: str = "Formative memory initializer"
    params: Mapping[str, Any] = dataclasses.field(
        default_factory=lambda: {
            "name": "initial setup rules",
            "next_game_master_name": "default rules",
            "shared_memories": [],
            "player_specific_context": {},
            "player_specific_memories": {},
        }
    )

    def generate_memories(self, model, player_name, shared_memories, player_context):
        """Generate formative backstory episodes via multi-step LLM prompting."""
        delimiter = "***"
        sentences_per_episode = 5

        prompt = interactive_document.InteractiveDocument(model)
        prompt.statement("----- Role Playing Master Class -----\n")
        prompt.statement(f"Question: What is the protagonist's name?\nAnswer: {player_name}\n")
        prompt.statement(
            "Question: Describe the setting or background.\n"
            f"Answer: {chr(10).join(shared_memories)}\n"
        )
        if player_context:
            prompt.statement(
                f"Question: Describe the personal context of the protagonist.\n"
                f"Answer: {player_context}\n"
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

        # Second pass: extract discrete episodes from the backstory.
        inner = interactive_document.InteractiveDocument(model)
        inner.statement("Creative Writing Master Class\n")
        inner.statement("Character background story:\n\n" + backstory)
        result = prompt.open_question(
            f"Given the life story above, invent formative episodes from the "
            f"life of {player_name}. Keep each to <= {sentences_per_episode} "
            f"sentences. Separate episodes with '{delimiter}'.",
            max_tokens=6000,
            terminators=[],
        )
        return [e.strip() for e in result.split(delimiter) if e.strip()]
