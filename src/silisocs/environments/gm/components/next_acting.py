"""Native next-acting components for game masters.

This slot is the home of ENVIRONMENT-derived selection (turn order, backend
state). Config-derived activity models (``activity_probability``,
``activity_markov``) live at the sim layer as participation policies —
``sim.engine.participation`` — which filter the step roster before any GM's
next_acting runs.
"""

from __future__ import annotations

from collections.abc import Sequence

from silisocs.environments.gm.components.base import ComponentState, NextActingComponent


class AllAgentsNextActing(NextActingComponent):
    """Select every configured agent."""

    def __init__(self, *, agent_names: Sequence[str]) -> None:
        super().__init__()
        self._agent_names = list(agent_names)

    def acting_agent_names(self) -> list[str]:
        """Return selected agent names."""
        return list(self._agent_names)


class FixedOrderNextActing(AllAgentsNextActing):
    """Select agents in fixed cyclic order."""

    def __init__(self, *, sequence: Sequence[str] | None = None, agent_names: Sequence[str] = ()):
        super().__init__(agent_names=sequence or agent_names)
        self._idx = 0

    def acting_agent_names(self) -> list[str]:
        """Return the next selected agent name."""
        if not self._agent_names:
            return []
        name = self._agent_names[self._idx % len(self._agent_names)]
        self._idx += 1
        return [name]

    def get_state(self) -> ComponentState:
        return {"idx": self._idx}

    def set_state(self, state: ComponentState) -> None:
        self._idx = int(state.get("idx", self._idx))
