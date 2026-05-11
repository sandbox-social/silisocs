"""Mastodon social network app for simulation.

This module provides ``SocialNetworkApp``, the Mastodon implementation of
the ``SocialMediaApp`` interface.  It wraps the Mastodon API via
``mastodon_ops`` and exposes ``@app_action`` decorated methods for the
simulation game master.

The generic ``PhoneApp`` base class, ``SocialMediaApp`` ABC, and supporting
utilities (``app_action``, ``Parameter``, ``ActionDescriptor``, etc.) are
imported from ``sim.core``.
"""

import dataclasses
import re
from html import unescape
from typing import Any

# All shared base-class machinery is now in sim.core
# Re-export COLOR_TYPE for any downstream code that imported it from here
from silisocs.environments.backends.base import (  # noqa: F401 – re-exported for backward compat
    COLOR_TYPE,
    ActionArgumentError,
    ActionDescriptor,
    Parameter,
    PhoneApp,
    SocialMediaApp,
    app_action,
)
from silisocs.environments.backends.mastodon.mastodon_ops import (
    check_env,
    clear_mastodon_server,
)

# region[Mastodon Social Network App]


@dataclasses.dataclass
class SocialNetworkApp(SocialMediaApp):
    """Mastodon social network app.
        description = (
            "MastodonSocialNetworkApp is a social media application similar to"
            " Twitter that allows users to interact on social media.\n\n    This"
            " app provides functionality for users to post status updates (toots), follow"
            " other users, like, boost, and respond to posts, and manage their"
            " notifications.\n\n    Critically important: Operations such as"
            " liking, boosting, replying, etc. require a `toot_id`. To obtain a"
            " `toot_id`, you must have memory/knowledge of a real `toot_id`. If you"
            " don't know a `toot_id`, you can't perform actions that require it."
            " `toot_id`'s can be retrieved using the `get_timeline` action."
        )
    A social media application similar to Twitter that allows users to interact on social media.
    """

    action_logger: Any = None
    perform_operations: bool = False
    app_description: str = "MastodonSocialNetworkApp"
    _log_color: COLOR_TYPE = dataclasses.field(default="blue", init=False)
    _mastodon_ops: Any = dataclasses.field(default=None, init=False)
    _user_mapping: dict[str, str] = dataclasses.field(default_factory=dict, init=False)

    def __post_init__(self) -> None:  # noqa: D105
        super().__init__()
        if self.perform_operations:
            from silisocs.environments.backends.mastodon import mastodon_ops

            self._mastodon_ops = mastodon_ops

    def name(self) -> str:
        """Define the name of the app."""
        return "MastodonSocialNetworkApp"

    def description(self) -> str:
        """Define the description of the app."""
        return self.app_description

    def _log_action_event(self, event: dict[str, Any]) -> None:
        """Write an action event when the runtime provided an action logger."""
        if self.action_logger is not None and hasattr(self.action_logger, "log"):
            self.action_logger.log(event)

    # ------------------------------------------------------------------ #
    # SocialMediaApp interface
    # ------------------------------------------------------------------ #

    def initialize(self, agent_names: list[str], **kwargs: Any) -> None:
        """Set up Mastodon users, generate follow network, and create seed posts.

        Uses ``social_network`` config to generate a follow graph (same as
        Twitter-like), then establishes follows and seed posts via the
        Mastodon API.

        Args:
            agent_names: Agent display names.
            **kwargs: ``sim_roles``, ``seed_posts``, ``social_network``,
                ``agent_bios``.
        """
        from silisocs.utils.network import generate_follow_network

        sim_roles = kwargs.get("sim_roles", {})
        seed_posts = kwargs.get("seed_posts", {})
        social_network = kwargs.get("social_network", {})
        agent_bios = kwargs.get("agent_bios", {})

        # Build user mapping.
        user_mapping = {}
        for i, display_name in enumerate(agent_names):
            parts = display_name.strip().split()
            short_name = parts[0] if parts else display_name
            concat_name = f"{parts[0]}{parts[1]}" if len(parts) >= 2 else parts[0]
            username = f"user{i + 1:04d}"
            user_mapping[short_name] = username
            user_mapping[concat_name] = username
        self.set_user_mapping(user_mapping)

        # Set bios/profiles.
        for display_name, bio in agent_bios.items():
            if bio:
                try:
                    self.update_profile(display_name, bio)
                except Exception as e:
                    self._print(f"Error setting bio for {display_name}: {e}", color="red")

        # Generate and establish follow network (graph-based).
        following = generate_follow_network(agent_names, sim_roles, social_network)
        for display_name, followees in following.items():
            for followee in followees:
                try:
                    self.follow_user(display_name, followee)
                except Exception as e:
                    self._print(f"Follow error ({display_name}->{followee}): {e}", color="red")

        # Seed posts.
        for display_name, post_text in seed_posts.items():
            if post_text:
                try:
                    self.post_toot(display_name, post_text)
                except Exception as e:
                    self._print(f"Seed post error for {display_name}: {e}", color="red")

        follow_edges = sum(len(v) for v in following.values())
        self._print(
            f"Initialized {len(agent_names)} users on Mastodon ({follow_edges} follow edges)",
        )

    def get_timeline(self, user_name: str, limit: int = 10) -> list[dict]:
        """Fetch the home timeline for a user from the Mastodon API.

        Mastodon timelines are always server-provided chronologically. For
        consistency with Twitter/Reddit, this supports a 'strategy' parameter,
        but Mastodon returns the same feed regardless since it's federated.

        Args:
            user_name: Display name of the user.
            limit: Maximum number of posts.

        Returns
        -------
            List of Mastodon status dicts.
        """
        try:
            # Re-use the existing get_own_timeline logic
            current_user = f"{user_name.split(maxsplit=1)[0]}{user_name.split()[1]}"
            username = self._get_username(current_user)
            if self.perform_operations:
                timeline = self._mastodon_ops.get_own_timeline(username, limit=limit)
            else:
                timeline = []
            return timeline or []
        except Exception as e:
            self._print(f"Error fetching timeline for {user_name}: {e}", color="red")
            return []

    # Timeline modes for consistency with Twitter/Reddit backends
    TIMELINE_MODES = {
        "follower_chronological": {
            "description": "Home feed from Mastodon server (always chronological, federated)",
            "note": "Mastodon's federated nature means timeline is server-determined",
        },
    }

    def get_timeline_mode(
        self,
        timeline_mode: str,
        user_name: str,
        limit: int = 10,
        recsys_type: str | None = None,
        **timeline_config,
    ) -> list[dict]:
        """Get timeline using the specified timeline mode.

        Mastodon does not support recommendations or algorithmic feeds.
        All modes return the server-provided chronological feed.

        Args:
            timeline_mode: Timeline mode (currently only "follower_chronological")
            user_name: User to get timeline for
            limit: Max posts to return
            recsys_type: Optional recommendation type (unused for Mastodon)
            **timeline_config: Mode parameters (unused for Mastodon)

        Returns
        -------
            List of timeline posts from Mastodon server
        """
        # Mastodon always returns the server's feed, regardless of mode requested.
        del timeline_mode, recsys_type, timeline_config
        return self.get_timeline(user_name, limit)

    # Recommendation APIs are intentionally unsupported for Mastodon.
    def init_recsys(self, recsys_type: str = "") -> None:
        del recsys_type
        raise NotImplementedError(
            "Mastodon backend does not support recommendation algorithms. "
            "Use timeline_mode='follower_chronological'."
        )

    def update_recommendations(
        self, active_user_ids: list[int] | None = None, max_posts: int = 10
    ) -> None:
        del active_user_ids, max_posts
        raise NotImplementedError("Mastodon backend does not support recommendation updates.")

    def get_recommendations(
        self,
        username: str,
        limit: int = 10,
        recsys_type: str | None = None,
    ) -> list[dict]:
        del username, limit, recsys_type
        raise NotImplementedError("Mastodon backend does not support recommendation retrieval.")

    def format_timeline_for_observation(self, timeline: list[dict]) -> str:
        """Format Mastodon timeline posts for LLM observation.

        Cleans HTML tags and formats each post as a readable text block.

        Args:
            timeline: List of Mastodon status dicts.

        Returns
        -------
            Formatted string suitable for inclusion in a prompt.
        """
        return self.print_and_return_timeline(timeline)

    def parse_and_resolve_action(self, user_name: str, action_data: dict) -> str:
        """Dispatch a parsed action to the correct Mastodon app_action method.

        Args:
            user_name: Display name of the acting agent.
            action_data: Dict with ``action_type``, ``target_id``, ``content``,
                ``reasoning``.

        Returns
        -------
            Result string describing the action outcome.
        """
        action_type = action_data.get("action_type", "").lower().strip()
        target_id = action_data.get("target_id", "")
        content = action_data.get("content", "")

        try:
            if action_type in {"finished", "finish", "finish_action_episode"}:
                return self.finish_action_episode()
            if action_type in {"post", "post_toot"}:
                return self.post_toot(user_name, content)
            if action_type == "reply":
                return self.reply_to_toot(
                    user_name,
                    status=content,
                    in_reply_to_id=int(target_id),
                )
            if action_type == "like":
                return self.like_toot(user_name, str(target_id))
            if action_type in ("boost", "repost"):
                return self.boost_toot(user_name, str(target_id))
            return f"Unknown action type: {action_type}"
        except Exception as e:
            self._print(f"Error resolving action {action_type}: {e}", color="red")
            return f"Error performing {action_type}: {e}"

    # ------------------------------------------------------------------ #
    # User mapping management
    # ------------------------------------------------------------------ #

    def set_user_mapping(self, mapping: dict[str, str]) -> None:
        """Set the mapping of display names to usernames."""
        self._user_mapping = mapping
        self._print(f"Updated user mapping with {len(mapping)} entries", emoji="🔄")
        if self.perform_operations:
            check_env()
            clear_mastodon_server(len(self._user_mapping) + 1)

    def get_user_mapping(self) -> dict[str, str]:
        """Get the mapping of display names to usernames."""
        return self._user_mapping

    def _get_username(self, display_name: str) -> str:
        """Get the username for a given display name."""
        current_user = display_name.split(maxsplit=1)[0]
        username = self._user_mapping.get(current_user)
        # self._print(f"Mapped {display_name} to @{username}", emoji="🔗")
        if not username:
            raise ValueError(f"No username found for display name: {display_name}")
        return username

    def public_get_username(self, display_name: str) -> str:
        """Public interface to get the username."""
        return self._get_username(display_name)

    @app_action
    def update_profile(self, current_user: str, bio: str) -> str:
        """Update the user's bio."""
        current_user_full = str(current_user)
        current_user = f"{current_user.split(maxsplit=1)[0]}{current_user.split()[1]}"

        username = self._get_username(current_user)
        self._print(f"Updating profile for @{username}: {current_user}", emoji="✏️")
        if self.perform_operations:
            self._mastodon_ops.update_bio(username, current_user, bio)
        else:
            self._print(
                "Skipping real Mastodon API call since perform_operations is set to False",
                color="light_grey",
            )
        bio_message = f'Profile updated successfully: "{bio}"'
        self._print(bio_message, emoji="✅")
        self._log_action_event(
            {"source_user": current_user_full, "label": "update_profile", "data": {"new_bio": bio}}
        )

        return bio_message

    @app_action
    def read_profile(self, current_user: str, target_user: str) -> tuple[str, str]:
        """Read a user's profile on Mastodon social network."""
        current_user_full = str(current_user)
        current_user = f"{current_user.split(maxsplit=1)[0]}{current_user.split()[1]}"
        target_user_full = str(target_user)
        target_user = f"{target_user.split(maxsplit=1)[0]}{target_user.split()[1]}"

        current_username = self._get_username(current_user)
        target_username = self._get_username(target_user)
        self._print(f"@{current_username} reading profile of @{target_username}", emoji="👀")
        if self.perform_operations:
            try:
                display_name, bio = self._mastodon_ops.read_bio(current_username, target_username)
            except Exception as e:
                self._print(f"Error reading profile of @{target_username}: {e}", color="red")
                display_name, bio = "Error", "Error fetching profile"
        else:
            display_name, bio = "Mock Name", "Mock Bio"
            self._print(
                "Skipping real Mastodon API call since perform_operations is set to False",
                color="light_grey",
            )
        self._print(f"Profile: {display_name} - {bio}", emoji="📄")

        self._log_action_event(
            {
                "source_user": current_user_full,
                "label": "read_profile",
                "data": {"target_user": target_user_full, "bio": bio},
            }
        )
        return display_name, bio

    @app_action
    def follow_user(self, current_user: str, target_user: str) -> str:
        """Follow a user on Mastodon social network."""
        current_user_full = str(current_user)
        current_user = f"{current_user.split(maxsplit=1)[0]}{current_user.split()[1]}"
        target_user_full = str(target_user)
        target_user = f"{target_user.split(maxsplit=1)[0]}{target_user.split()[1]}"
        current_username = self._get_username(current_user)
        target_username = self._get_username(target_user)
        if self.perform_operations:
            self._mastodon_ops.follow(current_username, target_username)
        else:
            self._print(
                "Skipping real Mastodon API call since perform_operations is set to False",
                color="light_grey",
            )
        follow_message = (
            f"current_user (@{current_username}) followed target_user (@{target_username})"
        )
        self._print(follow_message, emoji="➕")  # noqa: RUF001
        self._log_action_event(
            {
                "source_user": current_user_full,
                "label": "follow",
                "data": {"target_user": target_user_full},
            }
        )
        return follow_message

    @app_action
    def unfollow_user(self, current_user: str, target_user: str) -> str:
        """Unfollow a user."""
        current_user_full = str(current_user)
        current_user = f"{current_user.split(maxsplit=1)[0]}{current_user.split()[1]}"
        target_user_full = str(target_user)
        target_user = f"{target_user.split(maxsplit=1)[0]}{target_user.split()[1]}"
        current_username = self._get_username(current_user)
        target_username = self._get_username(target_user)
        self._print(
            f"@{current_username} unfollowing user: @{target_username}",
            emoji="➖",  # noqa: RUF001
        )
        if self.perform_operations:
            self._mastodon_ops.unfollow(current_username, target_username)
        else:
            self._print(
                "Skipping real Mastodon API call since perform_operations is set to False",
                color="light_grey",
            )
        unfollow_message = (
            f"current_user (@{current_username}) unfollowed target_user (@{target_username})"
        )
        self._print(unfollow_message, emoji="✅")
        self._log_action_event(
            {
                "source_user": current_user_full,
                "label": "unfollow",
                "data": {"target_user": target_user_full},
            }
        )
        return unfollow_message

    # @app_action
    # def post_status(
    #     self,
    #     current_user: str,
    #     status: str,
    #     visibility: (Literal["private", "public", "unlisted", "direct"] | None) = None,
    #     sensitive: bool = False,
    #     spoiler_text: str | None = None,
    #     language: str | None = None,
    #     scheduled_at: datetime.datetime | None = None,
    #     in_reply_to_id: int | None = None,
    #     media_files: list[str] | None = None,
    #     idempotency_key: str | None = None,
    #     content_type: str | None = None,
    #     poll_options: list[str] | None = None,
    #     poll_expires_in: int | None = None,
    #     poll_multiple: bool = False,
    #     poll_hide_totals: bool = False,
    #     quote_id: int | None = None,
    # ) -> str:
    #     """Post a new status update to the Mastodon-like social network.

    #     Args:
    #         current_user (str): The username of the user posting the status.
    #         status (str): The text content of the status update.
    #         visibility (str | None): The visibility level of the post ('direct', 'private', 'unlisted', or 'public').
    #         sensitive (bool): Whether the post should be marked as sensitive content.
    #         spoiler_text (str | None): Text to be shown as a warning before the status.
    #         language (str | None): The language of the status (ISO 639-1 or 639-3 code).
    #         scheduled_at (datetime.datetime | None): When to schedule the post for future publishing.
    #         in_reply_to_id (int | None): The `toot_id` of the status this post is replying to.
    #         media_files (List[str] | None): List of paths to media files to attach to the post.
    #         idempotency_key (str | None): A unique key to prevent duplicate posts.
    #         content_type (str | None): The MIME type of the status content (for Pleroma servers).
    #         poll_options (List[str] | None): List of options for a poll attached to the post.
    #         poll_expires_in (int | None): Number of seconds until the poll expires.
    #         poll_multiple (bool): Whether multiple choices are allowed in the poll.
    #         poll_hide_totals (bool): Whether to hide poll results until it expires.
    #         quote_id (int | None): The ID of a status being quoted (Fedibird-specific feature).

    #     Raises
    #     ------
    #         ValueError: If the input parameters are invalid.
    #         Exception: For any other unexpected errors during posting.
    #     """
    #     try:
    #         username = self._get_username(current_user)
    #         if self.perform_operations:
    #             self._mastodon_ops.post_status(
    #                 login_user=username,
    #                 status=status,
    #                 visibility=visibility,
    #                 sensitive=sensitive,
    #                 spoiler_text=spoiler_text,
    #                 language=language,
    #                 scheduled_at=scheduled_at,
    #                 in_reply_to_id=in_reply_to_id,
    #                 media_files=media_files,
    #                 idempotency_key=idempotency_key,
    #                 content_type=content_type,
    #                 poll_options=poll_options,
    #                 poll_expires_in=poll_expires_in,
    #                 poll_multiple=poll_multiple,
    #                 poll_hide_totals=poll_hide_totals,
    #                 quote_id=quote_id,
    #             )
    #         else:
    #             self._print(
    #                 "Skipping real Mastodon API call since perform_operations is set to False",
    #                 color="light_grey",
    #             )

    #         # Log success
    #         if scheduled_at:
    #             self._print(
    #                 "Status scheduled successfully for user:"
    #                 f' {current_user} ({username}) at {scheduled_at}: "{status}"',
    #                 emoji="🕒",
    #             )
    #         else:
    #             self._print(
    #                 f'Status posted for user: {current_user} ({username}): "{status}"',
    #                 emoji="📝",
    #             )

    #         if poll_options:
    #             self._print("Poll attached to the status.", emoji="📊")

    #         if media_files:
    #             self._print(f"Attached {len(media_files)} media file(s).", emoji="📎")

    #     except ValueError as e:
    #         self._print(f"Invalid input: {e!s}", emoji="❌")
    #         raise

    #     except Exception as e:
    #         self._print(f"An unexpected error occurred: {e!s}", emoji="❌")
    #         raise
    #     return_msg = f'Status posted for user: {current_user} ({username}): "{status}"'
    #     return return_msg

    @app_action
    def post_toot(
        self,
        current_user: str,
        status: str,
        media_links: list[str] | None = None,
    ) -> str:
        """Post a new toot to the Mastodon-like social network.

        Args:
            current_user (str): The username of the user posting the status.
            status (str): The text content of the status update.

        Raises
        ------
            ValueError: If the input parameters are invalid.
            Exception: For any other unexpected errors during posting.
        """
        return_val = None
        current_user_full = str(current_user)
        try:
            current_user = f"{current_user.split(maxsplit=1)[0]}{current_user.split()[1]}"
            username = self._get_username(current_user)
            if self.perform_operations:
                return_val = self._mastodon_ops.post_status(
                    login_user=username,
                    status=status,
                    media_files=media_links,
                )
            else:
                self._print(
                    "Skipping real Mastodon API call since perform_operations is set to False",
                    color="light_grey",
                )

            self._print(
                f'Status posted for user: {current_user} ({username}): "{status}"',
                emoji="📝",
            )
            # self._print(return_val)

        except ValueError as e:
            self._print(f"Invalid input: {e!s}", emoji="❌")
            raise

        except Exception as e:
            self._print(f"An unexpected error occurred: {e!s}", emoji="❌")
            raise
        toot_id = None
        if return_val:
            return_msg = (
                f"{current_user} posted a toot with Toot ID: {return_val['id']} --- {status}\n"
            )
            toot_id = return_val["id"]
        else:
            return_msg = f'{current_user} posted a toot!: "{status}"\n'
        self._log_action_event(
            {
                "source_user": current_user_full,
                "label": "post",
                "data": {"toot_id": str(toot_id), "post_text": status},
            }
        )
        return return_msg

    # @app_action
    # def post_media_toot(
    #     self,
    #     current_user: str,
    #     status: str,
    #     media_link: str,
    # ) -> str:
    #     """Post a new toot to the Mastodon-like social network.

    #     Args:
    #         current_user (str): The username of the user posting the status.
    #         status (str): The text content of the status update.

    #     Raises
    #     ------
    #         ValueError: If the input parameters are invalid.
    #         Exception: For any other unexpected errors during posting.
    #     """
    #     return_val = None
    #     current_user_full = str(current_user)
    #     try:
    #         current_user = f"{current_user.split()[0]}{current_user.split()[1]}"
    #         username = self._get_username(current_user)
    #         if self.perform_operations:
    #             return_val = self._mastodon_ops.post_status(
    #                 login_user=username,
    #                 status=status,
    #             )
    #         else:
    #             self._print(
    #                 "Skipping real Mastodon API call since perform_operations is set to False",
    #                 color="light_grey",
    #             )

    #         self._print(
    #             f'Status posted for user: {current_user} ({username}): "{status}"',
    #             emoji="📝",
    #         )
    #         # self._print(return_val)

    #     except ValueError as e:
    #         self._print(f"Invalid input: {e!s}", emoji="❌")
    #         raise

    #     except Exception as e:
    #         self._print(f"An unexpected error occurred: {e!s}", emoji="❌")
    #         raise
    #     toot_id = None
    #     if return_val:
    #         return_msg = (
    #             f"{current_user} posted a toot with Toot ID: {return_val['id']} --- {status}\n"
    #         )
    #         toot_id = return_val["id"]
    #     else:
    #         return_msg = f'{current_user} posted a toot!: "{status}"\n'
    #     self.action_logger.log(
    #         {
    #             "source_user": current_user_full,
    #             "label": "post",
    #             "data": {"toot_id": toot_id, "post_text": status},
    #         }
    #     )
    #     return return_msg

    @app_action
    def reply_to_toot(
        self,
        current_user: str,
        status: str,
        in_reply_to_id: int,
    ) -> str:
        """Post a new status update to the Mastodon-like social network.

        Args:
            current_user (str): The username of the user posting the status.
            status (str): The text content of the status update.
            in_reply_to_id (int): The `toot_id` of the status this post is replying to.

        Raises
        ------
            ValueError: If the input parameters are invalid.
            Exception: For any other unexpected errors during posting.
        """
        return_val = None
        try:
            current_user_full = str(current_user)
            current_user = f"{current_user.split(maxsplit=1)[0]}{current_user.split()[1]}"
            username = self._get_username(current_user)
            if self.perform_operations:
                return_val = self._mastodon_ops.post_status(
                    login_user=username,
                    status=status,
                    in_reply_to_id=in_reply_to_id,
                )
                if return_val:
                    toot_id = return_val["id"]
                else:
                    toot_id = ""
                    self._print("Failed to post reply.", color="red")
            else:
                self._print(
                    "Skipping real Mastodon API call since perform_operations is set to False",
                    color="light_grey",
                )
                toot_id = ""

            self._print(
                f"You replied to the toot with toot id {in_reply_to_id} : {status}",
                emoji="📝",
            )
            return_msg = (
                f"{current_user} replied to a toot with toot id {in_reply_to_id} : {status}"
            )
            self._log_action_event(
                {
                    "source_user": current_user_full,
                    "label": "reply",
                    "data": {
                        "reply_to": {"toot_id": in_reply_to_id},
                        "toot_id": toot_id,
                        "post_text": status,
                    },
                }
            )
        except ValueError as e:
            self._print(f"Invalid input, regular toot posted: {e!s}", emoji="❌")
            return_msg = f'''There was an error in posting {current_user}'s reply, response was posted as a new toot!: "{status}"'''
            if (
                self.perform_operations
                and self._mastodon_ops is not None
                and "username" in locals()
            ):
                self._mastodon_ops.post_status(
                    login_user=username,
                    status=status,
                )

        except Exception as e:
            self._print(f"An unexpected error occurred, regular toot posted: {e!s}", emoji="❌")
            return_msg = f'''There was an error in posting {current_user}'s reply, response was posted as a new toot!: "{status}"'''
            if (
                self.perform_operations
                and self._mastodon_ops is not None
                and "username" in locals()
            ):
                self._mastodon_ops.post_status(
                    login_user=username,
                    status=status,
                )
        return return_msg

    # @app_action
    # def get_public_timeline(self, limit: int) -> str:
    #     """Read the public Mastodon social network feed."""
    #     self._print(f"Fetching public timeline (limit: {limit})", emoji="🌐")
    #     if self.perform_operations:
    #         timeline = self._mastodon_ops.get_public_timeline(limit=limit)
    #     else:
    #         self._print(
    #             "Skipping real Mastodon API call since perform_operations is set to False",
    #             color="light_grey",
    #         )
    #         timeline = []
    #     self._print(f"Retrieved {len(timeline)} posts from the public timeline", emoji="📊")
    #     str_timeline = self.print_and_return_timeline(timeline)
    #     return f"{self._get_username} viewed the Public Mastodon timeline:\n" + str_timeline

    def print_timeline(self, timeline: list[dict[str, Any]]) -> None:
        """Print the timeline in a readable format."""

        def _clean_html(html_string):
            clean_text = re.sub("<[^<]+?>", "", unescape(html_string))
            return re.sub(r"\s+", " ", clean_text).strip()

        for post in timeline:
            self._print("----------------------------------------")
            self._print(f"User: {post['account']['display_name']} (@{post['account']['username']})")
            self._print(f"Content: {_clean_html(post['content'])}")
            self._print(f"Toot ID: {post['id']}")
            self._print(f"Favourites: {post['favourites_count']}, Reblogs: {post['reblogs_count']}")
            # self._print(f"URL: {post['url']}")
        self._print("----------------------------------------")

    def print_and_return_timeline(self, timeline: list[dict[str, Any]]) -> str:
        """Print the timeline in a readable format and return it as a string."""

        def _clean_html(html_string):
            clean_text = re.sub("<[^<]+?>", "", unescape(html_string))
            return re.sub(r"\s+", " ", clean_text).strip()

        output = []
        for post in timeline:
            output.extend(
                [
                    "----------------------------------------",
                    f"User: {post['account']['display_name']} (@{post['account']['username']})",
                    f"Content: {_clean_html(post['content'])}",
                    f"Toot ID: {post['id']}",
                    f"Favourites: {post['favourites_count']}, Reblogs: {post['reblogs_count']}",
                    # f"URL: {post['url']}",
                    "",  # Add an empty string to create a blank line between posts
                ]
            )
        output.append("----------------------------------------")

        str_timeline = "\n".join(output)
        self._print(str_timeline)
        return str_timeline

    @app_action
    def get_own_timeline(self, current_user: str, limit: int, return_str: bool = False) -> str:
        """Read the Mastodon social network feed for the current user."""
        current_user_full = str(current_user)
        current_user = f"{current_user.split(maxsplit=1)[0]}{current_user.split()[1]}"
        username = self._get_username(current_user)
        self._print(
            f"Fetching @{username}'s timeline (limit: {limit})",
            emoji="🏠",
        )

        if self.perform_operations:
            try:
                timeline = self._mastodon_ops.get_own_timeline(username, limit=limit)
            except Exception as e:
                self._print(f"Error fetching timeline for @{username}: {e}", color="red")
                timeline = []
        else:
            timeline = []
            self._print(
                "Skipping real Mastodon API call since perform_operations is set to False",
                color="light_grey",
            )
        self._print(
            f"Retrieved {len(timeline)} posts from @{username}'s timeline",
            emoji="📊",
        )

        self._log_action_event(
            {
                "source_user": current_user_full,
                "label": "get_own_timeline",
                "data": {"num_posts_retreived": len(timeline)},  # TODO: add timeline here
            }
        )

        if return_str:
            str_timeline = self.print_and_return_timeline(timeline)
            return "Own Mastodon Timeline:\n" + str_timeline
        return timeline

    # @app_action
    # def get_user_timeline(self, current_user: str, target_user: str, limit: int) -> str:
    #     """Read a specific user's timeline on Mastodon social network."""
    #     current_username = self._get_username(current_user.split()[0])
    #     target_username = self._get_username(target_user.split()[0])
    #     self._print(
    #         f"@{current_username} fetching @{target_username}'s timeline (limit: {limit})",
    #         emoji="👥",
    #     )
    #     if self.perform_operations:
    #         timeline = self._mastodon_ops.get_user_timeline(
    #             current_username, target_username, limit=limit
    #         )
    #     else:
    #         timeline = []
    #         self._print(
    #             "Skipping real Mastodon API call since perform_operations is set to False",
    #             color="light_grey",
    #         )
    #     self._print(
    #         f"Retrieved {len(timeline)} posts from @{target_username}'s timeline",
    #         emoji="📊",
    #     )
    #     str_timeline = self.print_and_return_timeline(timeline)
    #     return f"@{current_username}'s Mastodon Timeline:\n" + str_timeline

    def print_notifications(self, notifications: list[dict[str, Any]]) -> str:
        """Generate a string of important details of notifications, one per line."""
        if not notifications:
            return "No notifications to display."

        notification_lines = []
        for notification in notifications:
            notif_type = notification["type"]
            created_at = notification["created_at"].strftime("%Y-%m-%d %H:%M:%S")
            account = notification["account"]
            display_name = account["display_name"]
            username = account["username"]

            notification_info = (
                f"[{created_at}] {notif_type.capitalize()} from {display_name} (@{username})"
            )

            if notif_type == "mention":
                status = notification.get("status", {})
                content = status.get("content", "No content available")
                # Truncate content if it's too long
                content = content[:50] + "..." if len(content) > 50 else content  # noqa: PLR2004
                notification_info += f" - Content: {content}"

            notification_lines.append(notification_info)

        return "\n".join(notification_lines)

    @app_action
    def read_notifications(self, current_user: str, clear: bool, limit: int) -> str:
        """Read Mastodon social network notifications."""
        current_user_full = str(current_user)
        current_user = f"{current_user.split(maxsplit=1)[0]}{current_user.split()[1]}"

        username = self._get_username(current_user)
        self._print(
            f"Reading notifications for @{username} (clear: {clear}, limit: {limit})",
            emoji="🔔",
        )
        if self.perform_operations:
            notifications = self._mastodon_ops.read_notifications(
                username, clear=clear, limit=limit
            )
        else:
            notifications = []
            self._print(
                "Skipping real Mastodon API call since perform_operations is set to False",
                color="light_grey",
            )

        retrieval_message = f"Retrieved {len(notifications)} notifications for {current_user}:"
        self._print(retrieval_message, emoji="📬")

        notifications_string = self.print_notifications(notifications)
        full_output = f"{retrieval_message}\n{notifications_string}"
        self._print(full_output)
        self._log_action_event(
            {
                "source_user": current_user_full,
                "label": "read_notification",
                "data": {
                    "num_notifications_retreived": len(notifications)
                },  # TODO: add notifications timeline here
            }
        )

        return full_output

    @app_action
    def like_toot(self, current_user: str, toot_id: str) -> str:
        """Like (favorite) a toot."""
        current_user_full = str(current_user)
        current_user = f"{current_user.split(maxsplit=1)[0]}{current_user.split()[1]}"
        current_username = self._get_username(current_user)
        # self._print(
        #     f"@{current_username} liking post {toot_id}",
        #     emoji="❤️",
        # )
        try:
            like_message = f"{current_user} (@{current_username}) liked post {toot_id}"
            if self.perform_operations:
                check = self._mastodon_ops.like_check(current_username, toot_id)
                if not check:
                    self._mastodon_ops.like_toot(current_username, toot_id)
                else:
                    like_message = f"{current_user} (@{current_username}) has previously liked post {toot_id}. Please conduct a different action!!"
            else:
                self._print(
                    "Skipping real Mastodon API call since perform_operations is set to False",
                    color="light_grey",
                )
            self._print(like_message, emoji="✅")
            self._log_action_event(
                {
                    "source_user": current_user_full,
                    "label": "like_toot",
                    "data": {"toot_id": str(toot_id)},
                }
            )

        except ValueError as e:
            self._print(f"Invalid input: {e!s}", emoji="❌")
            like_message = '''There was an error in liking due to invalid toot id"'''

        except Exception as e:
            self._print(f"An unexpected error occurred{e!s}", emoji="❌")
            like_message = '''There was an error in liking due to invalid toot id"'''
        return like_message

    # region[additional methods]

    @app_action
    def boost_toot(self, current_user: str, toot_id: str) -> str:
        """Boost (reblog) a toot."""
        current_user_full = str(current_user)
        current_user = f"{current_user.split(maxsplit=1)[0]}{current_user.split()[1]}"
        current_username = self._get_username(current_user)
        self._print(
            f"@{current_username} boosting post {toot_id}",
            emoji="🔁",
        )
        try:
            boost_message = f"{current_user} (@{current_username}) boosted post {toot_id}"
            if self.perform_operations:
                check = self._mastodon_ops.boost_check(current_username, toot_id)
                if not check:
                    self._mastodon_ops.boost_toot(current_username, toot_id)
                else:
                    boost_message = f"{current_user} (@{current_username}) has previously boosted post {toot_id}. Please conduct a different action!!"
            self._print(
                f"@{current_username} boosted post {toot_id}",
                emoji="✅",
            )
            self._log_action_event(
                {
                    "source_user": current_user_full,
                    "label": "boost_toot",
                    "data": {"toot_id": str(toot_id)},
                }
            )
        except ValueError as e:
            self._print(f"Invalid input: {e!s}", emoji="❌")
            boost_message = '''There was an error in boosting due to invalid toot id"'''

        except Exception as e:
            self._print(f"An unexpected error occurred{e!s}", emoji="❌")
            boost_message = '''There was an error in boosting due to invalid toot id"'''
        return boost_message

    # @app_action
    # def block_user(self, current_user: str, target_user: str) -> None:
    #   """Block a user."""
    #   current_username = self._get_username(current_user)
    #   target_username = self._get_username(target_user)
    #   self._print(
    #       f"@{current_username} blocking user: @{target_username}", emoji="🚫"
    #   )
    #   if self.perform_operations:
    #     self._mastodon_ops.block_user(current_username, target_username)
    #   self._print(
    #       f"@{current_username} blocked user @{target_username}", emoji="✅"
    #   )

    # @app_action
    # def unblock_user(self, current_user: str, target_user: str) -> None:
    #   """Unblock a user."""
    #   current_username = self._get_username(current_user)
    #   target_username = self._get_username(target_user)
    #   self._print(
    #       f"@{current_username} unblocking user: @{target_username}", emoji="✅"
    #   )
    #   if self.perform_operations:
    #     self._mastodon_ops.unblock_user(current_username, target_username)
    #   self._print(
    #       f"@{current_username} unblocked user @{target_username}", emoji="✅"
    #   )

    # @app_action
    # def mute_account(
    #     self,
    #     current_user: str,
    #     target_user: str,
    #     notifications: bool,
    #     duration: int,
    # ) -> None:
    #   """Mute an account."""
    #   current_username = self._get_username(current_user)
    #   target_username = self._get_username(target_user)
    #   self._print(
    #       f"@{current_username} muting @{target_username} (notifications:"
    #       f" {notifications}, duration: {duration})",
    #       emoji="🔇",
    #   )
    #   if self.perform_operations:
    #     self._mastodon_ops.mute_account(
    #         current_username,
    #         target_username,
    #         notifications=notifications,
    #         duration=duration,
    #     )
    #   self._print(f"@{current_username} muted @{target_username}", emoji="✅")

    # @app_action
    # def unmute_account(self, current_user: str, target_user: str) -> None:
    #   """Unmute an account."""
    #   current_username = self._get_username(current_user)
    #   target_username = self._get_username(target_user)
    #   self._print(f"@{current_username} unmuting @{target_username}", emoji="🔊")
    #   if self.perform_operations:
    #     self._mastodon_ops.unmute_account(current_username, target_username)
    #   self._print(f"@{current_username} unmuted @{target_username}", emoji="✅")

    # @app_action
    # def delete_posts(
    #     self,
    #     current_user: str,
    #     post_ids: list[str],
    #     recent_count: int,
    #     delete_all: bool,
    # ) -> None:
    #   """Delete posts for a user."""
    #   username = self._get_username(current_user)
    #   if delete_all:
    #     self._print(f"Deleting all posts for @{username}", emoji="🗑️")
    #   elif recent_count:
    #     self._print(
    #         f"Deleting {recent_count} recent posts for @{username}", emoji="🗑️"
    #     )
    #   elif post_ids:
    #     self._print(f"Deleting specific posts for @{username}", emoji="🗑️")
    #   else:
    #     self._print("No posts specified for deletion", emoji="❌")
    #     return

    #   if self.perform_operations:
    #     self._mastodon_ops.delete_posts(
    #         username,
    #         post_ids=post_ids,
    #         recent_count=recent_count,
    #         delete_all=delete_all,
    #     )
    #   self._print("Deletion process completed", emoji="✅")

    # @app_action
    # def send_direct_message(
    #     self, current_user: str, target_user: str, message: str
    # ) -> None:
    #   """Send a direct message to another user."""
    #   current_username = self._get_username(current_user)
    #   target_username = self._get_username(target_user)
    #   self._print(
    #       f"@{current_username} sending DM to @{target_username}", emoji="✉️"
    #   )
    #   if self.perform_operations:
    #     self._mastodon_ops.post_status(
    #         current_username, f"@{target_username} {message}", visibility="direct"
    #     )
    #   self._print(
    #       f"DM sent from @{current_username} to @{target_username}", emoji="✅"
    #   )

    # endregion


# endregion
