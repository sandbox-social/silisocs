"""Agent-builder extension contract."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping
from typing import Any

from silisocs.runtime.construction.specs import AgentConfig


class AgentBuilder(ABC):
    """Translate composed agent config into runtime agent specs.

    Builders do not instantiate live agents. Runtime assembly owns model
    injection, native construction, and compatibility wrapping.
    """

    def __init__(self, agents_config: Any, params: Mapping[str, Any] | None = None) -> None:
        self.config = agents_config
        self.params = dict(params or {})

    @abstractmethod
    def build_agent_configs(self) -> list[AgentConfig]:
        """Return construction specs for agents in this run."""
