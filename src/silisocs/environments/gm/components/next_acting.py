"""Native next-acting components for game masters."""

from __future__ import annotations

import random
from collections.abc import Mapping, Sequence

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


class ActivityMarkovNextActing(AllAgentsNextActing):
    """Select active actors using role-conditioned activity transitions."""

    def __init__(
        self,
        *,
        agent_names: Sequence[str],
        activity_transition_rates: Mapping[str, Mapping[str, float]] | None = None,
        sim_roles: Mapping[str, str] | None = None,
    ):
        """__init__."""
        super().__init__(agent_names=agent_names)
        self._activity_transition_rates = dict(activity_transition_rates or {})
        self._sim_roles = dict(sim_roles or {})
        self._users_activity_state: dict[str, int] = dict(
            zip(agent_names, [1] * len(agent_names), strict=False)
        )

    def _rates_for_agent(self, agent_name: str) -> Mapping[str, float]:
        role = self._sim_roles.get(agent_name, "")
        return self._activity_transition_rates.get(
            agent_name,
            self._activity_transition_rates.get(role, {}),
        )

    def acting_agent_names(self) -> list[str]:
        """Return active agents for the current step."""
        for agent_name in self._agent_names:
            last_state = self._users_activity_state[agent_name]
            rates = self._rates_for_agent(agent_name)
            inactive_to_active = float(rates.get("inactive_to_active", 0.3))
            active_to_inactive = float(rates.get("active_to_inactive", inactive_to_active))
            if last_state == 0:
                current_state = 1 if random.random() < inactive_to_active else 0
            else:
                current_state = 0 if random.random() < active_to_inactive else 1
            self._users_activity_state[agent_name] = current_state

        return [
            agent_name
            for agent_name in self._agent_names
            if str(agent_name).strip() and self._users_activity_state.get(agent_name)
        ]

    def get_state(self) -> ComponentState:
        """Return serializable component state."""
        return {"users_activity_state": dict(self._users_activity_state)}

    def set_state(self, state: ComponentState) -> None:
        """Restore component state."""
        self._users_activity_state = dict(state["users_activity_state"])


class ActivityProbabilityNextActing(AllAgentsNextActing):
    """Select active actors independently each step using a fixed probability."""

    def __init__(
        self,
        *,
        agent_names: Sequence[str],
        activity_transition_rates: Mapping[str, Mapping[str, float]] | None = None,
        sim_roles: Mapping[str, str] | None = None,
        active_probability: float | None = None,
        min_active_agents: int = 0,
    ):
        """__init__."""
        super().__init__(agent_names=agent_names)
        self._activity_transition_rates = dict(activity_transition_rates or {})
        self._sim_roles = dict(sim_roles or {})
        self._global_active_probability = active_probability
        self._min_active_agents = max(0, int(min_active_agents))

    def _agent_probability(self, agent_name: str) -> float:
        """Return the current activation probability for one agent."""
        if self._global_active_probability is not None:
            return max(0.0, min(1.0, float(self._global_active_probability)))

        role = self._sim_roles.get(agent_name, "")
        rates = self._activity_transition_rates.get(
            agent_name,
            self._activity_transition_rates.get(role, {}),
        )
        p = rates.get("inactive_to_active")
        if p is None:
            p = rates.get("active_to_inactive")
        if p is None:
            p = 0.3
        return max(0.0, min(1.0, float(p)))

    def acting_agent_names(self) -> list[str]:
        """Return active agents for the current step."""
        active: list[str] = [
            name
            for name in self._agent_names
            if str(name).strip() and random.random() < self._agent_probability(name)
        ]

        # Optional safety guard to avoid pathological all-inactive turns.
        if self._min_active_agents > 0 and len(active) < self._min_active_agents:
            remaining = [n for n in self._agent_names if n not in active]
            random.shuffle(remaining)
            need = self._min_active_agents - len(active)
            active.extend(remaining[:need])

        return active

    def get_state(self) -> ComponentState:
        """Return serializable component state."""
        return {
            "global_active_probability": self._global_active_probability,
            "min_active_agents": self._min_active_agents,
        }

    def set_state(self, state: ComponentState) -> None:
        """Restore component state."""
        self._global_active_probability = state.get("global_active_probability")
        self._min_active_agents = int(state.get("min_active_agents", 0))
