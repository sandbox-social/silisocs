"""Observation component for the EchoChamberSim replication."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from replications.echo_chambers.components.app import observation_to_text
from silisocs.environments.gm.components.observe import ObservationComponent


class EchoChamberObservation(ObservationComponent):
    """Return selected neighbor opinions instead of a social-media timeline."""

    def __init__(
        self,
        *,
        model: Any,
        agent_names: Sequence[str],
        backend: Any,
        call_to_make_observation: str = "{name}",
        timeline_mode: str | None = None,
        recsys_type: str | None = None,
        timeline_config: dict[str, Any] | None = None,
        timeline_posts: int | None = None,
    ) -> None:
        del call_to_make_observation, timeline_mode, recsys_type, timeline_config, timeline_posts
        super().__init__(model=model, agent_names=agent_names, components=())
        self._backend = backend

    def make_observation(self, agent_name: str) -> str:
        name = self._agent_name(agent_name)
        # The base engine syncs the current step onto the app action logger.
        engine_episode = int(
            getattr(getattr(self._backend, "action_logger", None), "episode_idx", 0)
        )
        replication_episode = max(0, engine_episode - 1)
        if not hasattr(self._backend, "echo_observation_for"):
            raise TypeError("EchoChamberObservation requires an app with echo_observation_for().")
        payload = self._backend.echo_observation_for(name, replication_episode)
        return observation_to_text(payload)
