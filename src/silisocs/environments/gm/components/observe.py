"""Generic native observation components for game masters."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from silisocs.environments.gm.components.base import (
    ObservationComponent as ObservationComponentBase,
)


class ObservationComponent(ObservationComponentBase):
    """Base class for native per-agent observation components."""

    def __init__(
        self,
        *,
        model: Any | None = None,
        agent_names: Sequence[str] = (),
        components: Sequence[str] = (),
    ) -> None:
        del model, components
        super().__init__()
        self._agent_names = list(agent_names)

    def _agent_name(self, agent_name: str) -> str:
        name = str(agent_name or "").strip()
        if self._agent_names and name not in self._agent_names:
            return name
        return name


class AppObservationComponent(ObservationComponent):
    """Generic observation component that delegates to ``BackendApp.observe``."""

    def __init__(
        self,
        *,
        model: Any,
        agent_names: Sequence[str],
        backend: Any | None = None,
        agent_flow_tags: dict[str, str] | None = None,
        observation_params: dict[str, Any] | None = None,
    ):
        """Initialize a generic app-backed observation component."""
        ObservationComponent.__init__(
            self,
            model=model,
            agent_names=agent_names,
            components=(),
        )
        self._backend = backend
        self._agent_flow_tags = dict(agent_flow_tags or {})
        self._observation_params = dict(observation_params or {})

    def make_observation(self, agent_name: str) -> str:
        """Return the environment app observation for one agent."""
        active_agent_name = self._agent_name(agent_name)
        flow_tag = self._agent_flow_tags.get(active_agent_name, "default")
        current_episode = getattr(getattr(self._backend, "action_logger", None), "episode_idx", 0)
        observe = getattr(self._backend, "observe", None)
        if not callable(observe):
            result = ""
        else:
            result = str(
                observe(
                    active_agent_name,
                    step=current_episode,
                    flow_tag=flow_tag,
                    **self._observation_params,
                )
            )

        return result


class EpisodeObservation(ObservationComponent):
    """Return episode number instead of timeline for specific flows.

    Used to differentiate agent behavior based on flow tags
    (e.g., fixed_pre agents see only episode numbers, not timelines).
    """

    def __init__(
        self,
        *,
        model: Any,
        agent_names: Sequence[str],
        backend: Any,
        agent_flow_tags: dict[str, str] | None = None,
        episode_observation_flow: str = "fixed_pre",
    ):
        """__init__."""
        super().__init__(
            model=model,
            agent_names=agent_names,
            components=(),
        )
        self._backend = backend
        self._agent_flow_tags = dict(agent_flow_tags or {})
        self._episode_observation_flow = str(episode_observation_flow).strip()

    def make_observation(self, agent_name: str) -> str:
        """Return episode number if agent is in the episode observation flow."""
        active_agent_name = self._agent_name(agent_name)
        flow_type = self._agent_flow_tags.get(active_agent_name, "default")
        if flow_type != self._episode_observation_flow:
            return ""

        current_episode = getattr(getattr(self._backend, "action_logger", None), "episode_idx", 0)
        result = f"EPISODE: {current_episode}"
        return result
