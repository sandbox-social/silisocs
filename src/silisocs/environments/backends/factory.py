"""Factory function for creating social media app instances.

Provides a single entry point for the game master to instantiate the
correct platform-specific ``SocialMediaApp`` based on configuration.
"""

from typing import Any

from silisocs.environments.backends.base import SocialMediaApp


def create_social_media_app(platform_type: str, **kwargs: Any) -> SocialMediaApp:
    """Create and return a SocialMediaApp for the given platform type.

    Args:
        platform_type: One of ``"mastodon"``, ``"twitter_like"``, ``"reddit_like"``.
        **kwargs: Common keys:
            - ``action_logger``: Logger for recording actions.
            - ``app_description`` (str): Description of the app.
            - ``perform_operations`` (bool): Whether to perform real API calls
              (Mastodon-specific).

    Returns
    -------
        A configured ``SocialMediaApp`` instance.

    Raises
    ------
        ValueError: If ``platform_type`` is not recognized.
    """
    action_logger = kwargs.get("action_logger")
    app_description = kwargs.get("app_description", "")

    if platform_type == "mastodon":
        from silisocs.environments.backends.mastodon.apps import SocialNetworkApp

        return SocialNetworkApp(
            action_logger=action_logger,
            perform_operations=kwargs.get("perform_operations", False),
            app_description=app_description,
        )
    if platform_type == "twitter_like":
        from silisocs.environments.backends.twitter_like.app import TwitterLikeApp

        # Derive DB path from output directory if available
        db_path = kwargs.get("db_path", "twitter_like.db")
        return TwitterLikeApp(
            action_logger=action_logger,
            app_description=app_description,
            db_path=db_path,
        )
    if platform_type == "reddit_like":
        from silisocs.environments.backends.reddit_like.app import RedditLikeApp

        db_path = kwargs.get("db_path", "reddit_like.db")
        return RedditLikeApp(
            action_logger=action_logger,
            app_description=app_description,
            db_path=db_path,
        )
    raise ValueError(
        f"Unknown social media platform type: '{platform_type}'. "
        f"Supported types: 'mastodon', 'twitter_like', 'reddit_like'."
    )
