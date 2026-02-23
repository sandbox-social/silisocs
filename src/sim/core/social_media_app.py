"""SocialMediaApp - abstract base class for all social media platform apps.

This defines the minimal interface that all social media platform apps
must implement to be used as drag-and-drop replacements in the simulation.
Only ``initialize()`` is required; other methods have default no-op
implementations so subclasses only need to override what they need.
"""

import abc
from typing import Any

from sim.core.phone_app import PhoneApp


class SocialMediaApp(PhoneApp, abc.ABC):
    """Base class for all social media platform apps used in the simulation.

    Subclasses wrap a platform engine (e.g. Mastodon API, TwitterLikePlatform,
    RedditLikePlatform) and expose ``@app_action`` decorated methods that agents
    can invoke.

    The only **required** method is ``initialize()``, which sets up platform
    state (users, follow networks, seed posts, etc.) at the start of a
    simulation run.  Everything else (``get_timeline``,
    ``format_timeline_for_observation``, ``parse_and_resolve_action``) has a
    default no-op implementation so simple subclasses can start minimal and
    grow.
    """

    # ---------------------------------------------------------------------- #
    # Required interface
    # ---------------------------------------------------------------------- #

    @abc.abstractmethod
    def initialize(self, agent_names: list[str], **kwargs: Any) -> None:
        """Set up the platform state for a simulation run.

        Args:
            agent_names: List of agent display names (e.g. ``["Alice Smith", "Bob Jones"]``).
            **kwargs: Platform-specific keyword arguments. Common keys include:

                - ``sim_roles`` (dict[str, str]): Agent name → role mapping.
                - ``following_network`` (dict[str, list[str]]): Agent name → list
                  of agents they follow.
                - ``agent_bios`` (dict[str, str]): Agent name → bio text.
                - ``seed_posts`` (dict[str, str]): Agent name → initial post text.

                Reddit-specific:
                - ``subreddits`` (list[dict]): Subreddit configs with name/description.

                Scenario-specific:
                - ``political_labels`` (dict): Any additional labels needed by the
                  scenario.

            Subclasses should document which kwargs they accept and provide
            sensible defaults for any optional ones.
        """
        ...

    # ---------------------------------------------------------------------- #
    # Optional interface (override per platform)
    # ---------------------------------------------------------------------- #

    def get_timeline(self, user_name: str, limit: int = 10) -> list[dict]:
        """Return raw timeline data for a user.

        Args:
            user_name: The display name of the user whose timeline to fetch.
            limit: Maximum number of posts to return.

        Returns
        -------
            A list of dicts, each representing a post in platform-native format.
        """
        return []

    def format_timeline_for_observation(self, timeline: list[dict]) -> str:
        """Convert raw timeline data into a human-readable string for the LLM prompt.

        Args:
            timeline: List of post dicts as returned by ``get_timeline()``.

        Returns
        -------
            A formatted string suitable for inclusion in an agent observation.
        """
        return ""

    def parse_and_resolve_action(self, user_name: str, action_data: dict) -> str:
        """Dispatch a parsed action to the correct ``@app_action`` method.

        Args:
            user_name: The display name of the acting agent.
            action_data: Dict with keys ``action_type``, ``target_id``,
                ``content``, ``reasoning`` as parsed from the LLM output.

        Returns
        -------
            A result string describing the outcome of the action.
        """
        return ""

    def generate_action_prompt(self) -> str:
        """Generate the call-to-action prompt listing all available actions.

        Uses ``self.full_description()`` by default; subclasses can override
        for custom prompt formatting.
        """
        return self.full_description()
