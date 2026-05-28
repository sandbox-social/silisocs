"""Loose social-media observation for Echo Chamber follow-up studies."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from silisocs.environments.gm.components.observe import ObservationComponent


class EchoBeliefFilteredTimelineObservation(ObservationComponent):
    """Return a real timeline filtered by EchoChamber belief-distance policy."""

    def __init__(
        self,
        *,
        model: Any,
        agent_names: Sequence[str],
        backend: Any,
        timeline_mode: str | None = None,
        recsys_type: str | None = None,
        timeline_config: dict[str, Any] | None = None,
        timeline_posts: int | None = None,
        call_to_make_observation: str = "{name}",
        policy: str = "similarity",
        threshold: int = 2,
        max_posts: int | None = None,
        fetch_multiplier: int = 3,
        include_unclassified_posts: bool = False,
    ) -> None:
        del call_to_make_observation, timeline_posts
        super().__init__(model=model, agent_names=agent_names, components=())
        self._backend = backend
        self._timeline_mode = str(timeline_mode or "follower_chronological")
        self._recsys_type = str(recsys_type or "").strip() or None
        self._timeline_config = dict(timeline_config or {})
        self._policy = str(policy or "similarity").strip().lower()
        self._threshold = int(threshold)
        self._max_posts = int(max_posts) if max_posts is not None else None
        self._fetch_multiplier = max(1, int(fetch_multiplier))
        self._include_unclassified_posts = bool(include_unclassified_posts)

    def _keep_post(self, *, active_name: str, post: dict[str, Any]) -> bool:
        echo_state = getattr(self._backend, "echo_state", None)
        if echo_state is None:
            return True
        if self._policy in {"none", "off", "all", "unfiltered"}:
            return True
        author_name = self._backend.echo_name_for_post(post)
        if author_name == active_name:
            return False
        if author_name is None:
            return self._include_unclassified_posts
        own_belief = int(echo_state.current_beliefs.get(active_name, 0))
        author_belief = int(echo_state.current_beliefs.get(author_name, 0))
        distance = abs(own_belief - author_belief)
        if self._policy == "random":
            return True
        if self._policy == "opposite":
            return distance >= self._threshold
        return distance <= self._threshold

    def make_observation(self, agent_name: str) -> str:
        active_name = self._agent_name(agent_name)
        limit = self._max_posts or 10
        raw_limit = max(limit, limit * self._fetch_multiplier)
        raw_timeline = self._backend.get_timeline_mode(
            self._timeline_mode,
            active_name,
            raw_limit,
            recsys_type=self._recsys_type,
            **self._timeline_config,
        )
        filtered = [
            post for post in raw_timeline if self._keep_post(active_name=active_name, post=post)
        ]
        if self._policy == "random":
            rng = getattr(getattr(self._backend, "echo_state", None), "rng", None)
            if rng is not None:
                rng.shuffle(filtered)
        filtered = filtered[:limit]

        timeline_text = self._backend.format_timeline_for_observation(filtered)
        if not timeline_text.strip():
            timeline_text = "No visible posts matched your current feed policy."

        echo_state = getattr(self._backend, "echo_state", None)
        topic = str(getattr(echo_state, "topic", "the discussion topic")) if echo_state else ""
        return f"STARTING SOCIAL MEDIA SESSION\n\nTopic: {topic}\nTIMELINE:\n{timeline_text}"
