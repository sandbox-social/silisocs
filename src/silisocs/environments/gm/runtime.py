"""Native game-master runtime contracts."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Protocol, runtime_checkable

from silisocs.runtime.types import ActionOutput, ActionSpec


@runtime_checkable
class GameMasterProtocol(Protocol):
    """Direct native game-master surface used by runtime engines."""

    @property
    def name(self) -> str: ...

    def acting_agents(self, candidate_agents: Sequence[Any]) -> list[str]: ...

    def initialize(self, *, agents: Sequence[Any], context: Any) -> None: ...

    def update(self, *, step: int, agents: Sequence[Any], context: Any | None = None) -> None: ...

    def action_prompt(self, agent_name: str) -> ActionSpec: ...

    def make_observation(self, agent_name: str) -> str: ...

    def resolve_action(self, agent_name: str, action: ActionOutput) -> str: ...
