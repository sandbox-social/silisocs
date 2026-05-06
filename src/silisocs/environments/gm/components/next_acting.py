"""Concordia-native next-acting components for social-media game masters."""

from __future__ import annotations

import random
from collections.abc import Mapping, Sequence

from concordia.components.game_master import next_acting as next_acting_components
from concordia.typing import entity as entity_lib
from concordia.typing import entity_component


class ActivityMarkovNextActing(next_acting_components.NextActingAllEntities):
    """Select active actors using role-conditioned activity transitions."""

    def __init__(
        self,
        *,
        player_names: Sequence[str],
        activity_transition_rates: Mapping[str, Mapping[str, float]],
    ):
        """__init__."""
        super().__init__(player_names=player_names)
        self._activity_transition_rates = activity_transition_rates
        self._users_activity_state: dict[str, int] = dict(
            zip(player_names, [1] * len(player_names), strict=False)
        )

    def pre_act(self, action_spec: entity_lib.ActionSpec) -> str:
        """Return comma-separated active entities for the current step."""
        if action_spec.output_type != entity_lib.OutputType.NEXT_ACTING:
            return ""

        for entity_name in self._player_names:
            last_state = self._users_activity_state[entity_name]
            rates = self._activity_transition_rates[entity_name]
            inactive_to_active = float(rates.get("inactive_to_active", 0.3))
            active_to_inactive = float(rates.get("active_to_inactive", inactive_to_active))
            if last_state == 0:
                current_state = 1 if random.random() < inactive_to_active else 0
            else:
                current_state = 0 if random.random() < active_to_inactive else 1
            self._users_activity_state[entity_name] = current_state

        return ",".join(
            entity_name
            for entity_name in self._player_names
            if str(entity_name).strip() and self._users_activity_state.get(entity_name)
        )

    def get_state(self) -> entity_component.ComponentState:
        """Return serializable component state."""
        return {"users_activity_state": dict(self._users_activity_state)}

    def set_state(self, state: entity_component.ComponentState) -> None:
        """Restore component state."""
        self._users_activity_state = dict(state["users_activity_state"])


class ActivityProbabilityNextActing(next_acting_components.NextActingAllEntities):
    """Select active actors independently each step using a fixed probability."""

    def __init__(
        self,
        *,
        player_names: Sequence[str],
        activity_transition_rates: Mapping[str, Mapping[str, float]],
        active_probability: float | None = None,
        min_active_agents: int = 0,
    ):
        """__init__."""
        super().__init__(player_names=player_names)
        self._activity_transition_rates = activity_transition_rates
        self._global_active_probability = active_probability
        self._min_active_agents = max(0, int(min_active_agents))

    def _entity_probability(self, entity_name: str) -> float:
        """_entity_probability.

        :param str entity_name:
        :type entity_name: str

        :returns: float
        :rtype: float
        """
        if self._global_active_probability is not None:
            return max(0.0, min(1.0, float(self._global_active_probability)))

        rates = self._activity_transition_rates.get(entity_name, {})
        p = rates.get("inactive_to_active")
        if p is None:
            p = rates.get("active_to_inactive")
        if p is None:
            p = 0.3
        return max(0.0, min(1.0, float(p)))

    def pre_act(self, action_spec: entity_lib.ActionSpec) -> str:
        """pre_act.

        :param entity_lib.ActionSpec action_spec:
        :type action_spec: entity_lib.ActionSpec

        :returns: str
        :rtype: str
        """
        if action_spec.output_type != entity_lib.OutputType.NEXT_ACTING:
            return ""

        active: list[str] = [
            name
            for name in self._player_names
            if str(name).strip() and random.random() < self._entity_probability(name)
        ]

        # Optional safety guard to avoid pathological all-inactive turns.
        if self._min_active_agents > 0 and len(active) < self._min_active_agents:
            remaining = [n for n in self._player_names if n not in active]
            random.shuffle(remaining)
            need = self._min_active_agents - len(active)
            active.extend(remaining[:need])

        return ",".join(active)

    def get_state(self) -> entity_component.ComponentState:
        """get_state.

        :returns: entity_component.ComponentState
        :rtype: entity_component.ComponentState
        """
        return {
            "global_active_probability": self._global_active_probability,
            "min_active_agents": self._min_active_agents,
        }

    def set_state(self, state: entity_component.ComponentState) -> None:
        """set_state.

        :param entity_component.ComponentState state:
        :type state: entity_component.ComponentState

        :returns: None
        :rtype: None
        """
        self._global_active_probability = state.get("global_active_probability")
        self._min_active_agents = int(state.get("min_active_agents", 0))


class AllEntitiesNextActing(next_acting_components.NextActingAllEntities):
    """Select all entities each step (simultaneous behavior)."""


class FixedOrderNextActing(next_acting_components.NextActingInFixedOrder):
    """Select entities in fixed cyclic order."""
