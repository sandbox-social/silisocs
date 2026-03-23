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

from mastodon_sim.environments.gm.components.base import FlowComponent

if TYPE_CHECKING:
    from mastodon_sim.environments import environment

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
        update_every_n_steps: int = 1,
        lazy: bool = True,
        max_posts: int = 10,
        **kwargs: Any,
    ):
        """Initialize the recommendation component.

        Args:
            update_every_n_steps: Steps between recommendation updates
            lazy: If True, only compute for active users
            max_posts: Maximum recommended posts per user
        """
        super().__init__()
        self.update_every_n_steps = update_every_n_steps
        self.lazy = lazy
        self.max_posts = max_posts
        self._step_count = 0
        self._initialized_recsys_types: set[str] = set()

    def __call__(
        self,
        game_state: Any,
        environment_object: environment.Environment,
    ) -> Any:
        """Update recommendations if scheduled."""
        self._step_count += 1

        # Check if it's time to update
        if self._step_count % self.update_every_n_steps != 0:
            return game_state

        try:
            backend = environment_object.backend

            # Initialize all unique recsys types on first call
            if not self._initialized_recsys_types:
                recsys_types = self._extract_unique_recsys_types()
                if hasattr(backend, "init_recsys"):
                    for recsys_type in recsys_types:
                        backend.init_recsys(recsys_type=recsys_type)
                        logger.info(f"Initialized recsys type: {recsys_type}")
                self._initialized_recsys_types = recsys_types

            # Get active users if lazy evaluation enabled
            active_user_ids = None
            if self.lazy and hasattr(game_state, "active_entities"):
                active_user_ids = [e.agent_id for e in game_state.active_entities]

            # Update recommendations via backend
            if hasattr(backend, "update_recommendations"):
                backend.update_recommendations(
                    active_user_ids=active_user_ids,
                    lazy=self.lazy,
                    max_posts=self.max_posts,
                )

                logger.debug(
                    f"Updated recommendations (step {self._step_count}, "
                    f"algorithms: {self._initialized_recsys_types}, lazy: {self.lazy})"
                )
            else:
                logger.warning("Backend does not support recommendations")

        except Exception as e:
            logger.error(f"Error updating recommendations: {e}")

        return game_state

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
