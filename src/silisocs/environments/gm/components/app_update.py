"""Generic backend-update component for native game masters."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from silisocs.environments.backends.base import BackendApp
from silisocs.environments.gm.components.base import UpdateComponent


class AppUpdateComponent(UpdateComponent):
    """Delegate the GM update slot to ``BackendApp.update``.

    Always passes the FULL population: ``BackendApp.update`` is world
    simulation with population semantics (e.g. resource-market upkeep charges
    every agent each step), so participation filtering must not shrink its
    roster — an inactive agent still exists in the world.
    """

    requires_full_roster = True

    def __init__(self, *, backend: BackendApp | None = None) -> None:
        super().__init__()
        self._backend = backend

    def update(self, *, step: int, agents: Sequence[Any], context: Any | None = None) -> None:
        if self._backend is None:
            raise TypeError("AppUpdateComponent requires a backend.")
        self._backend.update(
            step=step,
            agent_names=[agent.name for agent in agents],
            context=context,
        )
