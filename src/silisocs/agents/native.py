"""Default native Silisocs agent."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from silisocs.agents.base_agent import Agent
from silisocs.initialization.context import AgentInitializationContext
from silisocs.runtime.language_models import LanguageModel
from silisocs.runtime.types import ActionOutput, ActionSpec


class NativeAgent(Agent):
    """Plain native agent with memories, observations, and direct model calls."""

    def __init__(
        self,
        *,
        name: str,
        model: LanguageModel,
        instructions: str = "",
        context: str = "",
        persona_context: str = "",
        world_context: str = "",
        election_info: str = "",
        goal: str = "",
        style: str = "",
        bio: str = "",
        seed_post: str = "",
        sim_role: Mapping[str, Any] | None = None,
        specific_memories: Sequence[str] | None = None,
        shared_memories: Sequence[str] | None = None,
        flow_tag: str | None = None,
        observation_history: int = 100,
        **extra_params: Any,
    ) -> None:
        super().__init__(model)
        self._name = str(name)
        self._instructions = str(instructions or "")
        self._persona_context = str(persona_context or context or "")
        self._world_context = str(world_context or election_info or "")
        self._goal = str(goal or "")
        self._style = str(style or "")
        self.bio = str(bio or "")
        self.seed_post = str(seed_post or "")
        self.sim_role = dict(sim_role or {})
        self.flow_tag = flow_tag
        self.extra_params = dict(extra_params)
        self._observation_history = max(1, int(observation_history or 100))
        self._observations: list[str] = []
        self._memory_text: list[str] = _normalize_text_items(
            shared_memories
        ) + _normalize_text_items(specific_memories)
        self._last_log: dict[str, Any] = {}

    @property
    def name(self) -> str:
        return self._name

    def observe(self, observation: str) -> None:
        text = str(observation or "").strip()
        if not text:
            return
        self._observations.append(text)
        self._observations = self._observations[-self._observation_history :]
        self._memory_text.append(text)

    def initialize(self, context: Any | None = None) -> None:
        memories: list[str] = []
        if isinstance(context, AgentInitializationContext):
            memories = _normalize_text_items(context.memories)
        elif isinstance(context, Mapping):
            raw = context.get("memories", context.get("observations", ()))
            memories = _normalize_text_items(raw)
        else:
            memories = _normalize_text_items(context)
        for memory in memories:
            self.observe(memory)

    def _memories(self) -> list[str]:
        return list(self._memory_text)

    def get_all_memories_as_text(self) -> list[str]:
        """Return all stored memory text for logs and tests."""
        return self._memories()

    def _context(self) -> str:
        sections: list[tuple[str, str]] = [
            ("Instructions", self._instructions),
            ("Persona", self._persona_context),
            ("World", self._world_context),
            ("Goal", self._goal),
            ("Style", self._style),
            ("Recent observations", "\n".join(self._observations[-self._observation_history :])),
            ("Memory", "\n".join(self._memories()[-10:])),
        ]
        return "\n\n".join(f"{label}:\n{text.strip()}" for label, text in sections if text.strip())

    def act(self, action_spec: ActionSpec) -> ActionOutput:
        context = self._context()
        result = self._call_model(context, action_spec)
        self._last_log = {
            "context": context,
            "prompt": action_spec.prompt,
            "action_attempt": str(result),
            "action_output_type": getattr(result.output_type, "value", str(result.output_type)),
        }
        return result

    def get_last_log(self) -> dict[str, Any]:
        return dict(self._last_log)

    def get_state(self) -> dict[str, Any]:
        state: dict[str, Any] = {
            "observations": list(self._observations),
            "memory_text": list(self._memory_text),
        }
        return state

    def set_state(self, state: Mapping[str, Any]) -> None:
        self._observations = _normalize_text_items(state.get("observations", ()))
        self._memory_text = _normalize_text_items(state.get("memory_text", self._observations))


def _normalize_text_items(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        text = value.strip()
        return [text] if text else []
    if isinstance(value, Sequence):
        return [str(item).strip() for item in value if str(item).strip()]
    text = str(value).strip()
    return [text] if text else []
