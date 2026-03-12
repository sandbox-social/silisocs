"""Reddit-like social media app for simulation.

Wraps ``RedditLikePlatform`` (SQLite-backed engine) with the
``SocialMediaApp`` interface so it can be used as a drag-and-drop
replacement for the Mastodon app in the simulation.
"""

from __future__ import annotations

import dataclasses
import os
from typing import Any

from mastodon_sim.environments.backends.base import SocialMediaApp, app_action
from mastodon_sim.environments.backends.reddit_like.engine import RedditLikePlatform


@dataclasses.dataclass
class RedditLikeApp(SocialMediaApp):
    """Reddit-like social media app.

    A forum-style platform where users create posts in subreddits, comment
    on posts (with threaded replies), and upvote/downvote content.
    Uses a local SQLite database as the backend.
    """

    action_logger: Any = None
    app_description: str = "RedditLikeApp"
    db_path: str = "reddit_like.db"
    _platform: RedditLikePlatform = dataclasses.field(default=None, init=False, repr=False)  # type: ignore[assignment]
    _user_mapping: dict[str, str] = dataclasses.field(default_factory=dict, init=False)

    def __post_init__(self) -> None:
        super().__init__()
        # Ensure the directory for the DB file exists (for output folder paths)
        db_dir = os.path.dirname(self.db_path)
        if db_dir:
            os.makedirs(db_dir, exist_ok=True)
        self._platform = RedditLikePlatform(self.db_path, use_queue=True)

    # ------------------------------------------------------------------ #
    # SocialMediaApp required interface
    # ------------------------------------------------------------------ #

    def name(self) -> str:
        """Return the name of the app."""
        return "RedditLikeApp"

    def description(self) -> str:
        """Return a description of the app."""
        return self.app_description

    def _log_action_event(self, source_user: str, label: str, data: dict[str, Any]) -> None:
        if self.action_logger:
            self.action_logger.log(
                {
                    "source_user": source_user,
                    "label": label,
                    "data": data,
                }
            )

    def initialize(self, agent_names: list[str], **kwargs: Any) -> None:
        """Set up users, subreddits, role-based memberships, and seed posts.

        Reddit uses **subreddit membership** instead of a follow graph.
        Configure subreddits and which roles subscribe to them via
        ``social_network.subreddits`` in the scenario YAML::

            social_network:
              subreddits:
                - name: general
                  description: General discussion
                  roles: all
                - name: politics
                  description: Political discussion
                  roles: [voter, candidate]
              default_subreddit: general

        Args:
            agent_names: Agent display names.
            **kwargs: ``sim_roles``, ``seed_posts``, ``social_network``.
        """
        sim_roles = kwargs.get("sim_roles", {})
        seed_posts = kwargs.get("seed_posts", {})
        social_network = kwargs.get("social_network", {})

        subreddit_configs = social_network.get(
            "subreddits",
            [{"name": "general", "description": "General discussion", "roles": "all"}],
        )
        default_subreddit = social_network.get("default_subreddit", "general")

        # Create subreddits.
        for sub_cfg in subreddit_configs:
            sub_name = sub_cfg.get("name", "general") if isinstance(sub_cfg, dict) else str(sub_cfg)
            sub_desc = sub_cfg.get("description", "") if isinstance(sub_cfg, dict) else ""
            self._platform.create_subreddit(sub_name, sub_desc)
            self._log_action_event(
                "system",
                "init_create_subreddit",
                {
                    "subreddit": sub_name,
                    "description": sub_desc,
                },
            )

        # Create users.
        for display_name in agent_names:
            username = self._display_name_to_username(display_name)
            self._user_mapping[display_name] = username
            self._platform.create_user(username, bio="")
            self._log_action_event(display_name, "init_create_user", {"username": username})

        # Subscribe agents to subreddits based on role.
        sub_count = 0
        for sub_cfg in subreddit_configs:
            if not isinstance(sub_cfg, dict):
                sub_cfg = {"name": str(sub_cfg), "roles": "all"}
            sub_name = sub_cfg.get("name", "general")
            allowed_roles = sub_cfg.get("roles", "all")

            for display_name in agent_names:
                agent_role = sim_roles.get(display_name, "")
                if allowed_roles == "all" or agent_role in allowed_roles:
                    username = self._get_username(display_name)
                    try:
                        self._platform.join_subreddit(username, sub_name)
                        sub_count += 1
                    except Exception as e:
                        self._print(f"Join error ({username}->{sub_name}): {e}", color="red")

        # Seed posts in default subreddit.
        for display_name, post_text in seed_posts.items():
            if post_text:
                username = self._get_username(display_name)
                try:
                    self._platform.create_post(
                        username,
                        default_subreddit,
                        title=post_text[:100],
                        content=post_text,
                    )
                except Exception as e:
                    self._print(f"Seed post error for {username}: {e}", color="red")

        self._log_action_event(
            "system",
            "initialize",
            {
                "platform": "reddit_like",
                "num_users": len(agent_names),
                "num_subreddits": len(subreddit_configs),
                "num_subscriptions": sub_count,
                "num_seed_posts": sum(1 for t in seed_posts.values() if t),
            },
        )
        self._print(
            f"Initialized {len(agent_names)} users, "
            f"{len(subreddit_configs)} subreddits, {sub_count} subscriptions",
        )

    def get_timeline(self, user_name: str, limit: int = 10) -> list[dict]:
        """Fetch the home feed for a user (posts from joined subreddits).

        Args:
            user_name: Display name of the user.
            limit: Maximum number of posts.

        Returns
        -------
            List of post dicts from the platform engine.
        """
        username = self._get_username(user_name)
        try:
            feed = self._platform.get_feed("home", username=username, limit=limit)
            return feed.get("posts", [])
        except Exception as e:
            self._print(f"Error fetching feed for {username}: {e}", color="red")
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
            result += (
                f"\n\nSubreddit: r/{post.get('subreddit_name', '?')}\n"
                f"User: {post['username']}\n"
                f"Title: {post.get('title', '')}\n"
                f"Content: {post.get('content', '')}\n"
                f"Post ID: {post['id']}\n"
                f"Score: {post.get('upvotes', 0) - post.get('downvotes', 0)} "
                f"(↑{post.get('upvotes', 0)} ↓{post.get('downvotes', 0)}), "
                f"Comments: {post.get('comment_count', 0)}\n"
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
                # Default to "general" subreddit; content is used as both title and body
                return self.create_reddit_post(user_name, "general", content[:100], content)
            if action_type == "comment":
                return self.create_comment(user_name, int(target_id), content)
            if action_type == "upvote":
                return self.upvote(user_name, int(target_id), "post")
            if action_type == "downvote":
                return self.downvote(user_name, int(target_id), "post")
            if action_type == "reply":
                # Treat reply as a comment on a post
                return self.create_comment(user_name, int(target_id), content)
            return f"Unknown action type: {action_type}"
        except Exception as e:
            self._print(f"Error resolving action {action_type}: {e}", color="red")
            return f"Error performing {action_type}: {e}"

    # ------------------------------------------------------------------ #
    # Helper methods
    # ------------------------------------------------------------------ #

    def _display_name_to_username(self, display_name: str) -> str:
        """Convert a display name to a platform username."""
        parts = display_name.strip().split()
        if len(parts) >= 2:
            return f"{parts[0]}{parts[1]}".lower()
        return parts[0].lower() if parts else display_name.lower()

    def _get_username(self, display_name: str) -> str:
        """Look up the platform username for a display name."""
        if display_name in self._user_mapping:
            return self._user_mapping[display_name]
        username = self._display_name_to_username(display_name)
        if username in {v for v in self._user_mapping.values()}:
            return username
        raise ValueError(f"No username found for display name: {display_name}")

    def shutdown(self) -> None:
        """Clean shutdown of the platform engine."""
        self._platform.shutdown()

    # ------------------------------------------------------------------ #
    # @app_action methods — Core actions
    # ------------------------------------------------------------------ #

    @app_action
    def create_reddit_post(
        self, current_user: str, subreddit: str, title: str, content: str
    ) -> str:
        """Create a new post in a subreddit.

        Args:
            current_user: The full display name of the user posting.
            subreddit: The name of the subreddit to post in (e.g. "general").
            title: The title of the post.
            content: The body text content of the post.
        """
        current_user_full = str(current_user)
        username = self._get_username(current_user)
        post_id = self._platform.create_post(username, subreddit, title, content)
        result_msg = f'{current_user_full} posted in r/{subreddit} (ID: {post_id}): "{title}"'
        self._print(result_msg, emoji="📝")
        self._log_action_event(
            source_user=current_user_full,
            label="post",
            data={
                "post_id": str(post_id),
                "subreddit": subreddit,
                "title": title,
                "content": content,
            },
        )
        return result_msg

    @app_action
    def create_comment(
        self, current_user: str, post_id: int, content: str, parent_id: int | None = None
    ) -> str:
        """Comment on a post or reply to an existing comment.

        Args:
            current_user: The full display name of the user commenting.
            post_id: The ID of the post to comment on.
            content: The text content of the comment.
            parent_id: The ID of the parent comment if replying to a comment
                (omit for top-level comments on a post).
        """
        current_user_full = str(current_user)
        username = self._get_username(current_user)
        comment_id = self._platform.create_comment(username, post_id, content, parent_id=parent_id)
        if parent_id:
            result_msg = (
                f'{current_user_full} replied to comment {parent_id} on post {post_id}: "{content}"'
            )
        else:
            result_msg = f'{current_user_full} commented on post {post_id}: "{content}"'
        self._print(result_msg, emoji="💬")
        self._log_action_event(
            source_user=current_user_full,
            label="comment",
            data={
                "comment_id": str(comment_id),
                "post_id": str(post_id),
                "parent_id": str(parent_id) if parent_id else None,
                "content": content,
            },
        )
        return result_msg

    @app_action
    def upvote(self, current_user: str, target_id: int, target_type: str) -> str:
        """Upvote a post or comment to increase its score.

        Args:
            current_user: The full display name of the user voting.
            target_id: The ID of the post or comment to upvote.
            target_type: Either "post" or "comment".
        """
        current_user_full = str(current_user)
        username = self._get_username(current_user)
        try:
            self._platform.vote(username, target_id, target_type, 1)
            result_msg = f"{current_user_full} upvoted {target_type} {target_id}."
        except Exception as e:
            result_msg = f"Error upvoting {target_type} {target_id}: {e}"
        self._print(result_msg, emoji="⬆️")
        self._log_action_event(
            source_user=current_user_full,
            label="upvote",
            data={"target_id": str(target_id), "target_type": target_type},
        )
        return result_msg

    @app_action
    def downvote(self, current_user: str, target_id: int, target_type: str) -> str:
        """Downvote a post or comment to decrease its score.

        Args:
            current_user: The full display name of the user voting.
            target_id: The ID of the post or comment to downvote.
            target_type: Either "post" or "comment".
        """
        current_user_full = str(current_user)
        username = self._get_username(current_user)
        try:
            self._platform.vote(username, target_id, target_type, -1)
            result_msg = f"{current_user_full} downvoted {target_type} {target_id}."
        except Exception as e:
            result_msg = f"Error downvoting {target_type} {target_id}: {e}"
        self._print(result_msg, emoji="⬇️")
        self._log_action_event(
            source_user=current_user_full,
            label="downvote",
            data={"target_id": str(target_id), "target_type": target_type},
        )
        return result_msg

    @app_action
    def get_home_feed(self, current_user: str, limit: int) -> str:
        """Read your home feed showing posts from subreddits you've joined.

        Args:
            current_user: The full display name of the user reading the feed.
            limit: Maximum number of posts to retrieve.
        """
        current_user_full = str(current_user)
        timeline = self.get_timeline(current_user, limit)
        str_timeline = self.format_timeline_for_observation(timeline)
        self._print(f"Retrieved {len(timeline)} posts for {current_user_full}", emoji="📊")
        self._log_action_event(
            source_user=current_user_full,
            label="get_home_feed",
            data={"num_posts_retrieved": len(timeline)},
        )
        return f"Reddit Home Feed for {current_user_full}:\n{str_timeline}"

    @app_action
    def update_profile(self, current_user: str, bio: str) -> str:
        """Update your profile bio.

        Args:
            current_user: The full display name of the user updating their profile.
            bio: The new bio text for the profile.
        """
        current_user_full = str(current_user)
        username = self._get_username(current_user)
        try:
            self._platform.update_profile(username, bio)
            msg = f'Profile updated for {current_user_full}: "{bio}"'
        except Exception as e:
            msg = f"Error updating profile: {e}"
        self._print(msg, emoji="✏️")
        self._log_action_event(
            source_user=current_user_full,
            label="update_profile",
            data={"new_bio": bio},
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
                    f"  Post Karma: {profile.get('post_karma', 0)}\n"
                    f"  Comment Karma: {profile.get('comment_karma', 0)}\n"
                )
            else:
                msg = f"Profile not found for {target_user_full}."
        except Exception as e:
            msg = f"Error viewing profile for {target_user_full}: {e}"
        self._print(msg, emoji="👤")
        return msg

    @app_action
    def get_post_comments(self, current_user: str, post_id: int, limit: int = 20) -> str:
        """Read the comments on a specific post.

        Args:
            current_user: The full display name of the user reading comments.
            post_id: The ID of the post to read comments for.
            limit: Maximum number of comments to retrieve.
        """
        current_user_full = str(current_user)
        try:
            comments = self._platform.get_post_comments(post_id, limit=limit, as_tree=False)
            if comments:
                msg = f"Comments on post {post_id}:\n"
                for c in comments:
                    cdict = c if isinstance(c, dict) else c.to_dict()
                    indent = "  " if cdict.get("parent_id") else ""
                    msg += (
                        f"{indent}Comment ID: {cdict['id']} | @{cdict['username']}: "
                        f"{cdict['content'][:100]}\n"
                        f"{indent}  Score: ↑{cdict.get('upvotes', 0)} ↓{cdict.get('downvotes', 0)}\n"
                    )
            else:
                msg = f"No comments on post {post_id}."
        except Exception as e:
            msg = f"Error fetching comments for post {post_id}: {e}"
        self._print(msg, emoji="💬")
        self._log_action_event(
            source_user=current_user_full,
            label="get_post_comments",
            data={"post_id": str(post_id)},
        )
        return msg

    @app_action
    def search_subreddits(self, current_user: str, query: str, limit: int = 20) -> str:
        """Search for subreddits by name or description.

        Args:
            current_user: The full display name of the user searching.
            query: The search text to look for.
            limit: Maximum number of results to return.
        """
        try:
            results = self._platform.search_subreddits(query, limit=limit)
            if results:
                msg = f"Subreddit search results for '{query}':\n"
                for sub in results:
                    msg += f"  r/{sub['name']}: {sub.get('description', '')}\n"
            else:
                msg = f"No subreddits found matching '{query}'."
        except Exception as e:
            msg = f"Error searching subreddits: {e}"
        self._print(msg, emoji="🔍")
        return msg

    # ------------------------------------------------------------------ #
    # @app_action methods — Non-essential / commented out
    # ------------------------------------------------------------------ #

    # @app_action
    # def create_subreddit(self, current_user: str, subreddit_name: str, description: str) -> str:
    #     """Create a new subreddit.
    #
    #     Args:
    #         current_user: The full display name of the user creating the subreddit.
    #         subreddit_name: The name for the new subreddit.
    #         description: A description of the subreddit's purpose and topic.
    #     """
    #     current_user_full = str(current_user)
    #     try:
    #         sub_id = self._platform.create_subreddit(subreddit_name, description)
    #         msg = f"{current_user_full} created subreddit r/{subreddit_name} (ID: {sub_id})."
    #     except Exception as e:
    #         msg = f"Error creating subreddit: {e}"
    #     self._print(msg, emoji="🏠")
    #     if self.action_logger:
    #         self.action_logger.log({
    #             "source_user": current_user_full,
    #             "label": "create_subreddit",
    #             "data": {"subreddit_name": subreddit_name, "description": description},
    #         })
    #     return msg

    # @app_action
    # def join_subreddit(self, current_user: str, subreddit_name: str) -> str:
    #     """Join a subreddit to see its posts in your home feed.
    #
    #     Args:
    #         current_user: The full display name of the user joining.
    #         subreddit_name: The name of the subreddit to join.
    #     """
    #     current_user_full = str(current_user)
    #     username = self._get_username(current_user)
    #     try:
    #         self._platform.join_subreddit(username, subreddit_name)
    #         msg = f"{current_user_full} joined r/{subreddit_name}."
    #     except Exception as e:
    #         msg = f"Error joining subreddit: {e}"
    #     self._print(msg, emoji="📌")
    #     if self.action_logger:
    #         self.action_logger.log({
    #             "source_user": current_user_full,
    #             "label": "join_subreddit",
    #             "data": {"subreddit_name": subreddit_name},
    #         })
    #     return msg

    # @app_action
    # def leave_subreddit(self, current_user: str, subreddit_name: str) -> str:
    #     """Leave a subreddit to stop seeing its posts.
    #
    #     Args:
    #         current_user: The full display name of the user leaving.
    #         subreddit_name: The name of the subreddit to leave.
    #     """
    #     current_user_full = str(current_user)
    #     username = self._get_username(current_user)
    #     try:
    #         self._platform.leave_subreddit(username, subreddit_name)
    #         msg = f"{current_user_full} left r/{subreddit_name}."
    #     except Exception as e:
    #         msg = f"Error leaving subreddit: {e}"
    #     self._print(msg, emoji="🚪")
    #     if self.action_logger:
    #         self.action_logger.log({
    #             "source_user": current_user_full,
    #             "label": "leave_subreddit",
    #             "data": {"subreddit_name": subreddit_name},
    #         })
    #     return msg

    # @app_action
    # def get_subreddit_feed(self, current_user: str, subreddit_name: str, limit: int) -> str:
    #     """Read the feed for a specific subreddit.
    #
    #     Args:
    #         current_user: The full display name of the user browsing.
    #         subreddit_name: The name of the subreddit to browse.
    #         limit: Maximum number of posts to retrieve.
    #     """
    #     current_user_full = str(current_user)
    #     username = self._get_username(current_user)
    #     try:
    #         feed = self._platform.get_subreddit_feed(subreddit_name, limit=limit)
    #         posts = feed.get("posts", [])
    #         str_feed = self.format_timeline_for_observation(posts)
    #         msg = f"r/{subreddit_name} Feed for {current_user_full}:\n{str_feed}"
    #     except Exception as e:
    #         msg = f"Error fetching subreddit feed: {e}"
    #     self._print(msg, emoji="📰")
    #     if self.action_logger:
    #         self.action_logger.log({
    #             "source_user": current_user_full,
    #             "label": "get_subreddit_feed",
    #             "data": {"subreddit_name": subreddit_name},
    #         })
    #     return msg
