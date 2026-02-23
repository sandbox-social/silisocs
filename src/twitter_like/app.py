"""Twitter-like social media app for simulation.

Wraps ``TwitterLikePlatform`` (SQLite-backed engine) with the
``SocialMediaApp`` interface so it can be used as a drag-and-drop
replacement for the Mastodon app in the simulation.
"""

from __future__ import annotations

import dataclasses
import os
from typing import Any

from sim.core.phone_app import app_action
from sim.core.social_media_app import SocialMediaApp
from twitter_like.engine import TwitterLikePlatform


@dataclasses.dataclass
class TwitterLikeApp(SocialMediaApp):
    """Twitter-like social media app.

    A microblogging platform where users post tweets, reply, like, repost,
    and follow other users.  Uses a local SQLite database as the backend.
    """

    action_logger: Any = None
    app_description: str = "TwitterLikeApp"
    db_path: str = "twitter_like.db"
    _platform: TwitterLikePlatform = dataclasses.field(default=None, init=False, repr=False)  # type: ignore[assignment]
    _user_mapping: dict[str, str] = dataclasses.field(default_factory=dict, init=False)

    def __post_init__(self) -> None:
        super().__init__()
        # Ensure the directory for the DB file exists (for output folder paths)
        db_dir = os.path.dirname(self.db_path)
        if db_dir:
            os.makedirs(db_dir, exist_ok=True)
        self._platform = TwitterLikePlatform(self.db_path, use_queue=True)

    # ------------------------------------------------------------------ #
    # SocialMediaApp required interface
    # ------------------------------------------------------------------ #

    def name(self) -> str:
        """Return the name of the app."""
        return "TwitterLikeApp"

    def description(self) -> str:
        """Return a description of the app."""
        return self.app_description

    def initialize(self, agent_names: list[str], **kwargs: Any) -> None:
        """Set up users, follow network, bios, and seed posts.

        Args:
            agent_names: List of agent display names.
            **kwargs: Supported keys:
                - ``following_network`` (dict[str, list[str]]): Who follows whom.
                - ``agent_bios`` (dict[str, str]): Display name → bio.
                - ``seed_posts`` (dict[str, str]): Display name → first post.
                - ``sim_roles`` (dict[str, str]): Display name → role name.
        """
        following_network = kwargs.get("following_network", {})
        agent_bios = kwargs.get("agent_bios", {})
        seed_posts = kwargs.get("seed_posts", {})

        # Create users
        for display_name in agent_names:
            username = self._display_name_to_username(display_name)
            self._user_mapping[display_name] = username
            bio = agent_bios.get(display_name, "")
            self._platform.create_user(username, bio=bio)

        # Establish follow network
        for display_name, followees in following_network.items():
            src_username = self._get_username(display_name)
            for followee in followees:
                tgt_username = self._get_username(followee)
                try:
                    self._platform.follow(src_username, tgt_username)
                except Exception as e:
                    self._print(f"Follow error ({src_username}->{tgt_username}): {e}", color="red")

        # Seed posts
        for display_name, post_text in seed_posts.items():
            if post_text:
                username = self._get_username(display_name)
                try:
                    self._platform.create_post(username, post_text)
                except Exception as e:
                    self._print(f"Seed post error for {username}: {e}", color="red")

        self._print(
            f"Initialized {len(agent_names)} users on TwitterLikeApp",
            emoji="✅",
        )

    def get_timeline(self, user_name: str, limit: int = 10) -> list[dict]:
        """Fetch the chronological home timeline for a user.

        Args:
            user_name: Display name of the user.
            limit: Maximum number of posts.

        Returns
        -------
            List of post dicts from the platform engine.
        """
        username = self._get_username(user_name)
        try:
            feed = self._platform.get_feed("chronological_home", username, limit=limit)
            return feed.get("posts", [])
        except Exception as e:
            self._print(f"Error fetching timeline for {username}: {e}", color="red")
            return []

    def format_timeline_for_observation(self, timeline: list[dict]) -> str:
        """Format timeline posts as a clean text block for the LLM.

        Args:
            timeline: List of post dicts.

        Returns
        -------
            Formatted string with one block per post.
        """
        result = ""
        for post in timeline:
            formatted_date = post.get("formatted_date", "")
            result += (
                f"\n\nUser: {post['username']}\n"
                f"Content: {post['content']}\n"
                f"Tweet ID: {post['id']}\n"
                f"Likes: {post['likes_count']}, "
                f"Reposts: {post['reposts_count']}, "
                f"Replies: {post.get('reply_count', 0)}\n"
            )
        return result

    def parse_and_resolve_action(self, user_name: str, action_data: dict) -> str:
        """Dispatch a parsed action to the correct app_action method.

        Args:
            user_name: Display name of the acting agent.
            action_data: Dict with ``action_type``, ``target_id``, ``content``, ``reasoning``.

        Returns
        -------
            Result string describing the action outcome.
        """
        action_type = action_data.get("action_type", "").lower().strip()
        target_id = action_data.get("target_id", "0")
        content = action_data.get("content", "")

        try:
            if action_type == "post":
                return self.create_tweet(user_name, content)
            if action_type == "reply":
                return self.reply_to_tweet(user_name, content, int(target_id))
            if action_type == "like":
                return self.like_tweet(user_name, int(target_id))
            if action_type in ("repost", "retweet", "boost"):
                return self.repost_tweet(user_name, int(target_id))
            return f"Unknown action type: {action_type}"
        except Exception as e:
            self._print(f"Error resolving action {action_type}: {e}", color="red")
            return f"Error performing {action_type}: {e}"

    # ------------------------------------------------------------------ #
    # Helper methods
    # ------------------------------------------------------------------ #

    def _display_name_to_username(self, display_name: str) -> str:
        """Convert a display name to a platform username.

        Concatenates first and last name parts into a single lowercase string.
        """
        parts = display_name.strip().split()
        if len(parts) >= 2:
            return f"{parts[0]}{parts[1]}".lower()
        return parts[0].lower() if parts else display_name.lower()

    def _get_username(self, display_name: str) -> str:
        """Look up the platform username for a display name."""
        # Try direct mapping first
        if display_name in self._user_mapping:
            return self._user_mapping[display_name]
        # Try generating from name
        username = self._display_name_to_username(display_name)
        if username in {v for v in self._user_mapping.values()}:
            return username
        raise ValueError(f"No username found for display name: {display_name}")

    def shutdown(self) -> None:
        """Clean shutdown of the platform engine."""
        self._platform.shutdown()

    # ------------------------------------------------------------------ #
    # @app_action methods
    # ------------------------------------------------------------------ #

    @app_action
    def create_tweet(self, current_user: str, status: str) -> str:
        """Post a new tweet to the timeline.

        Args:
            current_user: The full display name of the user posting (e.g. "Alice Smith").
            status: The text content of the tweet (max 280 characters).
        """
        current_user_full = str(current_user)
        username = self._get_username(current_user)
        post_id = self._platform.create_post(username, status)
        result_msg = f'{current_user_full} posted a tweet (ID: {post_id}): "{status}"'
        self._print(result_msg, emoji="📝")
        if self.action_logger:
            self.action_logger.log(
                {
                    "source_user": current_user_full,
                    "label": "post",
                    "data": {"post_id": str(post_id), "post_text": status},
                }
            )
        return result_msg

    @app_action
    def reply_to_tweet(self, current_user: str, status: str, post_id: int) -> str:
        """Reply to an existing tweet.

        Args:
            current_user: The full display name of the user replying.
            status: The text content of the reply.
            post_id: The ID of the tweet being replied to.
        """
        current_user_full = str(current_user)
        username = self._get_username(current_user)
        reply_id = self._platform.create_post(username, status, reply_to_id=post_id)
        result_msg = f'{current_user_full} replied to tweet {post_id}: "{status}"'
        self._print(result_msg, emoji="💬")
        if self.action_logger:
            self.action_logger.log(
                {
                    "source_user": current_user_full,
                    "label": "reply",
                    "data": {
                        "post_id": str(reply_id),
                        "reply_to_id": str(post_id),
                        "post_text": status,
                    },
                }
            )
        return result_msg

    @app_action
    def like_tweet(self, current_user: str, post_id: int) -> str:
        """Like (favorite) a tweet.

        Args:
            current_user: The full display name of the user liking the tweet.
            post_id: The ID of the tweet to like.
        """
        current_user_full = str(current_user)
        username = self._get_username(current_user)
        try:
            result = self._platform.like(username, post_id)
            if result is False:
                like_msg = f"{current_user_full} has already liked tweet {post_id}."
            else:
                like_msg = f"{current_user_full} liked tweet {post_id}."
        except Exception as e:
            like_msg = f"Error liking tweet {post_id}: {e}"
        self._print(like_msg, emoji="❤️")
        if self.action_logger:
            self.action_logger.log(
                {
                    "source_user": current_user_full,
                    "label": "like",
                    "data": {"post_id": str(post_id)},
                }
            )
        return like_msg

    @app_action
    def repost_tweet(self, current_user: str, post_id: int) -> str:
        """Repost (retweet) an existing tweet to share it with your followers.

        Args:
            current_user: The full display name of the user reposting.
            post_id: The ID of the tweet to repost.
        """
        current_user_full = str(current_user)
        username = self._get_username(current_user)
        try:
            repost_id = self._platform.repost(username, post_id)
            repost_msg = f"{current_user_full} reposted tweet {post_id} (new ID: {repost_id})."
        except Exception as e:
            repost_msg = f"Error reposting tweet {post_id}: {e}"
        self._print(repost_msg, emoji="🔁")
        if self.action_logger:
            self.action_logger.log(
                {
                    "source_user": current_user_full,
                    "label": "repost",
                    "data": {"post_id": str(post_id)},
                }
            )
        return repost_msg

    @app_action
    def follow_user(self, current_user: str, target_user: str) -> str:
        """Follow another user to see their tweets in your timeline.

        Args:
            current_user: The full display name of the user who wants to follow.
            target_user: The full display name of the user to follow.
        """
        current_user_full = str(current_user)
        target_user_full = str(target_user)
        src_username = self._get_username(current_user)
        tgt_username = self._get_username(target_user)
        try:
            self._platform.follow(src_username, tgt_username)
            follow_msg = f"{current_user_full} followed {target_user_full}."
        except Exception as e:
            follow_msg = f"Error following {target_user_full}: {e}"
        self._print(follow_msg, emoji="➕")
        if self.action_logger:
            self.action_logger.log(
                {
                    "source_user": current_user_full,
                    "label": "follow",
                    "data": {"target_user": target_user_full},
                }
            )
        return follow_msg

    @app_action
    def unfollow_user(self, current_user: str, target_user: str) -> str:
        """Unfollow a user to stop seeing their tweets.

        Args:
            current_user: The full display name of the user who wants to unfollow.
            target_user: The full display name of the user to unfollow.
        """
        current_user_full = str(current_user)
        target_user_full = str(target_user)
        src_username = self._get_username(current_user)
        tgt_username = self._get_username(target_user)
        try:
            self._platform.unfollow(src_username, tgt_username)
            msg = f"{current_user_full} unfollowed {target_user_full}."
        except Exception as e:
            msg = f"Error unfollowing {target_user_full}: {e}"
        self._print(msg, emoji="➖")
        if self.action_logger:
            self.action_logger.log(
                {
                    "source_user": current_user_full,
                    "label": "unfollow",
                    "data": {"target_user": target_user_full},
                }
            )
        return msg

    @app_action
    def get_own_timeline(self, current_user: str, limit: int) -> str:
        """Read your home timeline showing tweets from users you follow.

        Args:
            current_user: The full display name of the user reading the timeline.
            limit: Maximum number of tweets to retrieve.
        """
        current_user_full = str(current_user)
        timeline = self.get_timeline(current_user, limit)
        str_timeline = self.format_timeline_for_observation(timeline)
        self._print(f"Retrieved {len(timeline)} tweets for {current_user_full}", emoji="📊")
        if self.action_logger:
            self.action_logger.log(
                {
                    "source_user": current_user_full,
                    "label": "get_own_timeline",
                    "data": {"num_posts_retrieved": len(timeline)},
                }
            )
        return f"Twitter Timeline for {current_user_full}:\n{str_timeline}"

    @app_action
    def update_profile(self, current_user: str, bio: str) -> str:
        """Update your profile bio.

        Args:
            current_user: The full display name of the user updating their profile.
            bio: The new bio text for the profile.
        """
        current_user_full = str(current_user)
        username = self._get_username(current_user)
        # TwitterLikePlatform doesn't have update_profile, use raw SQL
        try:
            with self._platform.get_connection() as conn:
                conn.execute("UPDATE users SET bio = ? WHERE username = ?", (bio, username))
                conn.commit()
            msg = f'Profile updated for {current_user_full}: "{bio}"'
        except Exception as e:
            msg = f"Error updating profile: {e}"
        self._print(msg, emoji="✏️")
        if self.action_logger:
            self.action_logger.log(
                {
                    "source_user": current_user_full,
                    "label": "update_profile",
                    "data": {"new_bio": bio},
                }
            )
        return msg

    @app_action
    def view_profile(self, current_user: str, target_user: str) -> str:
        """View a user's profile including their bio and stats.

        Args:
            current_user: The full display name of the user viewing the profile.
            target_user: The full display name of the user whose profile to view.
        """
        target_user_full = str(target_user)
        tgt_username = self._get_username(target_user)
        try:
            profile = self._platform.view_profile(tgt_username)
            if profile:
                msg = (
                    f"Profile of {target_user_full} (@{tgt_username}):\n"
                    f"  Bio: {profile.get('bio', '')}\n"
                    f"  Posts: {profile.get('post_count', 0)}\n"
                    f"  Followers: {profile.get('followers_count', 0)}\n"
                    f"  Following: {profile.get('following_count', 0)}\n"
                )
            else:
                msg = f"Profile not found for {target_user_full}."
        except Exception as e:
            msg = f"Error viewing profile for {target_user_full}: {e}"
        self._print(msg, emoji="👤")
        return msg

    @app_action
    def unlike_tweet(self, current_user: str, post_id: int) -> str:
        """Remove your like from a tweet.

        Args:
            current_user: The full display name of the user unliking.
            post_id: The ID of the tweet to unlike.
        """
        current_user_full = str(current_user)
        username = self._get_username(current_user)
        try:
            self._platform.unlike(username, post_id)
            msg = f"{current_user_full} unliked tweet {post_id}."
        except Exception as e:
            msg = f"Error unliking tweet {post_id}: {e}"
        self._print(msg, emoji="💔")
        if self.action_logger:
            self.action_logger.log(
                {
                    "source_user": current_user_full,
                    "label": "unlike",
                    "data": {"post_id": str(post_id)},
                }
            )
        return msg

    @app_action
    def quote_repost_tweet(self, current_user: str, post_id: int, status: str) -> str:
        """Quote-repost a tweet, adding your own commentary.

        Args:
            current_user: The full display name of the user quote-reposting.
            post_id: The ID of the tweet to quote.
            status: Your commentary text to add above the quoted tweet.
        """
        current_user_full = str(current_user)
        username = self._get_username(current_user)
        try:
            new_id = self._platform.quote_repost(username, post_id, status)
            msg = (
                f'{current_user_full} quote-reposted tweet {post_id} (new ID: {new_id}): "{status}"'
            )
        except Exception as e:
            msg = f"Error quote-reposting tweet {post_id}: {e}"
        self._print(msg, emoji="🔁💬")
        if self.action_logger:
            self.action_logger.log(
                {
                    "source_user": current_user_full,
                    "label": "quote_repost",
                    "data": {"post_id": str(post_id), "content": status},
                }
            )
        return msg

    @app_action
    def search_posts(self, current_user: str, query: str, limit: int = 20) -> str:
        """Search for tweets containing specific text.

        Args:
            current_user: The full display name of the user searching.
            query: The search text to look for in tweet content.
            limit: Maximum number of results to return.
        """
        current_user_full = str(current_user)
        try:
            results = self._platform.search_posts(query, limit=limit)
            if results:
                msg = f"Search results for '{query}':\n"
                for post in results:
                    msg += (
                        f"  Tweet ID: {post['id']} | @{post['username']}: "
                        f"{post['content'][:80]}...\n"
                    )
            else:
                msg = f"No results found for '{query}'."
        except Exception as e:
            msg = f"Error searching posts: {e}"
        self._print(msg, emoji="🔍")
        return msg
