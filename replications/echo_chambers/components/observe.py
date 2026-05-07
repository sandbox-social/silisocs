"""Observation component for the EchoChamberSim replication."""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Any

from concordia.components.game_master import make_observation as make_observation_component
from concordia.typing import entity as entity_lib

from replications.echo_chambers.components.app import observation_to_text


class EchoChamberObservation(make_observation_component.MakeObservation):
    """Return selected neighbor opinions instead of a social-media timeline."""

    def __init__(
        self,
        *,
        model: Any,
        player_names: Sequence[str],
        sm_app: Any,
        call_to_make_observation: str = "{name}",
    ) -> None:
        super().__init__(
            model=model,
            player_names=player_names,
            components=(),
            call_to_make_observation=call_to_make_observation,
        )
        self._sm_app = sm_app

    def _active_name(self, call_to_action: str) -> str:
        match = re.search(r"What is the current situation faced by (.+?)\?", call_to_action)
        if match:
            return match.group(1).strip()
        return str(call_to_action or "").strip()

    def pre_act(self, action_spec: entity_lib.ActionSpec) -> str:
        if action_spec.output_type != entity_lib.OutputType.MAKE_OBSERVATION:
            return ""
        name = self._active_name(action_spec.call_to_action)
        # The base engine syncs the current step onto the app action logger.
        engine_episode = int(
            getattr(getattr(self._sm_app, "action_logger", None), "episode_idx", 0)
        )
        replication_episode = max(0, engine_episode - 1)
        if not hasattr(self._sm_app, "echo_observation_for"):
            raise TypeError("EchoChamberObservation requires an app with echo_observation_for().")
        payload = self._sm_app.echo_observation_for(name, replication_episode)
        result = observation_to_text(payload)
        self._logging_channel(
            {
                "Key": self._pre_act_label,
                "Summary": f"Echo observation for {name} episode {replication_episode}",
                "Value": result,
                "Active Entity": name,
                "Engine Episode": engine_episode,
                "Replication Episode": replication_episode,
            }
        )
        return result
