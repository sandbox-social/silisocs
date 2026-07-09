"""Default native Silisocs agent."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from silisocs.agents.base_agent import Agent
from silisocs.agents.memory import MemoryPolicy, MemoryPolicyFactory, WindowMemory
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
        memory_history: int = 1000,
        memory_policy: MemoryPolicyFactory | None = None,
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
        self._memory_history = max(1, int(memory_history or 1000))
        self._observations: list[str] = []
        # Memory is delegated to a MemoryPolicy (sim.memory). None = the default
        # window policy, byte-identical to the pre-slot behavior. Seeded memories
        # go into memory only (not the recent-observation feed), matching the
        # original construction.
        self._memory: MemoryPolicy = (
            memory_policy(model=model, memory_history=self._memory_history)
            if memory_policy is not None
            else WindowMemory(memory_history=self._memory_history)
        )
        for seeded in _normalize_text_items(shared_memories) + _normalize_text_items(
            specific_memories
        ):
            self._memory.record(seeded)
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
        self._memory.record(text)

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

    def get_all_memories_as_text(self) -> list[str]:
        """Return all stored memory text for logs and tests."""
        return self._memory.all_memories()

    def _context(self) -> str:
        # The current observation is the retrieval query for the Memory section
        # (window/summarizing ignore it; retrieval ranks memories against it).
        query = self._observations[-1] if self._observations else None
        sections: list[tuple[str, str]] = [
            ("Instructions", self._instructions),
            ("Persona", self._persona_context),
            ("World", self._world_context),
            ("Goal", self._goal),
            ("Style", self._style),
            ("Recent observations", "\n".join(self._observations[-self._observation_history :])),
            ("Memory", self._memory.render(query=query)),
        ]
        return "\n\n".join(f"{label}:\n{text.strip()}" for label, text in sections if text.strip())

    def act(self, action_spec: ActionSpec) -> ActionOutput:
        context = self._context()
        result = self._call_model(context, action_spec)
        return self._record_act(context, action_spec, result)

    async def act_async(self, action_spec: ActionSpec) -> ActionOutput:
        """Loop-native act: awaits the model's async path instead of holding a
        thread for the request, so thousands of native-agent turns can be in
        flight at once under the asyncio turn executor.
        """
        context = self._context()
        result = await self._call_model_async(context, action_spec)
        return self._record_act(context, action_spec, result)

    def _record_act(
        self, context: str, action_spec: ActionSpec, result: ActionOutput
    ) -> ActionOutput:
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
        return {
            "observations": list(self._observations),
            "memory": self._memory.get_state(),
        }

    def set_state(self, state: Mapping[str, Any]) -> None:
        self._observations = _normalize_text_items(state.get("observations", ()))[
            -self._observation_history :
        ]
        if "memory" in state:
            self._memory.set_state(state["memory"])
        elif "memory_text" in state:
            # Legacy checkpoint (pre-memory-slot): raw memory list.
            self._memory.set_state({"memories": _normalize_text_items(state["memory_text"])})


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
