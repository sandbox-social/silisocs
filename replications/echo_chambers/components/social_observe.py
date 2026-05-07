"""Loose social-media observation for Echo Chamber follow-up studies."""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Any

from concordia.components.game_master import make_observation as make_observation_component
from concordia.typing import entity as entity_lib


class EchoBeliefFilteredTimelineObservation(make_observation_component.MakeObservation):
    """Return a real timeline filtered by EchoChamber belief-distance policy."""

    def __init__(
        self,
        *,
        model: Any,
        player_names: Sequence[str],
        sm_app: Any,
        timeline_mode: str | None = None,
        recsys_type: str | None = None,
        timeline_config: dict[str, Any] | None = None,
        call_to_make_observation: str = "{name}",
        policy: str = "similarity",
        threshold: int = 2,
        max_posts: int | None = None,
        fetch_multiplier: int = 3,
        include_unclassified_posts: bool = False,
    ) -> None:
        super().__init__(
            model=model,
            player_names=player_names,
            components=(),
            call_to_make_observation=call_to_make_observation,
        )
        self._sm_app = sm_app
        self._timeline_mode = str(timeline_mode or "follower_chronological")
        self._recsys_type = str(recsys_type or "").strip() or None
        self._timeline_config = dict(timeline_config or {})
        self._policy = str(policy or "similarity").strip().lower()
        self._threshold = int(threshold)
        self._max_posts = int(max_posts) if max_posts is not None else None
        self._fetch_multiplier = max(1, int(fetch_multiplier))
        self._include_unclassified_posts = bool(include_unclassified_posts)

    def _active_name(self, call_to_action: str) -> str:
        match = re.search(r"What is the current situation faced by (.+?)\?", call_to_action)
        if match:
            return match.group(1).strip()
        return str(call_to_action or "").strip()

    def _keep_post(self, *, active_name: str, post: dict[str, Any]) -> bool:
        echo_state = getattr(self._sm_app, "echo_state", None)
        if echo_state is None:
            return True
        if self._policy in {"none", "off", "all", "unfiltered"}:
            return True
        author_name = self._sm_app.echo_name_for_post(post)
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

    def pre_act(self, action_spec: entity_lib.ActionSpec) -> str:
        if action_spec.output_type != entity_lib.OutputType.MAKE_OBSERVATION:
            return ""

        active_name = self._active_name(action_spec.call_to_action)
        limit = self._max_posts or 10
        raw_limit = max(limit, limit * self._fetch_multiplier)
        raw_timeline = self._sm_app.get_timeline_mode(
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
            rng = getattr(getattr(self._sm_app, "echo_state", None), "rng", None)
            if rng is not None:
                rng.shuffle(filtered)
        filtered = filtered[:limit]

        timeline_text = self._sm_app.format_timeline_for_observation(filtered)
        if not timeline_text.strip():
            timeline_text = "No visible posts matched your current feed policy."

        echo_state = getattr(self._sm_app, "echo_state", None)
        topic = str(getattr(echo_state, "topic", "the discussion topic")) if echo_state else ""
        result = f"STARTING SOCIAL MEDIA SESSION\n\nTopic: {topic}\nTIMELINE:\n{timeline_text}"
        self._logging_channel(
            {
                "Key": self._pre_act_label,
                "Summary": (
                    f"Belief-filtered timeline for {active_name}: "
                    f"{len(filtered)}/{len(raw_timeline)} posts"
                ),
                "Value": result,
                "Active Entity": active_name,
                "Policy": self._policy,
            }
        )
        return result
