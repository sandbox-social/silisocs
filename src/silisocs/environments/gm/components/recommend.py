"""Recommendation system component for the Game Master.

This component schedules recommendation updates via the backend's
recommendation system with configurable recommendation algorithms per flow.

Supported algorithms:
- reddit: Hot-score ranking (engagement + recency)
- twitter: TF-IDF based personalization (bio-to-content similarity)
- twhin: Deep embedding-based recommendations  (TWHIN-BERT)

The component receives flow-to-field mapping at initialization, initializes all
unique recsys types, then calls backend.update_recommendations() on schedule;
the backend handles all computation, caching, and lazy evaluation.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from concordia.typing import entity as entity_lib

from silisocs.environments.gm.components.base import FlowComponent

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


class RecommendationComponent(FlowComponent):
    """Schedules recommendation updates via the backend.

    Calls backend.update_recommendations() on a schedule. The backend handles
    all computation, caching, and management of the recommendation system.

    Configuration options:
    - update_every_n_steps: How often to recompute (default: 1 = every step)
    - lazy: Only compute for active users (default: True)
    - max_posts: Max recommendations per user (default: 10)

        Multi-flow configuration via `flows` field:
                recommend:
                    flows:
                        active:
                            recsys_type: "twitter"
                        lurker:
                            recsys_type: "reddit"
    """

    FLOW_FIELDS = {
        "recsys_type": str,
    }

    _RECSYS_TIMELINE_MODES = {
        "pure_recsys",
        "hybrid_recsys_follower",
    }

    def __init__(
        self,
        sm_app: Any | None = None,
        platform_type: str | None = None,
        default_recsys_type: str | None = None,
        timeline_mode: str | None = None,
        update_every_n_steps: int = 1,
        lazy: bool = True,
        max_posts: int = 10,
        user_context_recent_posts: int = 0,
        include_like_trace: bool = False,
        like_trace_window: int = 5,
        like_trace_weight: float = 0.0,
        include_like_trace_in_context: bool = True,
        **kwargs: Any,
    ):
        """Initialize the recommendation component.

        Args:
            sm_app: Reference to the social media app backend (required for pre_act)
            update_every_n_steps: Steps between recommendation updates
            lazy: If True, only compute for active users
            max_posts: Maximum recommended posts per user
        """
        super().__init__()
        self.sm_app = sm_app
        self.platform_type = str(platform_type or "").strip()
        self.default_recsys_type = str(default_recsys_type).strip() if default_recsys_type else None
        self.timeline_mode = str(timeline_mode or "").strip().lower()
        self.update_every_n_steps = update_every_n_steps
        self.lazy = lazy
        self.max_posts = max_posts
        self.user_context_recent_posts = max(0, int(user_context_recent_posts or 0))
        self.include_like_trace = bool(include_like_trace)
        self.like_trace_window = max(0, int(like_trace_window or 0))
        self.like_trace_weight = max(0.0, min(1.0, float(like_trace_weight or 0.0)))
        self.include_like_trace_in_context = bool(include_like_trace_in_context)
        self._entity: Any | None = None
        self._step_count = 0
        self._last_update_episode: int | None = None
        self._initialized_recsys_types: set[str] = set()
        self._recsys_disabled = False

    _SUPPORTED_RECSYS_BY_PLATFORM = {
        "twitter_like": {"twitter", "twitter_tfidf", "twhin"},
        "reddit_like": {"reddit", "twhin"},
        "mastodon": set(),
    }

    _DEFAULT_RECSYS_BY_PLATFORM = {
        "twitter_like": "twitter",
        "reddit_like": "reddit",
    }

    def _supported_recsys_types(self) -> set[str]:
        return set(self._SUPPORTED_RECSYS_BY_PLATFORM.get(self.platform_type, set()))

    def _effective_default_recsys_type(self) -> str | None:
        if self.default_recsys_type:
            return self.default_recsys_type
        return self._DEFAULT_RECSYS_BY_PLATFORM.get(self.platform_type)

    def validate_recsys_types(self) -> None:
        """Validate configured recsys types for the current backend platform."""
        configured_types = self._extract_unique_recsys_types()
        supported = self._supported_recsys_types()
        unsupported = sorted(configured_types - supported)
        if unsupported:
            raise ValueError(
                "Unsupported recommendation algorithm(s) for platform "
                f"'{self.platform_type}': {unsupported}. Supported: {sorted(supported)}"
            )

    def _log_recsys_event(self, label: str, data: dict[str, Any]) -> None:
        """Emit recommendation diagnostics to app action logger when available."""
        if not self.sm_app:
            return
        log_fn = getattr(self.sm_app, "_log_action_event", None)
        if callable(log_fn):
            try:
                payload = dict(data)
                payload.setdefault("step_count", self._step_count)
                log_fn("system", label, payload)
            except Exception:
                logger.debug("Failed to log recsys diagnostic event: %s", label, exc_info=True)

    def _current_episode(self) -> int | None:
        """Return current engine episode index when available."""
        action_logger = getattr(self.sm_app, "action_logger", None)
        episode_idx = getattr(action_logger, "episode_idx", None)
        if episode_idx is None:
            return None
        try:
            return int(episode_idx)
        except (TypeError, ValueError):
            return None

    def pre_act(self, action_spec: entity_lib.ActionSpec) -> str:
        """Called each step by Concordia's EntityAgent to update recommendations.

        Args:
            action_spec: The current actionspec (unused, but required by Concordia interface)

        Returns
        -------
            Empty string (passive component with no observation output)
        """
        del action_spec  # unused
        self._step_count += 1

        if self._recsys_disabled:
            return ""

        update_interval = max(1, int(self.update_every_n_steps or 1))
        current_episode = self._current_episode()

        # Deduplicate repeated component calls within the same engine episode.
        if current_episode is not None and self._last_update_episode == current_episode:
            return ""

        # Prefer episode-based scheduling; fallback to call-count when episode metadata
        # is unavailable (e.g., isolated unit contexts).
        schedule_index = current_episode if current_episode is not None else self._step_count
        if schedule_index % update_interval != 0:
            return ""

        try:
            if not self.sm_app:
                logger.warning("RecommendationComponent has no sm_app; skipping update")
                self._log_recsys_event(
                    "recsys_update_skipped",
                    {"reason": "no_sm_app"},
                )
                return ""

            backend = self.sm_app

            # Initialize all unique recsys types on first call
            if not self._initialized_recsys_types:
                recsys_types = self._extract_unique_recsys_types()
                if not recsys_types:
                    logger.debug(
                        "No recommendation algorithms configured/supported for platform '%s'; "
                        "skipping recommendation updates.",
                        self.platform_type,
                    )
                    self._initialized_recsys_types = set()
                    self._recsys_disabled = True
                    self._log_recsys_event(
                        "recsys_update_skipped",
                        {
                            "reason": "no_recsys_types",
                            "platform_type": self.platform_type,
                        },
                    )
                    return ""
                if hasattr(backend, "init_recsys"):
                    for recsys_type in recsys_types:
                        backend.init_recsys(
                            recsys_type=recsys_type,
                            user_context_recent_posts=self.user_context_recent_posts,
                            include_like_trace=self.include_like_trace,
                            like_trace_window=self.like_trace_window,
                            like_trace_weight=self.like_trace_weight,
                            include_like_trace_in_context=self.include_like_trace_in_context,
                        )
                        logger.info(f"Initialized recsys type: {recsys_type}")
                self._initialized_recsys_types = recsys_types

            # Update recommendations via backend
            if hasattr(backend, "update_recommendations"):
                self._log_recsys_event(
                    "recsys_update_attempt",
                    {
                        "platform_type": self.platform_type,
                        "recsys_types": sorted(self._initialized_recsys_types),
                        "max_posts": int(self.max_posts),
                        "episode_idx": current_episode,
                    },
                )
                backend.update_recommendations(
                    active_user_ids=None,  # Not available from ActionSpec
                    max_posts=self.max_posts,
                )

                if current_episode is not None:
                    self._last_update_episode = current_episode

                logger.debug(
                    f"Updated recommendations (step {self._step_count}, "
                    f"algorithms: {self._initialized_recsys_types})"
                )
                self._log_recsys_event(
                    "recsys_update_complete",
                    {
                        "platform_type": self.platform_type,
                        "recsys_types": sorted(self._initialized_recsys_types),
                        "max_posts": int(self.max_posts),
                        "episode_idx": current_episode,
                    },
                )
            else:
                logger.warning("Backend does not support recommendations")
                self._log_recsys_event(
                    "recsys_update_skipped",
                    {
                        "reason": "backend_missing_update_recommendations",
                        "platform_type": self.platform_type,
                        "backend_class": backend.__class__.__name__,
                    },
                )

        except Exception as e:
            logger.error(f"Error updating recommendations: {e}", exc_info=True)
            self._log_recsys_event(
                "recsys_update_error",
                {
                    "platform_type": self.platform_type,
                    "error": str(e),
                    "recsys_types": sorted(self._initialized_recsys_types),
                },
            )

        return ""  # Passive component, no text output

    def post_act(self, action_attempt: str) -> str:
        """Concordia post-act hook for passive components.

        Recommendation updates are handled in ``pre_act``; this hook exists only
        to satisfy the component lifecycle expected by ``EntityAgent``.
        """
        del action_attempt  # unused
        return ""

    def update(self) -> None:
        """Concordia update-phase hook for passive components."""
        return

    def _extract_unique_recsys_types(self) -> set[str]:
        """Extract unique recsys_type values from flow:field mapping."""
        unique_types: set[str] = set()

        # Iterate through all flows and their field mappings
        for flow_name, fields in self._flow_field_values.items():
            del flow_name
            recsys_type = str(fields.get("recsys_type", "") or "").strip()
            if not recsys_type:
                continue
            unique_types.add(recsys_type)

        # If recsys timeline mode is active and no flow-specific override exists,
        # use platform default when available.
        if not unique_types and self.timeline_mode in self._RECSYS_TIMELINE_MODES:
            default_type = self._effective_default_recsys_type()
            if default_type:
                unique_types.add(default_type)

        return unique_types

    def update_from_context(self, context: str) -> None:
        """Update component state from LLM context (no-op)."""

    def set_entity(self, entity: Any) -> None:
        """Bind owning entity (Concordia component contract)."""
        self._entity = entity

    def get_entity(self) -> Any:
        """Return owning entity if bound."""
        if self._entity is None:
            raise RuntimeError("RecommendationComponent entity not set")
        return self._entity

    def get_state(self) -> dict[str, Any]:
        """Return serializable component state for checkpoints."""
        return {
            "step_count": self._step_count,
            "last_update_episode": self._last_update_episode,
            "initialized_recsys_types": sorted(self._initialized_recsys_types),
            "recsys_disabled": self._recsys_disabled,
        }

    def set_state(self, state: dict[str, Any]) -> None:
        """Restore serializable component state."""
        state = dict(state or {})
        self._step_count = int(state.get("step_count", self._step_count))
        last_update_episode = state.get("last_update_episode", self._last_update_episode)
        self._last_update_episode = (
            int(last_update_episode) if last_update_episode is not None else None
        )
        restored_types = state.get("initialized_recsys_types", [])
        self._initialized_recsys_types = {str(v).strip() for v in restored_types if str(v).strip()}
        self._recsys_disabled = bool(state.get("recsys_disabled", self._recsys_disabled))

    def get_output_dict(self) -> dict[str, Any]:
        """Return component output metadata."""
        return {
            "recsys_enabled": True,
            "recsys_types": list(self._initialized_recsys_types),
        }
