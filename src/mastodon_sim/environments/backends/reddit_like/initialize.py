"""Custom Reddit initialization for OASIS scenarios.

Reddit uses subreddit membership instead of follow graphs.
This initializer properly sets up subreddit subscriptions for agents.
"""

from collections.abc import Mapping, Sequence
from typing import Any

from mastodon_sim.environments.gm.components.base import BackendInitializer


class RedditSubredditInitializer(BackendInitializer):
    """Initialize Reddit with subreddit-based model instead of follow graphs.

    In OASIS, Reddit agents:
    1. Join subreddits (not follow individuals)
    2. See home feed from subscribed subreddit members
    3. Discover content via recommendations + community participation
    """

    def initialize(
        self,
        *,
        sm_app: Any,
        agent_names: Sequence[str],
        init_kwargs: Mapping[str, Any],
    ) -> None:
        """Initialize Reddit backend with subreddit subscriptions.

        Extracts subreddit configuration from social_network config
        and subscribes agents to appropriate subreddits based on roles.
        """
        kwargs = dict(init_kwargs)
        social_network = kwargs.get("social_network", {})

        # Call backend initialize with full kwargs
        # The backend will handle subreddit creation and agent subscriptions
        sm_app.initialize(agent_names=list(agent_names), **kwargs)

        # Additional setup: subscribe agents to subreddits
        # This is delegated to backend.initialize() which reads social_network config
        # and subscribes agents appropriately


class RedditHybridInitializer(BackendInitializer):
    """Initialize Reddit with hybrid model (subreddits primary, optional follows).

    Combines subreddit membership with optional follow relationships
    for agents that want to follow specific users (like news accounts).
    """

    def initialize(
        self,
        *,
        sm_app: Any,
        agent_names: Sequence[str],
        init_kwargs: Mapping[str, Any],
    ) -> None:
        """Initialize with subreddit base + optional follows."""
        kwargs = dict(init_kwargs)
        # Let backend handle full initialization
        sm_app.initialize(agent_names=list(agent_names), **kwargs)
