"""Social-media observation components."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from silisocs.environments.gm.components.observe import ObservationComponent


class TimelineMakeObservation(ObservationComponent):
    """Fetch a formatted social-media timeline for the active agent."""

    # Reads a timeline through SocialBackendApp-only methods; the GM refuses to
    # build this component on a backend that has none.
    requires_social_backend = True

    # Timeline reads + thread-safe event logging only; safe without the GM lock,
    # so a step's timeline fetches run concurrently on thread-local connections.
    read_only = True

    # Mid-run tunable via the generic set_component_params intervention (and its
    # set_recsys sugar); applied through the set_* methods below. Both values are
    # read per make_observation call and the component is stateless, so retuning
    # is replay-safe.
    runtime_tunable = frozenset({"recsys_type", "timeline_mode"})

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
        log_exposures: bool = True,
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
        # Record what each agent SAW (post ids + source) to exposure_events.jsonl.
        self._log_exposures = bool(log_exposures)

    def set_recsys_type(self, recsys_type: str) -> None:
        """Swap the recommender agents SEE (mid-run ``set_recsys`` intervention)."""
        self._default_recsys_type = str(recsys_type or "").strip() or None

    def set_timeline_mode(self, timeline_mode: str) -> None:
        """Swap the timeline composition mode (mid-run ``set_component_params``)."""
        self._timeline_mode = str(timeline_mode or "follower_chronological").strip()

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
        if self._log_exposures:
            self._record_exposure(active_agent_name, timeline)
        result = (
            "STARTING SOCIAL MEDIA SESSION\n\n TIMELINE:\n\n"
            + self._backend.format_timeline_for_observation(timeline)
        )
        return result if result.strip() else "## Timeline\n\nNo posts available in your feed yet."

    def _record_exposure(self, agent_name: str, timeline: Any) -> None:
        """Log the ids + source of the posts shown to ``agent_name`` this turn.

        Records what was SHOWN (the post-dedup, post-limit timeline). Best-effort:
        exposure logging must never fail an observe, and it no-ops for backends
        whose timeline isn't a list of post dicts (e.g. Mastodon).
        """
        exposure_logger = getattr(self._backend, "exposure_logger", None)
        if exposure_logger is None or not isinstance(timeline, list):
            return
        try:
            posts = [
                {
                    "id": post.get("id", post.get("post_id")),
                    "author": post.get("username"),
                    "source": post.get("source"),
                    "rank": rank,
                }
                for rank, post in enumerate(timeline)
                if isinstance(post, dict)
            ]
            exposure_logger.log(
                {
                    "agent": agent_name,
                    "timeline_mode": self._timeline_mode,
                    "recsys_type": self._default_recsys_type or "",
                    "posts": posts,
                }
            )
        except Exception:  # pragma: no cover - logging must never break an observe
            pass
