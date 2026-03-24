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

from mastodon_sim.environments.gm.components.base import FlowComponent

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

    Multi-flow configuration via 'entities' field:
        recommend:
          entities:
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
        self._step_count = 0
        self._initialized_recsys_types: set[str] = set()
        self._recsys_disabled = False

    _SUPPORTED_RECSYS_BY_PLATFORM = {
        "twitter_like": {"twitter", "twhin"},
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

        # Check if it's time to update
        if self._step_count % self.update_every_n_steps != 0:
            return ""

        try:
            if not self.sm_app:
                logger.warning("RecommendationComponent has no sm_app; skipping update")
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
                    return ""
                if hasattr(backend, "init_recsys"):
                    for recsys_type in recsys_types:
                        backend.init_recsys(recsys_type=recsys_type)
                        logger.info(f"Initialized recsys type: {recsys_type}")
                self._initialized_recsys_types = recsys_types

            # Update recommendations via backend
            if hasattr(backend, "update_recommendations"):
                backend.update_recommendations(
                    active_user_ids=None,  # Not available from ActionSpec
                    max_posts=self.max_posts,
                )

                logger.debug(
                    f"Updated recommendations (step {self._step_count}, "
                    f"algorithms: {self._initialized_recsys_types})"
                )
            else:
                logger.warning("Backend does not support recommendations")

        except Exception as e:
            logger.error(f"Error updating recommendations: {e}", exc_info=True)

        return ""  # Passive component, no text output

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

    def get_output_dict(self) -> dict[str, Any]:
        """Return component output metadata."""
        return {
            "recsys_enabled": True,
            "recsys_types": list(self._initialized_recsys_types),
        }
