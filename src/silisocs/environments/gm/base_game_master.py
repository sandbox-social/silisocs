"""Native game-master base interfaces."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence
from typing import Any

from silisocs.runtime.types import ActionOutput, ActionSpec


class BaseGameMaster(ABC):
    """Abstract native game-master surface used by runtime engines."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Return this game master's stable runtime name."""

    @abstractmethod
    def initialize(self, *, agents: Sequence[Any], context: Any) -> None:
        """Initialize backend/environment state before the simulation loop."""

    @abstractmethod
    def update(self, *, step: int, agents: Sequence[Any], context: Any | None = None) -> None:
        """Run one pre-turn update for this game master."""

    @abstractmethod
    def acting_agents(self, candidate_agents: Sequence[Any]) -> list[str]:
        """Return selected agent names for this turn."""

    @abstractmethod
    def action_prompt(self, agent_name: str) -> ActionSpec:
        """Return the action prompt for one agent."""

    @abstractmethod
    def make_observation(self, agent_name: str) -> str:
        """Build an observation for one agent."""

    @abstractmethod
    def resolve_action(self, agent_name: str, action: ActionOutput) -> str:
        """Resolve one agent action through the backend."""
