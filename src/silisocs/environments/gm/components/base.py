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


class InitializeComponent(BaseComponent, ABC):
    """Base class for components that initialize one game master."""

    @abstractmethod
    def initialize(self, *, agents: Sequence[Any], gm_context: Any, context: Any) -> None:
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

    # Declare True when make_observation neither mutates GM/component state nor
    # performs backend writes; the engine then skips the per-GM lock around it,
    # so a step's observation reads run concurrently (SQLite WAL reads on
    # thread-local connections are safe alongside the single writer). Defaults
    # to False so custom components keep the serialized path unless they opt in.
    read_only: bool = False

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

    # ``update`` receives the step's ACTIVE roster (the engine applies the
    # sim-level participation filter before GM updates). Declare True when the
    # component genuinely needs the full population every step; the game master
    # then passes its full roster instead. Prefer the default: full-roster
    # per-step work is what makes large populations O(N) per step.
    requires_full_roster: bool = False

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
