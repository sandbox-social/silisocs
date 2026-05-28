"""Native recommendation update components for EchoChamberSim replications."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from silisocs.environments.gm.components.base import UpdateComponent


class NoOpRecommendationUpdate(UpdateComponent):
    """Preserve the old no-op recommendation behavior in the native update slot."""

    def __init__(self) -> None:
        super().__init__()

    def update(self, *, step: int, agents: Sequence[Any], context: Any | None = None) -> None:
        del step, agents, context

    def get_state(self) -> dict[str, Any]:
        return {}

    def set_state(self, state: dict[str, Any]) -> None:
        del state
