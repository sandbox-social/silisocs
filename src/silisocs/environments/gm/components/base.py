"""Base interfaces for native game-master components."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping, Sequence
from typing import Any

from silisocs.runtime.types import ActionOutput, ActionSpec

ComponentState = Mapping[str, Any]


class BaseComponent:
    """Small native component base with optional checkpoint hooks."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        del args, kwargs

    def get_state(self) -> ComponentState:
        return {}

    def set_state(self, state: Mapping[str, Any]) -> None:
        del state


class NoOpComponent(BaseComponent):
    """Passive component for optional GM slots that are disabled."""

    def __init__(self, **kwargs: Any) -> None:
        """Accept and ignore runtime kwargs from component factories."""
        del kwargs
        super().__init__()


class InitializeComponent(BaseComponent, ABC):
    """Base class for components that initialize one game master."""

    @abstractmethod
    def initialize(self, *, agents: Sequence[Any], game_master: Any, context: Any) -> None:
        """Initialize backend/environment state for one game master."""


class NextActingComponent(BaseComponent, ABC):
    """Base class for components that select acting agents."""

    @abstractmethod
    def acting_agent_names(self) -> list[str]:
        """Return selected agent names for the current turn."""


class ActionPromptComponent(BaseComponent, ABC):
    """Base class for components that build one agent action prompt."""

    @abstractmethod
    def action_prompt(self, agent_name: str) -> ActionSpec:
        """Return an action spec for one agent."""


class ObservationComponent(BaseComponent, ABC):
    """Base class for components that build one agent observation."""

    @abstractmethod
    def make_observation(self, agent_name: str) -> str:
        """Return an observation for one agent."""


class ResolveComponent(BaseComponent, ABC):
    """Base class for components that resolve one agent action."""

    @abstractmethod
    def resolve_action(self, agent_name: str, action: ActionOutput) -> str:
        """Resolve one action through the backend."""


class UpdateComponent(BaseComponent, ABC):
    """Base class for per-step GM update components."""

    @abstractmethod
    def update(self, *, step: int, agents: Sequence[Any], context: Any | None = None) -> None:
        """Run one per-step update before actor selection."""


class NoOpUpdateComponent(UpdateComponent):
    """Update component that intentionally does nothing."""

    def __init__(self, **kwargs: Any) -> None:
        del kwargs
        super().__init__()

    def update(self, *, step: int, agents: Sequence[Any], context: Any | None = None) -> None:
        del step, agents, context
