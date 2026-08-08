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

import inspect
import logging
from collections.abc import Mapping, Sequence
from typing import Any

from silisocs.environments.gm.components.base import UpdateComponent
from silisocs.runtime.telemetry.collector import SimMetricsCollector

logger = logging.getLogger(__name__)

# One scheduled refresh failed; the run continues on the previous rows.
RECSYS_UPDATE_FAILURE_COUNTER = "recsys_update_failures"


class SocialRecommendationUpdateComponent(UpdateComponent):
    """Schedules recommendation updates via the backend.

    Requires a SocialBackendApp (recsys reconciliation); the GM refuses to build
    it on a backend without one.

    Calls backend.update_recommendations() on a schedule. The backend handles
    all computation, caching, and management of the recommendation system.

    Configuration options:
    - update_every_n_steps: How often to recompute (default: 1 = every step)
    - lazy: Only compute for active users (default: True)
    - max_posts: Max recommendations per user (default: 10)

    For flow-specific recommendation behavior, configure multiple update
    component instances and route them from the game master.
    """

    requires_social_backend = True

    # Mid-run tunable via the generic set_component_params intervention:
    # recsys_type goes through set_recsys_type below; the rest are plain
    # attributes read on each scheduled update, safe to reassign at the
    # single-threaded step boundary where interventions fire.
    runtime_tunable = frozenset({"recsys_type", "update_every_n_steps", "lazy", "max_posts"})

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
        self._supports_active_scoping: bool | None = None
        # Active agents seen since the last refresh. With update_every_n_steps
        # > 1, a scoped refresh on the due step must cover everyone who acted
        # on the skipped steps too — refreshing only the due step's roster
        # would leave an agent who was active only on non-due steps with
        # permanently stale (or empty) recommendation rows.
        self._pending_scoped_names: set[str] = set()

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
        """Return the recsys types supported by the current backend."""
        return set(self._SUPPORTED_RECSYS_BY_BACKEND.get(self.backend_type, set()))

    def _effective_default_recsys_type(self) -> str | None:
        """Return the effective default recsys type for the current backend."""
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
        del step, context
        # ``agents`` is the step's ACTIVE roster (the engine applies participation
        # before GM updates). Under lazy scoping (the default) only these users'
        # recommendation rows are recomputed each step — O(active), not
        # O(population); ``lazy: false`` keeps the full-population recompute.
        active_names = (
            [str(getattr(agent, "name", "") or "") for agent in agents] if self.lazy else None
        )
        self.update_recommendations(active_agent_names=active_names)

    def update_recommendations(self, active_agent_names: Sequence[str] | None = None) -> None:
        """Run the scheduled recommendation update if due.

        ``active_agent_names`` scopes the recompute to those agents' users (rows
        for everyone else are left as-is); ``None`` recomputes the full
        population. Backends that don't accept the scoping parameter get the
        full-population call, preserving custom-backend compatibility.
        """
        self._step_count += 1

        if self._recsys_disabled:
            return

        # Accumulate this step's active roster BEFORE any scheduling early
        # return, so agents active on skipped (non-due) steps are covered by
        # the next due refresh.
        if active_agent_names is not None:
            self._pending_scoped_names.update(
                name for name in (str(n or "") for n in active_agent_names) if name
            )

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

        if not self.backend:
            logger.warning("SocialRecommendationUpdateComponent has no backend; skipping update")
            self._log_recsys_event(
                "recsys_update_skipped",
                {"reason": "no_backend"},
            )
            return

        if active_agent_names is not None and not self._pending_scoped_names:
            # Nobody has acted since the last refresh; existing rows stay
            # as-is (deliberately NOT a full-population recompute).
            self._log_recsys_event(
                "recsys_update_skipped",
                {"reason": "no_active_agents", "backend_type": self.backend_type},
            )
            return

        backend = self.backend

        # Reconcile configured recsys types against what the backend reports as
        # live (not our own flag): after a checkpoint restore the backend is
        # rebuilt empty, so any configured-but-missing type is re-initialized here
        # as a lazy self-heal. Otherwise a stale flag would suppress re-init and
        # update_recommendations would silently no-op.
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
            # NOT guarded: a recsys the scenario explicitly configured that cannot
            # be initialized (unsupported type, missing embedding dependency) is a
            # config error. Swallowing it ran the whole scenario with no
            # recommender at all — the one thing it asked for.
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

        if not hasattr(backend, "update_recommendations"):
            logger.warning("Backend does not support recommendations")
            self._log_recsys_event(
                "recsys_update_skipped",
                {
                    "reason": "backend_missing_update_recommendations",
                    "backend_type": self.backend_type,
                    "backend_class": backend.__class__.__name__,
                },
            )
            return

        scoped = active_agent_names is not None and self._backend_supports_active_scoping(backend)
        self._log_recsys_event(
            "recsys_update_attempt",
            {
                "backend_type": self.backend_type,
                "recsys_types": sorted(self._initialized_recsys_types),
                "max_posts": int(self.max_posts),
                "episode_idx": current_episode,
                "active_agents": len(self._pending_scoped_names) if scoped else None,
            },
        )
        try:
            if scoped:
                # Refresh everyone active since the last refresh (this
                # step's roster plus any skipped steps' rosters).
                backend.update_recommendations(
                    active_agent_names=sorted(self._pending_scoped_names),
                    max_posts=self.max_posts,
                )
            else:
                backend.update_recommendations(
                    active_user_ids=None,
                    max_posts=self.max_posts,
                )
        except Exception as e:
            # ONE scheduled refresh failed; the run continues on the previous
            # recommendation rows, so this is COUNTED, not raised. The pending
            # roster is deliberately kept so the next due refresh retries it.
            SimMetricsCollector.get().increment_counter(RECSYS_UPDATE_FAILURE_COUNTER)
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

        # Either branch covered every pending agent (a full recompute
        # trivially so); start accumulating fresh for the next window.
        self._pending_scoped_names.clear()

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

    def set_recsys_type(self, recsys_type: str) -> None:
        """Swap the recommender that is COMPUTED (mid-run ``set_recsys`` intervention).

        The reconcile loop in ``update_recommendations`` initializes the new type
        on the next due update; the paired observe component's ``set_recsys_type``
        swaps what agents actually see.
        """
        self.default_recsys_type = str(recsys_type).strip() or None
        # Un-latch: a run that started with no recsys configured latched
        # _recsys_disabled=True; re-enabling one mid-run must clear it or the
        # reconcile/init path is short-circuited and nothing is ever computed.
        if self.default_recsys_type:
            self._recsys_disabled = False

    def _extract_unique_recsys_types(self) -> set[str]:
        """Extract unique configured recommendation types."""
        unique_types: set[str] = set()
        if self.default_recsys_type:
            unique_types.add(self.default_recsys_type)
        return unique_types

    def _backend_supports_active_scoping(self, backend: Any) -> bool:
        """Whether ``backend.update_recommendations`` accepts ``active_agent_names``.

        Custom backends written before the scoping parameter existed keep
        receiving the original full-population call. Resolved once per component
        instance (signature inspection is not free on a per-step path).
        """
        if self._supports_active_scoping is None:
            try:
                parameters = inspect.signature(backend.update_recommendations).parameters
            except (TypeError, ValueError):
                self._supports_active_scoping = False
            else:
                self._supports_active_scoping = "active_agent_names" in parameters
        return self._supports_active_scoping

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
            "pending_scoped_names": sorted(self._pending_scoped_names),
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
        restored_pending = state.get("pending_scoped_names", [])
        self._pending_scoped_names = {str(v) for v in restored_pending if str(v)}

    def get_output_dict(self) -> dict[str, Any]:
        """Return component output metadata."""
        return {
            "recsys_enabled": True,
            "recsys_types": list(self._initialized_recsys_types),
        }
