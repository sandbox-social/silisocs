"""Social-media observation components."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from silisocs.environments.gm.components.observe import ObservationComponent


class TimelineMakeObservation(ObservationComponent):
    """Fetch a formatted social-media timeline for the active agent."""

    def __init__(
        self,
        *,
        model: Any,
        agent_names: Sequence[str],
        backend: Any,
        agent_flow_tags: dict[str, str] | None = None,
        episode_observation_flows: Sequence[str] | None = None,
        timeline_mode: str | None = None,
        recsys_type: str | None = None,
        timeline_posts: int = 10,
        timeline_config: dict[str, Any] | None = None,
    ):
        super().__init__(model=model, agent_names=agent_names, components=())
        self._backend = backend
        self._agent_flow_tags = dict(agent_flow_tags or {})
        self._episode_observation_flows = {
            str(flow).strip() for flow in (episode_observation_flows or ()) if str(flow).strip()
        }
        self._timeline_mode = str(timeline_mode or "follower_chronological").strip()
        self._default_recsys_type = str(recsys_type or "").strip() or None
        self._timeline_posts = int(timeline_posts)
        self._timeline_config = dict(timeline_config or {})

    def make_observation(self, agent_name: str) -> str:
        active_agent_name = self._agent_name(agent_name)
        flow_type = self._agent_flow_tags.get(active_agent_name, "default")
        if flow_type in self._episode_observation_flows:
            current_episode = getattr(
                getattr(self._backend, "action_logger", None), "episode_idx", 0
            )
            return f"EPISODE: {current_episode}"

        timeline = self._backend.get_timeline_mode(
            self._timeline_mode,
            active_agent_name,
            self._timeline_posts,
            recsys_type=self._default_recsys_type,
            **self._timeline_config,
        )
        result = (
            "STARTING SOCIAL MEDIA SESSION\n\n TIMELINE:\n\n"
            + self._backend.format_timeline_for_observation(timeline)
        )
        return result if result.strip() else "## Timeline\n\nNo posts available in your feed yet."
