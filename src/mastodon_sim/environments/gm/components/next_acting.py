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
            inactive_to_active = rates["inactive_to_active"]
            active_to_inactive = rates["active_to_inactive"]
            if last_state == 0:
                current_state = 1 if random.random() < inactive_to_active else 0
            else:
                current_state = 0 if random.random() < active_to_inactive else 1
            self._users_activity_state[entity_name] = current_state

        return ",".join(
            entity_name
            for entity_name in self._player_names
            if self._users_activity_state[entity_name]
        )

    def get_state(self) -> entity_component.ComponentState:
        """Return serializable component state."""
        return {"users_activity_state": dict(self._users_activity_state)}

    def set_state(self, state: entity_component.ComponentState) -> None:
        """Restore component state."""
        self._users_activity_state = dict(state["users_activity_state"])


class AllEntitiesNextActing(next_acting_components.NextActingAllEntities):
    """Select all entities each step (simultaneous behavior)."""


class FixedOrderNextActing(next_acting_components.NextActingInFixedOrder):
    """Select entities in fixed cyclic order."""
