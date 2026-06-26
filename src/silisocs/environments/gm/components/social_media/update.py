"""Social recommendation update component for the Game Master.

This component schedules recommendation updates via the backend's
recommendation system.

Supported algorithms:
- reddit: Hot-score ranking (engagement + recency)
- twitter: TF-IDF based personalization (bio-to-content similarity)
- twhin: Deep embedding-based recommendations  (TWHIN-BERT)

The component initializes the configured recommendation algorithm, then calls
backend.update_recommendations() on schedule; the backend handles computation,
caching, and lazy evaluation.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from typing import Any

from silisocs.environments.gm.components.base import UpdateComponent

logger = logging.getLogger(__name__)


class SocialRecommendationUpdateComponent(UpdateComponent):
    """Schedules recommendation updates via the backend.

    Calls backend.update_recommendations() on a schedule. The backend handles
    all computation, caching, and management of the recommendation system.

    Configuration options:
    - update_every_n_steps: How often to recompute (default: 1 = every step)
    - lazy: Only compute for active users (default: True)
    - max_posts: Max recommendations per user (default: 10)

    For flow-specific recommendation behavior, configure multiple update
    component instances and route them from the game master.
    """

    def __init__(
        self,
        backend: Any | None = None,
        backend_type: str | None = None,
        default_recsys_type: str | None = None,
        update_every_n_steps: int = 1,
        lazy: bool = True,
        max_posts: int = 10,
        user_context_recent_posts: int = 0,
        include_like_trace: bool = False,
        like_trace_window: int = 5,
        like_trace_weight: float = 0.0,
        include_like_trace_in_context: bool = True,
    ):
        """Initialize the update component.

        Args:
            backend: Reference to the social media app backend
            update_every_n_steps: Steps between recommendation updates
            lazy: If True, only compute for active users
            max_posts: Maximum recommended posts per user
        """
        super().__init__()
        self.backend = backend
        self.backend_type = str(backend_type or "").strip()
        self.default_recsys_type = str(default_recsys_type).strip() if default_recsys_type else None
        self.update_every_n_steps = update_every_n_steps
        self.lazy = lazy
        self.max_posts = max_posts
        self.user_context_recent_posts = max(0, int(user_context_recent_posts or 0))
        self.include_like_trace = bool(include_like_trace)
        self.like_trace_window = max(0, int(like_trace_window or 0))
        self.like_trace_weight = max(0.0, min(1.0, float(like_trace_weight or 0.0)))
        self.include_like_trace_in_context = bool(include_like_trace_in_context)
        self._step_count = 0
        self._last_update_episode: int | None = None
        self._initialized_recsys_types: set[str] = set()
        self._recsys_disabled = False

    _SUPPORTED_RECSYS_BY_BACKEND = {
        "twitter_like": {"twitter", "twitter_tfidf", "twhin"},
        "reddit_like": {"reddit", "twhin"},
        "mastodon": set(),
    }

    _DEFAULT_RECSYS_BY_BACKEND = {
        "twitter_like": "twitter",
        "reddit_like": "reddit",
    }

    def _supported_recsys_types(self) -> set[str]:
        """_supported_recsys_types.

        :returns: set[str]
        :rtype: set[str]
        """
        return set(self._SUPPORTED_RECSYS_BY_BACKEND.get(self.backend_type, set()))

    def _effective_default_recsys_type(self) -> str | None:
        """_effective_default_recsys_type.

        :returns: str | None
        :rtype: str | None
        """
        if self.default_recsys_type:
            return self.default_recsys_type
        return self._DEFAULT_RECSYS_BY_BACKEND.get(self.backend_type)

    def validate_recsys_types(self) -> None:
        """Validate configured recsys types for the current backend."""
        configured_types = self._extract_unique_recsys_types()
        supported = self._supported_recsys_types()
        unsupported = sorted(configured_types - supported)
        if unsupported:
            raise ValueError(
                "Unsupported recommendation algorithm(s) for backend "
                f"'{self.backend_type}': {unsupported}. Supported: {sorted(supported)}"
            )

    def _log_recsys_event(self, label: str, data: dict[str, Any]) -> None:
        """Emit recommendation diagnostics to app action logger when available."""
        if not self.backend:
            return
        log_fn = getattr(self.backend, "_log_action_event", None)
        if callable(log_fn):
            try:
                payload = dict(data)
                payload.setdefault("step_count", self._step_count)
                log_fn("system", label, payload)
            except Exception:
                logger.debug("Failed to log recsys diagnostic event: %s", label, exc_info=True)

    def _current_episode(self) -> int | None:
        """Return current engine episode index when available."""
        action_logger = getattr(self.backend, "action_logger", None)
        episode_idx = getattr(action_logger, "episode_idx", None)
        if episode_idx is None:
            return None
        try:
            return int(episode_idx)
        except (TypeError, ValueError):
            return None

    def update(
        self,
        *,
        step: int,
        agents: Sequence[Any],
        context: Any | None = None,
    ) -> None:
        del step, agents, context
        self.update_recommendations()

    def update_recommendations(self) -> None:
        """Run the scheduled recommendation update if due."""
        self._step_count += 1

        if self._recsys_disabled:
            return

        update_interval = max(1, int(self.update_every_n_steps or 1))
        current_episode = self._current_episode()

        # Deduplicate repeated component calls within the same engine episode.
        if current_episode is not None and self._last_update_episode == current_episode:
            return

        # Prefer episode-based scheduling; fallback to call-count when episode metadata
        # is unavailable (e.g., isolated unit contexts).
        schedule_index = current_episode if current_episode is not None else self._step_count
        if schedule_index % update_interval != 0:
            return

        try:
            if not self.backend:
                logger.warning(
                    "SocialRecommendationUpdateComponent has no backend; skipping update"
                )
                self._log_recsys_event(
                    "recsys_update_skipped",
                    {"reason": "no_backend"},
                )
                return

            backend = self.backend

            # Reconcile configured recsys types against what the backend reports
            # as live, rather than trusting our own initialized-set flag. After a
            # checkpoint restore the backend platform is rebuilt empty, so any
            # configured type the backend no longer has live is (re-)initialized
            # here as a lazy self-heal; otherwise the stale flag would suppress
            # re-init forever and update_recommendations would silently no-op.
            recsys_types = self._extract_unique_recsys_types()
            if not recsys_types:
                logger.debug(
                    "No recommendation algorithms configured/supported for backend '%s'; "
                    "skipping recommendation updates.",
                    self.backend_type,
                )
                self._initialized_recsys_types = set()
                self._recsys_disabled = True
                self._log_recsys_event(
                    "recsys_update_skipped",
                    {
                        "reason": "no_recsys_types",
                        "backend_type": self.backend_type,
                    },
                )
                return
            active_types = self._backend_active_recsys_types(backend)
            missing_types = recsys_types - active_types
            if missing_types and hasattr(backend, "init_recsys"):
                for recsys_type in sorted(missing_types):
                    backend.init_recsys(
                        recsys_type=recsys_type,
                        user_context_recent_posts=self.user_context_recent_posts,
                        include_like_trace=self.include_like_trace,
                        like_trace_window=self.like_trace_window,
                        like_trace_weight=self.like_trace_weight,
                        include_like_trace_in_context=self.include_like_trace_in_context,
                    )
                    logger.info("Initialized recsys type: %s", recsys_type)
                if active_types or self._initialized_recsys_types:
                    self._log_recsys_event(
                        "recsys_reinit_after_restore",
                        {
                            "backend_type": self.backend_type,
                            "reinitialized_types": sorted(missing_types),
                        },
                    )
            self._initialized_recsys_types = recsys_types

            # Update recommendations via backend
            if hasattr(backend, "update_recommendations"):
                self._log_recsys_event(
                    "recsys_update_attempt",
                    {
                        "backend_type": self.backend_type,
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
                        "backend_type": self.backend_type,
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
                        "backend_type": self.backend_type,
                        "backend_class": backend.__class__.__name__,
                    },
                )

        except Exception as e:
            logger.error(f"Error updating recommendations: {e}", exc_info=True)
            self._log_recsys_event(
                "recsys_update_error",
                {
                    "backend_type": self.backend_type,
                    "error": str(e),
                    "recsys_types": sorted(self._initialized_recsys_types),
                },
            )

        return

    def _extract_unique_recsys_types(self) -> set[str]:
        """Extract unique configured recommendation types."""
        unique_types: set[str] = set()
        if self.default_recsys_type:
            unique_types.add(self.default_recsys_type)
        return unique_types

    @staticmethod
    def _backend_active_recsys_types(backend: Any) -> set[str]:
        """Return the recsys types the backend currently has live (source of truth).

        Used to reconcile after a checkpoint restore: the backend platform is
        rebuilt empty on restore, so this returns an empty set and the component
        re-initializes the configured types. Backends that don't expose the hook
        report no live types (safe default).
        """
        getter = getattr(backend, "recsys_active_types", None)
        if not callable(getter):
            return set()
        try:
            return {str(value).strip() for value in getter() if str(value).strip()}
        except Exception:  # pragma: no cover - defensive
            return set()

    def get_state(self) -> dict[str, Any]:
        """Return serializable component state for checkpoints."""
        return {
            "step_count": self._step_count,
            "last_update_episode": self._last_update_episode,
            "initialized_recsys_types": sorted(self._initialized_recsys_types),
            "recsys_disabled": self._recsys_disabled,
        }

    def set_state(self, state: Mapping[str, Any]) -> None:
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
