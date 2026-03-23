"""Recommendation system component for the Game Master.

This component schedules recommendation updates via the backend's
recommendation system, enabling OASIS-compatible scenarios with configurable
recommendation algorithms per flow.

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

    def __init__(
        self,
        sm_app: Any | None = None,
        update_every_n_steps: int = 1,
        lazy: bool = True,
        max_posts: int = 10,
        **kwargs: Any,
    ):
        """Initialize the recommendation component.

        Args:
            sm_app: Reference to the social media app backend (required for pre_act)
            update_every_n_steps: Episodes between recommendation updates (default: 1 = every episode)
            lazy: If True, only compute for active users
            max_posts: Maximum recommended posts per user
        """
        super().__init__()
        self.sm_app = sm_app
        self.update_every_n_steps = update_every_n_steps
        self.lazy = lazy
        self.max_posts = max_posts
        self._last_updated_episode = -1
        self._current_episode = 0
        self._initialized_recsys_types: set[str] = set()

    def pre_act(self, action_spec: entity_lib.ActionSpec) -> str:
        """Called each step by Concordia's EntityAgent to update recommendations.

        Tracks episode number to ensure recommendations update only once per episode,
        regardless of how many times pre_act is called (once per entity).

        Args:
            action_spec: The current actionspec (unused, but required by Concordia interface)

        Returns:
            Empty string (passive component with no observation output)
        """
        del action_spec  # unused

        try:
            if not self.sm_app:
                logger.warning("RecommendationComponent has no sm_app; skipping update")
                return ""

            backend = self.sm_app

            # Get current episode from action logger if available
            if hasattr(backend, "action_logger") and backend.action_logger:
                self._current_episode = getattr(backend.action_logger, "episode_idx", 0)
            else:
                # Fallback: assume this is episode 0 if no logger
                self._current_episode = 0

            # Initialize all unique recsys types on first call
            if not self._initialized_recsys_types:
                recsys_types = self._extract_unique_recsys_types()
                if hasattr(backend, "init_recsys"):
                    for recsys_type in recsys_types:
                        backend.init_recsys(recsys_type=recsys_type)
                        logger.info(f"Initialized recsys type: {recsys_type}")
                self._initialized_recsys_types = recsys_types

            # Check if it's time to update (only once per episode, or every N episodes)
            episodes_since_update = self._current_episode - self._last_updated_episode
            if episodes_since_update < self.update_every_n_steps:
                return ""

            # Update recommendations via backend
            if hasattr(backend, "update_recommendations"):
                backend.update_recommendations(
                    active_user_ids=None,  # Not available from ActionSpec
                    max_posts=self.max_posts,
                )

                logger.debug(
                    f"Updated recommendations (episode {self._current_episode}, "
                    f"algorithms: {self._initialized_recsys_types})"
                )
                self._last_updated_episode = self._current_episode
            else:
                logger.warning("Backend does not support recommendations")

        except Exception as e:
            logger.error(f"Error updating recommendations: {e}", exc_info=True)

        return ""  # Passive component, no text output

    def _extract_unique_recsys_types(self) -> set[str]:
        """Extract unique recsys_type values from flow:field mapping."""
        unique_types: set[str] = set()

        # Iterate through all flows and their field mappings
        for flow_name, fields in self._entity_field_values.items():
            recsys_type = fields.get("recsys_type", "reddit")
            unique_types.add(recsys_type)

        # If no flows configured, use default
        if not unique_types:
            unique_types.add("reddit")

        return unique_types

    def update_from_context(self, context: str) -> None:
        """Update component state from LLM context (no-op)."""
        pass

    def get_output_dict(self) -> dict[str, Any]:
        """Return component output metadata."""
        return {
            "recsys_enabled": True,
            "recsys_types": list(self._initialized_recsys_types),
        }
