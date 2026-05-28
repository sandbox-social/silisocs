"""Reddit-like social media app for simulation.

Wraps ``RedditLikePlatform`` (SQLite-backed engine) with the
``SocialBackendApp`` interface so it can be used as a drag-and-drop
replacement for the Mastodon app in the simulation.
"""

from __future__ import annotations

import dataclasses
import os
from typing import Any

from silisocs.environments.backends.base import SocialBackendApp, app_action
from silisocs.environments.backends.reddit_like.engine import RedditLikePlatform


@dataclasses.dataclass
class RedditLikeApp(SocialBackendApp):
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
    # SocialBackendApp required interface
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
        """Compatibility no-op; runtime initializers own forum setup."""
        del agent_names, kwargs

    def setup_social_state(
        self,
        *,
        agent_names: list[str],
        sim_roles: dict[str, str] | None = None,
        graph_config: dict[str, Any] | None = None,
        following_graph: dict[str, list[str]] | None = None,
        agent_bios: dict[str, str] | None = None,
    ) -> None:
        """Set up users, subreddits, and role-based memberships.

        Reddit uses **subreddit membership** instead of a follow graph.
        Configure subreddits and which roles subscribe to them via the
        GM initialize component graph config::

            env.gm.components.initialize.params.graph:
              subreddits:
                - name: general
                  description: General discussion
                  roles: all
                - name: politics
                  description: Political discussion
                  roles: [voter, candidate]
              default_subreddit: general

        """
        del following_graph
        sim_roles = dict(sim_roles or {})
        graph_config = dict(graph_config or {})
        agent_bios = dict(agent_bios or {})

        subreddit_configs = graph_config.get(
            "subreddits",
            [{"name": "general", "description": "General discussion", "roles": "all"}],
        )
        default_subreddit = graph_config.get("default_subreddit", "general")

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
            self._platform.create_user(username, bio=str(agent_bios.get(display_name, "")))
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

        self._last_initialization_stats = {
            "platform": "reddit_like",
            "num_users": len(agent_names),
            "num_subreddits": len(subreddit_configs),
            "num_subscriptions": sub_count,
        }
        self._default_subreddit = str(default_subreddit)
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

    def get_timeline_mode(
        self,
        timeline_mode: str,
        user_name: str,
        limit: int = 10,
        recsys_type: str | None = None,
        **timeline_config: dict,
    ) -> list[dict]:
        """Fetch timeline using a configured timeline mode.

        Args:
            timeline_mode: Timeline mode (follower_chronological, pure_recsys,
                hybrid_recsys_follower, etc.)
            user_name: Display name of the user.
            limit: Maximum number of posts.
            recsys_type: Optional recommendation algorithm override.
            **timeline_config: Mode-specific config (e.g., recsys_ratio).

        Returns
        -------
            List of post dicts for the timeline.
        """
        username = self._get_username(user_name)
        try:
            timeline = self._platform.get_timeline(
                timeline_mode,
                username,
                limit,
                recsys_type=recsys_type,
                **timeline_config,
            )
            timeline_list = list(timeline or [])
            self._log_action_event(
                source_user=str(user_name),
                label="timeline_retrieval",
                data={
                    "timeline_mode": str(timeline_mode),
                    "recsys_type": str(recsys_type or ""),
                    "requested_limit": int(limit),
                    "returned_posts": len(timeline_list),
                },
            )
            return timeline_list
        except Exception as e:
            self._log_action_event(
                source_user=str(user_name),
                label="timeline_retrieval_error",
                data={
                    "timeline_mode": str(timeline_mode),
                    "recsys_type": str(recsys_type or ""),
                    "requested_limit": int(limit),
                    "error": str(e),
                },
            )
            self._print(
                f"Error fetching timeline with mode '{timeline_mode}' for {username}: {e}",
                color="red",
            )
            return []

    def init_recsys(self, recsys_type: str = "reddit") -> None:
        """Initialize recommendation algorithm(s) on the underlying platform."""
        self._platform.init_recsys(recsys_type=recsys_type)
        self._log_action_event(
            source_user="system",
            label="recsys_init",
            data={"recsys_type": str(recsys_type)},
        )

    def update_recommendations(
        self, active_user_ids: list[int] | None = None, max_posts: int = 10
    ) -> None:
        """Update recommendation rows via the underlying platform and log counts."""
        self._platform.update_recommendations(active_user_ids=active_user_ids, max_posts=max_posts)
        rec_count = 0
        try:
            with self._platform.get_connection() as conn:
                row = conn.execute("SELECT COUNT(*) AS n FROM recommendations").fetchone()
                rec_count = int(row["n"]) if row else 0
        except Exception:
            rec_count = -1

        self._log_action_event(
            source_user="system",
            label="recsys_update",
            data={
                "active_user_ids_count": len(active_user_ids or []),
                "max_posts": int(max_posts),
                "recommendation_rows": rec_count,
            },
        )

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
            if action_type in {"finished", "finish", "finish_action_episode"}:
                return self.finish_action_episode()
            if action_type == "post":
                # Default to "general" subreddit; content is used as both title and body
                return self.create_reddit_post(user_name, "general", content[:100], content)
            if action_type == "create_reddit_post":
                # Explicit action name for post creation
                return self.create_reddit_post(user_name, "general", content[:100], content)
            if action_type == "comment":
                return self.create_comment(user_name, int(target_id), content)
            if action_type == "create_comment":
                # Explicit action name for comment creation
                return self.create_comment(user_name, int(target_id), content)
            if action_type == "upvote":
                return self.upvote(user_name, int(target_id), "post")
            if action_type == "downvote":
                return self.downvote(user_name, int(target_id), "post")
            if action_type == "upvote_comment":
                # Upvote a comment (OASIS data-driven voting)
                return self.upvote(user_name, int(target_id), "comment")
            if action_type == "downvote_comment":
                # Downvote a comment (OASIS data-driven voting)
                return self.downvote(user_name, int(target_id), "comment")
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

    # ================================================================ #
    # OASIS-Compatible Actions
    # ================================================================ #

    @app_action
    def unlike_post(self, current_user: str, post_id: int) -> str:
        """Remove an upvote from a post.

        Args:
            current_user: The full display name of the user removing the upvote.
            post_id: The ID of the post to unlike.
        """
        current_user_full = str(current_user)
        username = self._get_username(current_user)
        result = self._platform.unlike_post(username, post_id)
        msg = f"{current_user_full} {'removed upvote from' if result else 'could not remove upvote from'} post {post_id}."
        self._print(msg, emoji="🚫⬆️")
        self._log_action_event(
            source_user=current_user_full,
            label="unlike_post",
            data={"post_id": str(post_id)},
        )
        return msg

    @app_action
    def dislike_post(self, current_user: str, post_id: int) -> str:
        """Downvote a post (negative reaction).

        Args:
            current_user: The full display name of the user downvoting.
            post_id: The ID of the post to downvote.
        """
        current_user_full = str(current_user)
        username = self._get_username(current_user)
        result = self._platform.dislike_post(username, post_id)
        msg = (
            f"{current_user_full} {'downvoted' if result else 'could not downvote'} post {post_id}."
        )
        self._print(msg, emoji="⬇️")
        self._log_action_event(
            source_user=current_user_full,
            label="dislike_post",
            data={"post_id": str(post_id)},
        )
        return msg

    @app_action
    def undo_dislike_post(self, current_user: str, post_id: int) -> str:
        """Remove a downvote from a post.

        Args:
            current_user: The full display name of the user removing the downvote.
            post_id: The ID of the post to undo downvote for.
        """
        current_user_full = str(current_user)
        username = self._get_username(current_user)
        result = self._platform.undo_dislike_post(username, post_id)
        msg = f"{current_user_full} {'removed downvote from' if result else 'could not remove downvote from'} post {post_id}."
        self._print(msg, emoji="🆗")
        self._log_action_event(
            source_user=current_user_full,
            label="undo_dislike_post",
            data={"post_id": str(post_id)},
        )
        return msg

    @app_action
    def mute_user(self, current_user: str, target_user: str) -> str:
        """Mute another user to hide their posts.

        Args:
            current_user: The full display name of the user doing the muting.
            target_user: The full display name of the user to mute.
        """
        current_user_full = str(current_user)
        target_user_full = str(target_user)
        src_username = self._get_username(current_user)
        tgt_username = self._get_username(target_user)
        result = self._platform.mute_user(src_username, tgt_username)
        msg = f"{current_user_full} {'muted' if result else 'could not mute'} {target_user_full}."
        self._print(msg, emoji="🔇")
        self._log_action_event(
            source_user=current_user_full,
            label="mute_user",
            data={"target_user": target_user_full},
        )
        return msg

    @app_action
    def unmute_user(self, current_user: str, target_user: str) -> str:
        """Unmute a user.

        Args:
            current_user: The full display name of the user doing the unmuting.
            target_user: The full display name of the user to unmute.
        """
        current_user_full = str(current_user)
        target_user_full = str(target_user)
        src_username = self._get_username(current_user)
        tgt_username = self._get_username(target_user)
        result = self._platform.unmute_user(src_username, tgt_username)
        msg = (
            f"{current_user_full} {'unmuted' if result else 'could not unmute'} {target_user_full}."
        )
        self._print(msg, emoji="🔊")
        self._log_action_event(
            source_user=current_user_full,
            label="unmute_user",
            data={"target_user": target_user_full},
        )
        return msg

    @app_action
    def report_post(
        self, current_user: str, post_id: int, reason: str = "Inappropriate content"
    ) -> str:
        """Report a post for moderation.

        Args:
            current_user: The full display name of the user reporting.
            post_id: The ID of the post to report.
            reason: The reason for reporting.
        """
        current_user_full = str(current_user)
        username = self._get_username(current_user)
        result = self._platform.report_post(username, post_id, reason)
        msg = f"{current_user_full} {'reported' if result else 'could not report'} post {post_id} ({reason})."
        self._print(msg, emoji="⚠️")
        self._log_action_event(
            source_user=current_user_full,
            label="report_post",
            data={"post_id": str(post_id), "reason": reason},
        )
        return msg

    @app_action
    def get_trending_posts(self, current_user: str, limit: int = 10, days: int = 7) -> str:
        """Get trending posts from the last N days.

        Args:
            current_user: The full display name of the user requesting trends.
            limit: Maximum number of posts to return.
            days: Number of days to consider for trending.
        """
        current_user_full = str(current_user)
        try:
            results = self._platform.get_trending_posts(limit=limit, days=days)
            if results:
                msg = f"Trending posts (last {days} days):\n"
                for post in results:
                    engagement = post.get("engagement_score", 0)
                    msg += (
                        f"  ID:{post['id']} | r/{post.get('community', 'unknown')}: "
                        f"{post['content'][:60]}... (engagement: {engagement:.1f})\n"
                    )
            else:
                msg = "No trending posts found."
        except Exception as e:
            msg = f"Error getting trending posts: {e}"
        self._print(msg, emoji="🔥")
        self._log_action_event(
            source_user=current_user_full,
            label="get_trending",
            data={"limit": limit, "days": days},
        )
        return msg

    @app_action
    def do_nothing(self, current_user: str) -> str:
        """Take no action (used as a baseline or filler action).

        Args:
            current_user: The full display name of the user.
        """
        current_user_full = str(current_user)
        msg = f"{current_user_full} did nothing."
        self._log_action_event(
            source_user=current_user_full,
            label="do_nothing",
            data={},
        )
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
