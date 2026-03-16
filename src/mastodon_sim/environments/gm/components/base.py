"""Base interfaces for configurable game-master components."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping, Sequence
from typing import Any


class BackendInitializer(ABC):
    """Initialize backend app state for a simulation run."""

    @abstractmethod
    def initialize(
        self,
        *,
        sm_app: Any,
        agent_names: Sequence[str],
        init_kwargs: Mapping[str, Any],
    ) -> None:
        """Initialize backend runtime state for the provided agent set."""
        raise NotImplementedError
