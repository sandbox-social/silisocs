"""Reddit-like social media app for simulation.

Wraps ``RedditLikePlatform`` (SQLite-backed engine) with the
``SocialBackendApp`` interface so it can be used as a drag-and-drop
replacement for the Mastodon app in the simulation.
"""

from __future__ import annotations

import dataclasses
import logging
import os
from collections.abc import Mapping
from typing import Any

from silisocs.environments.backends.base import (
    ActionResult,
    PlatformBackedSocialApp,
    VisualizerSpec,
    app_action,
    record_invalid_action_target,
    record_unexpected_action_error,
)
from silisocs.environments.backends.event_semantics import social_event_semantics
from silisocs.environments.backends.reddit_like.engine import RedditLikePlatform
from silisocs.environments.backends.sqlite_state import (
    restore_sqlite_database,
    snapshot_sqlite_database,
)

_LOGGER = logging.getLogger(__name__)

# Custom-mode verbs whose TARGET ID must be a numeric post/comment id.
_TARGET_REQUIRED_ACTIONS = frozenset(
    {
        "comment",
        "create_comment",
        "reply",
        "upvote",
        "downvote",
        "upvote_comment",
        "downvote_comment",
    }
)


def _coerce_target_id(target_id: Any) -> int | None:
    """Parse a custom-mode TARGET ID, or None when it is not a numeric id."""
    try:
        return int(str(target_id).strip())
    except (TypeError, ValueError):
        return None


def _subreddit_name(sub_cfg: Any) -> str:
    """Return a configured subreddit's canonical name, refusing to invent one.

    A mistyped key (``names:``) previously fell back to ``"general"``, quietly
    creating and subscribing everyone to the wrong community — a scenario that
    never runs what it declares. Config errors raise at setup.

    The ``r/`` prefix is stripped HERE, once, so every setup caller resolves the
    same canonical name. ``create_subreddit`` strips it internally but
    ``join_subreddit``/``get_subreddit_id`` do not, so a configured
    ``name: "r/politics"`` created ``politics`` and then failed to find it to
    subscribe anyone.
    """
    if not isinstance(sub_cfg, dict):
        name = str(sub_cfg).strip()
    else:
        name = str(sub_cfg.get("name", "")).strip()
    name = name.removeprefix("r/").strip()
    if not name:
        raise ValueError(
            "Each entry of env.graph_config.subreddits must declare a non-empty 'name'; "
            f"got {sub_cfg!r}."
        )
    return name


@dataclasses.dataclass
class RedditLikeApp(PlatformBackedSocialApp):
    """Reddit-like social media app.

    A forum-style platform where users create posts in subreddits, comment
    on posts (with threaded replies), and upvote/downvote content.
    Uses a local SQLite database as the backend.
    """

    action_logger: Any = None
    # Authoritative checkpoint state: full SQLite snapshot + user mapping.
    provides_checkpoint_state = True
    # Self-described analysis semantics (custom-mode labels the decorators can't
    # see); decorator-declared tags on new actions merge in on top.
    event_semantics = social_event_semantics(
        roots={"post"},
        replies={"comment"},
        reactions={"upvote", "downvote"},
        follows={"mute_user", "unmute_user"},
        negative={"downvote", "dislike_post", "report_post"},
        reads={"get_home_feed", "get_post_comments", "get_trending", "timeline_retrieval"},
        content_ids=("post_id",),
        response_ids=("comment_id",),
        parent_ids=("parent_id", "post_id"),
        reaction_target_id_fields=("target_id",),
        reaction_target_type_fields=("target_type",),
    )
    visualizer = VisualizerSpec(
        "REDDIT_LIKE_DB",
        "silisocs.environments.backends.reddit_like.visualizer.server",
        8001,
        app_factory=(
            "silisocs.environments.backends.reddit_like.visualizer.server:create_viewer_app"
        ),
    )
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
            sub_name = _subreddit_name(sub_cfg)
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

        # Create users in ONE bulk transaction (N serialized create round-trips
        # otherwise dominate init), then log the per-user init events.
        for display_name in agent_names:
            username = self._display_name_to_username(display_name)
            self._user_mapping[display_name] = username
        self._platform.create_users(
            [
                (self._user_mapping[display_name], str(agent_bios.get(display_name, "")))
                for display_name in agent_names
            ]
        )
        for display_name in agent_names:
            self._log_action_event(
                display_name, "init_create_user", {"username": self._user_mapping[display_name]}
            )

        # Subscribe agents to subreddits based on role.
        sub_count = 0
        for sub_cfg in subreddit_configs:
            if not isinstance(sub_cfg, dict):
                sub_cfg = {"name": str(sub_cfg), "roles": "all"}
            sub_name = _subreddit_name(sub_cfg)
            allowed_roles = sub_cfg.get("roles", "all")

            for display_name in agent_names:
                agent_role = sim_roles.get(display_name, "")
                if allowed_roles == "all" or agent_role in allowed_roles:
                    username = self._get_username(display_name)
                    # Setup, not a turn: a failed subscription means the scenario
                    # runs with a subreddit nobody is in, so it fails loudly here
                    # rather than producing a silently empty world.
                    self._platform.join_subreddit(username, sub_name)
                    sub_count += 1

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
        # A failed read PROPAGATES: swallowing it here would turn a transient DB
        # error into a legitimately-empty feed, and get_home_feed would log a
        # committed ``num_posts_retrieved: 0`` row for a read that never happened.
        feed = self._platform.get_feed("home", username=username, limit=limit)
        return feed.get("posts", [])

    def init_recsys(
        self,
        recsys_type: str = "reddit",
        user_context_recent_posts: int = 0,
        include_like_trace: bool = False,
        like_trace_window: int = 5,
        like_trace_weight: float = 0.0,
        include_like_trace_in_context: bool = True,
    ) -> None:
        """Initialize recommendation algorithm(s) on the underlying platform.

        Accepts the full like-trace parameter set for interface compatibility
        with :class:`SocialRecommendationUpdateComponent` (which always passes
        them). The Reddit recsys engine does not implement like-trace
        personalization, so those parameters are not forwarded to the platform.
        """
        del (
            user_context_recent_posts,
            include_like_trace,
            like_trace_window,
            like_trace_weight,
            include_like_trace_in_context,
        )
        self._platform.init_recsys(recsys_type=recsys_type)
        self._log_action_event(
            source_user="system",
            label="recsys_init",
            data={"recsys_type": str(recsys_type)},
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

    def action_aliases(self) -> list[set[str]]:
        """Domain-verb <-> method-name synonyms (keep in sync with parse_and_resolve_action)."""
        return [
            {"post", "create_reddit_post"},
            {"comment", "create_comment", "reply"},
        ]

    def parse_and_resolve_action(self, user_name: str, action_data: dict) -> str | ActionResult:
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

        numeric_target = _coerce_target_id(target_id)
        if action_type in _TARGET_REQUIRED_ACTIONS and numeric_target is None:
            return record_invalid_action_target(action_type, target_id)
        numeric_target = int(numeric_target or 0)

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
                return self.create_comment(user_name, numeric_target, content)
            if action_type == "create_comment":
                # Explicit action name for comment creation
                return self.create_comment(user_name, numeric_target, content)
            if action_type == "upvote":
                return self.upvote(user_name, numeric_target, "post")
            if action_type == "downvote":
                return self.downvote(user_name, numeric_target, "post")
            if action_type == "upvote_comment":
                # Upvote a comment (data-driven voting)
                return self.upvote(user_name, numeric_target, "comment")
            if action_type == "downvote_comment":
                # Downvote a comment (data-driven voting)
                return self.downvote(user_name, numeric_target, "comment")
            if action_type == "reply":
                # Treat reply as a comment on a post
                return self.create_comment(user_name, numeric_target, content)
            return f"Unknown action type: {action_type}"
        except ValueError as e:
            # The platform's language for a legitimate rejection (unknown user,
            # missing post/comment): the agent sees the message, nothing is counted.
            self._print(f"Error resolving action {action_type}: {e}", color="red")
            return f"Error performing {action_type}: {e}"
        except Exception as e:
            # Anything else (a DB error, a refactor's AttributeError) is a real
            # failure: count it at the boundary, then let the engine's turn
            # isolation handle it instead of disguising it as an observation.
            record_unexpected_action_error(action_type, e)
            raise

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
        if username in set(self._user_mapping.values()):
            return username
        raise ValueError(f"No username found for display name: {display_name}")

    def shutdown(self) -> None:
        """Clean shutdown of the platform engine."""
        self._platform.shutdown()

    def get_state(self) -> dict[str, Any]:
        """Return serializable backend state for checkpoints."""
        return {
            "db_snapshot_b64": snapshot_sqlite_database(self.db_path),
            "user_mapping": dict(self._user_mapping),
            "default_subreddit": str(getattr(self, "_default_subreddit", "general")),
            "last_initialization_stats": dict(getattr(self, "_last_initialization_stats", {})),
            "committed_events": self._committed_events_state(),
        }

    def set_state(self, state: dict[str, Any]) -> None:
        """Restore backend state from a checkpoint payload."""
        if not isinstance(state, Mapping):
            raise TypeError("RedditLikeApp checkpoint state must be a mapping.")
        snapshot = state.get("db_snapshot_b64")
        if not isinstance(snapshot, str) or not snapshot:
            raise ValueError("RedditLikeApp checkpoint state requires db_snapshot_b64.")
        self._close_platform_for_restore()
        restore_sqlite_database(self.db_path, snapshot)
        self._platform = RedditLikePlatform(self.db_path, use_queue=True)
        user_mapping = state.get("user_mapping", {})
        if not isinstance(user_mapping, Mapping):
            raise TypeError("RedditLikeApp checkpoint user_mapping must be a mapping.")
        self._user_mapping = {str(k): str(v) for k, v in user_mapping.items()}
        self._default_subreddit = str(state.get("default_subreddit") or "general")
        stats = state.get("last_initialization_stats", {})
        if isinstance(stats, Mapping):
            self._last_initialization_stats = dict(stats)
        self._restore_committed_events(state.get("committed_events"))

    def _close_platform_for_restore(self) -> None:
        # Best-effort: the DB file is replaced right after this, so a failed close
        # must not abort the restore — but it must be visible, because a connection
        # left open is exactly what corrupts the replacement.
        try:
            local_conn = getattr(getattr(self._platform, "_local", None), "conn", None)
            if local_conn is not None:
                local_conn.close()
                self._platform._local.conn = None
        except Exception:
            _LOGGER.warning(
                "Failed to close the thread-local reddit_like connection before checkpoint "
                "restore of %s.",
                self.db_path,
                exc_info=True,
            )
        try:
            self._platform.shutdown()
        except Exception:
            _LOGGER.warning(
                "Failed to shut down the reddit_like platform before checkpoint restore of %s; "
                "open connections may interfere with the database replacement.",
                self.db_path,
                exc_info=True,
            )

    # ------------------------------------------------------------------ #
    # @app_action methods — Core actions
    # ------------------------------------------------------------------ #

    @app_action
    def create_reddit_post(self, agent_name: str, subreddit: str, title: str, content: str) -> str:
        """Create a new post in a subreddit.

        Args:
            agent_name: The full display name of the user posting.
            subreddit: The name of the subreddit to post in (e.g. "general").
            title: The title of the post.
            content: The body text content of the post.
        """
        actor_display_name = str(agent_name)
        username = self._get_username(agent_name)
        post_id = self._platform.create_post(username, subreddit, title, content)
        result_msg = f'{actor_display_name} posted in r/{subreddit} (ID: {post_id}): "{title}"'
        self._print(result_msg, emoji="📝")
        self._log_action_event(
            source_user=actor_display_name,
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
        self, agent_name: str, post_id: int, content: str, parent_id: int | None = None
    ) -> str:
        """Comment on a post or reply to an existing comment.

        Args:
            agent_name: The full display name of the user commenting.
            post_id: The ID of the post to comment on.
            content: The text content of the comment.
            parent_id: The ID of the parent comment if replying to a comment
                (omit for top-level comments on a post).
        """
        actor_display_name = str(agent_name)
        username = self._get_username(agent_name)
        comment_id = self._platform.create_comment(username, post_id, content, parent_id=parent_id)
        if parent_id:
            result_msg = f'{actor_display_name} replied to comment {parent_id} on post {post_id}: "{content}"'
        else:
            result_msg = f'{actor_display_name} commented on post {post_id}: "{content}"'
        self._print(result_msg, emoji="💬")
        self._log_action_event(
            source_user=actor_display_name,
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
    def upvote(self, agent_name: str, target_id: int, target_type: str) -> str | ActionResult:
        """Upvote a post or comment to increase its score.

        Args:
            agent_name: The full display name of the user voting.
            target_id: The ID of the post or comment to upvote.
            target_type: Either "post" or "comment".
        """
        actor_display_name = str(agent_name)
        username = self._get_username(agent_name)
        try:
            # Returns False for a re-affirming vote (already upvoted) — a no-op.
            committed = self._platform.vote(username, target_id, target_type, 1) is not False
            result_msg = (
                f"{actor_display_name} upvoted {target_type} {target_id}."
                if committed
                else f"{actor_display_name} had already upvoted {target_type} {target_id}."
            )
        except ValueError as e:
            result_msg = f"Error upvoting {target_type} {target_id}: {e}"
            committed = False
        self._print(result_msg, emoji="⬆️")
        # Committed state changes only: failures leave no row in the canonical
        # action log (replay/eval consume it as ground truth).
        if not committed:
            return ActionResult(result_msg, committed=False)
        self._log_action_event(
            source_user=actor_display_name,
            label="upvote",
            data={"target_id": str(target_id), "target_type": target_type},
        )
        return result_msg

    @app_action
    def downvote(self, agent_name: str, target_id: int, target_type: str) -> str | ActionResult:
        """Downvote a post or comment to decrease its score.

        Args:
            agent_name: The full display name of the user voting.
            target_id: The ID of the post or comment to downvote.
            target_type: Either "post" or "comment".
        """
        actor_display_name = str(agent_name)
        username = self._get_username(agent_name)
        try:
            # Returns False for a re-affirming vote (already downvoted) — a no-op.
            committed = self._platform.vote(username, target_id, target_type, -1) is not False
            result_msg = (
                f"{actor_display_name} downvoted {target_type} {target_id}."
                if committed
                else f"{actor_display_name} had already downvoted {target_type} {target_id}."
            )
        except ValueError as e:
            result_msg = f"Error downvoting {target_type} {target_id}: {e}"
            committed = False
        self._print(result_msg, emoji="⬇️")
        if not committed:
            return ActionResult(result_msg, committed=False)
        self._log_action_event(
            source_user=actor_display_name,
            label="downvote",
            data={"target_id": str(target_id), "target_type": target_type},
        )
        return result_msg

    @app_action
    def get_home_feed(self, agent_name: str, limit: int) -> str:
        """Read your home feed showing posts from subreddits you've joined.

        Args:
            agent_name: The full display name of the user reading the feed.
            limit: Maximum number of posts to retrieve.
        """
        actor_display_name = str(agent_name)
        timeline = self.get_timeline(agent_name, limit)
        str_timeline = self.format_timeline_for_observation(timeline)
        self._print(f"Retrieved {len(timeline)} posts for {actor_display_name}", emoji="📊")
        self._log_action_event(
            source_user=actor_display_name,
            label="get_home_feed",
            data={"num_posts_retrieved": len(timeline)},
        )
        return f"Reddit Home Feed for {actor_display_name}:\n{str_timeline}"

    @app_action
    def update_profile(self, agent_name: str, bio: str) -> str | ActionResult:
        """Update your profile bio.

        Args:
            agent_name: The full display name of the user updating their profile.
            bio: The new bio text for the profile.
        """
        actor_display_name = str(agent_name)
        username = self._get_username(agent_name)
        try:
            self._platform.update_profile(username, bio)
            msg = f'Profile updated for {actor_display_name}: "{bio}"'
            committed = True
        except ValueError as e:
            msg = f"Error updating profile: {e}"
            committed = False
        self._print(msg, emoji="✏️")
        if not committed:
            return ActionResult(msg, committed=False)
        self._log_action_event(
            source_user=actor_display_name,
            label="update_profile",
            data={"new_bio": bio},
        )
        return msg

    @app_action
    def view_profile(self, agent_name: str, target_user: str) -> str | ActionResult:
        """View a user's profile including their bio and stats.

        Args:
            agent_name: The full display name of the user viewing the profile.
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
                self._print(f"Profile not found for {target_user_full}.", emoji="👤")
                return ActionResult(f"Profile not found for {target_user_full}.", committed=False)
        except ValueError as e:
            msg = f"Error viewing profile for {target_user_full}: {e}"
            self._print(msg, emoji="👤")
            return ActionResult(msg, committed=False)
        self._print(msg, emoji="👤")
        return msg

    @app_action
    def get_post_comments(
        self, agent_name: str, post_id: int, limit: int = 20
    ) -> str | ActionResult:
        """Read the comments on a specific post.

        Args:
            agent_name: The full display name of the user reading comments.
            post_id: The ID of the post to read comments for.
            limit: Maximum number of comments to retrieve.
        """
        actor_display_name = str(agent_name)
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
            committed = True
        except ValueError as e:
            msg = f"Error fetching comments for post {post_id}: {e}"
            committed = False
        self._print(msg, emoji="💬")
        if not committed:
            return ActionResult(msg, committed=False)
        self._log_action_event(
            source_user=actor_display_name,
            label="get_post_comments",
            data={"post_id": str(post_id)},
        )
        return msg

    @app_action
    def search_subreddits(self, agent_name: str, query: str, limit: int = 20) -> str | ActionResult:
        """Search for subreddits by name or description.

        Args:
            agent_name: The full display name of the user searching.
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
        except ValueError as e:
            msg = f"Error searching subreddits: {e}"
            self._print(msg, emoji="🔍")
            return ActionResult(msg, committed=False)
        self._print(msg, emoji="🔍")
        return msg

    # ================================================================ #
    # Extended social actions
    # ================================================================ #

    @app_action
    def unlike_post(self, agent_name: str, post_id: int) -> str | ActionResult:
        """Remove an upvote from a post.

        Args:
            agent_name: The full display name of the user removing the upvote.
            post_id: The ID of the post to unlike.
        """
        actor_display_name = str(agent_name)
        username = self._get_username(agent_name)
        result = self._platform.unlike_post(username, post_id)
        msg = f"{actor_display_name} {'removed upvote from' if result else 'could not remove upvote from'} post {post_id}."
        self._print(msg, emoji="🚫⬆️")
        if not result:
            return ActionResult(msg, committed=False)
        self._log_action_event(
            source_user=actor_display_name,
            label="unlike_post",
            data={"post_id": str(post_id)},
        )
        return msg

    @app_action
    def dislike_post(self, agent_name: str, post_id: int) -> str | ActionResult:
        """Downvote a post (negative reaction).

        Args:
            agent_name: The full display name of the user downvoting.
            post_id: The ID of the post to downvote.
        """
        actor_display_name = str(agent_name)
        username = self._get_username(agent_name)
        result = self._platform.dislike_post(username, post_id)
        msg = f"{actor_display_name} {'downvoted' if result else 'could not downvote'} post {post_id}."
        self._print(msg, emoji="⬇️")
        if not result:
            return ActionResult(msg, committed=False)
        self._log_action_event(
            source_user=actor_display_name,
            label="dislike_post",
            data={"post_id": str(post_id)},
        )
        return msg

    @app_action
    def undo_dislike_post(self, agent_name: str, post_id: int) -> str | ActionResult:
        """Remove a downvote from a post.

        Args:
            agent_name: The full display name of the user removing the downvote.
            post_id: The ID of the post to undo downvote for.
        """
        actor_display_name = str(agent_name)
        username = self._get_username(agent_name)
        result = self._platform.undo_dislike_post(username, post_id)
        msg = f"{actor_display_name} {'removed downvote from' if result else 'could not remove downvote from'} post {post_id}."
        self._print(msg, emoji="🆗")
        if not result:
            return ActionResult(msg, committed=False)
        self._log_action_event(
            source_user=actor_display_name,
            label="undo_dislike_post",
            data={"post_id": str(post_id)},
        )
        return msg

    @app_action
    def mute_user(self, agent_name: str, target_user: str) -> str | ActionResult:
        """Mute another user to hide their posts.

        Args:
            agent_name: The full display name of the user doing the muting.
            target_user: The full display name of the user to mute.
        """
        actor_display_name = str(agent_name)
        target_user_full = str(target_user)
        src_username = self._get_username(agent_name)
        tgt_username = self._get_username(target_user)
        result = self._platform.mute_user(src_username, tgt_username)
        msg = f"{actor_display_name} {'muted' if result else 'could not mute'} {target_user_full}."
        self._print(msg, emoji="🔇")
        if not result:
            return ActionResult(msg, committed=False)
        self._log_action_event(
            source_user=actor_display_name,
            label="mute_user",
            data={"target_user": target_user_full},
        )
        return msg

    @app_action
    def unmute_user(self, agent_name: str, target_user: str) -> str | ActionResult:
        """Unmute a user.

        Args:
            agent_name: The full display name of the user doing the unmuting.
            target_user: The full display name of the user to unmute.
        """
        actor_display_name = str(agent_name)
        target_user_full = str(target_user)
        src_username = self._get_username(agent_name)
        tgt_username = self._get_username(target_user)
        result = self._platform.unmute_user(src_username, tgt_username)
        msg = f"{actor_display_name} {'unmuted' if result else 'could not unmute'} {target_user_full}."
        self._print(msg, emoji="🔊")
        if not result:
            return ActionResult(msg, committed=False)
        self._log_action_event(
            source_user=actor_display_name,
            label="unmute_user",
            data={"target_user": target_user_full},
        )
        return msg

    @app_action
    def report_post(
        self, agent_name: str, post_id: int, reason: str = "Inappropriate content"
    ) -> str | ActionResult:
        """Report a post for moderation.

        Args:
            agent_name: The full display name of the user reporting.
            post_id: The ID of the post to report.
            reason: The reason for reporting.
        """
        actor_display_name = str(agent_name)
        username = self._get_username(agent_name)
        result = self._platform.report_post(username, post_id, reason)
        msg = f"{actor_display_name} {'reported' if result else 'could not report'} post {post_id} ({reason})."
        self._print(msg, emoji="⚠️")
        if not result:
            return ActionResult(msg, committed=False)
        self._log_action_event(
            source_user=actor_display_name,
            label="report_post",
            data={"post_id": str(post_id), "reason": reason},
        )
        return msg

    @app_action
    def get_trending_posts(
        self, agent_name: str, limit: int = 10, days: int = 7
    ) -> str | ActionResult:
        """Get trending posts from the last N days.

        Args:
            agent_name: The full display name of the user requesting trends.
            limit: Maximum number of posts to return.
            days: Number of days to consider for trending.
        """
        actor_display_name = str(agent_name)
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
            committed = True
        except ValueError as e:
            msg = f"Error getting trending posts: {e}"
            committed = False
        self._print(msg, emoji="🔥")
        if not committed:
            return ActionResult(msg, committed=False)
        self._log_action_event(
            source_user=actor_display_name,
            label="get_trending",
            data={"limit": limit, "days": days},
        )
        return msg

    @app_action
    def do_nothing(self, agent_name: str) -> str:
        """Take no action (used as a baseline or filler action).

        Args:
            agent_name: The full display name of the user.
        """
        actor_display_name = str(agent_name)
        msg = f"{actor_display_name} did nothing."
        self._log_action_event(
            source_user=actor_display_name,
            label="do_nothing",
            data={},
        )
        return msg

    # ------------------------------------------------------------------ #
    # @app_action methods — Subreddit management
    # ------------------------------------------------------------------ #

    @app_action
    def create_subreddit(
        self, agent_name: str, subreddit_name: str, description: str
    ) -> str | ActionResult:
        """Create a new subreddit.

        Args:
            agent_name: The full display name of the user creating the subreddit.
            subreddit_name: The name for the new subreddit.
            description: A description of the subreddit's purpose and topic.
        """
        actor_display_name = str(agent_name)
        try:
            sub_id = self._platform.create_subreddit(subreddit_name, description)
            msg = f"{actor_display_name} created subreddit r/{subreddit_name} (ID: {sub_id})."
            committed = True
        except ValueError as e:
            msg = f"Error creating subreddit: {e}"
            committed = False
        self._print(msg, emoji="🏠")
        if not committed:
            return ActionResult(msg, committed=False)
        self._log_action_event(
            source_user=actor_display_name,
            label="create_subreddit",
            data={"subreddit_name": subreddit_name, "description": description},
        )
        return msg

    @app_action
    def join_subreddit(self, agent_name: str, subreddit_name: str) -> str | ActionResult:
        """Join a subreddit to see its posts in your home feed.

        Args:
            agent_name: The full display name of the user joining.
            subreddit_name: The name of the subreddit to join.
        """
        actor_display_name = str(agent_name)
        username = self._get_username(agent_name)
        try:
            self._platform.join_subreddit(username, subreddit_name)
            msg = f"{actor_display_name} joined r/{subreddit_name}."
            committed = True
        except ValueError as e:
            msg = f"Error joining subreddit: {e}"
            committed = False
        self._print(msg, emoji="📌")
        if not committed:
            return ActionResult(msg, committed=False)
        self._log_action_event(
            source_user=actor_display_name,
            label="join_subreddit",
            data={"subreddit_name": subreddit_name},
        )
        return msg

    @app_action
    def leave_subreddit(self, agent_name: str, subreddit_name: str) -> str | ActionResult:
        """Leave a subreddit to stop seeing its posts.

        Args:
            agent_name: The full display name of the user leaving.
            subreddit_name: The name of the subreddit to leave.
        """
        actor_display_name = str(agent_name)
        username = self._get_username(agent_name)
        try:
            self._platform.leave_subreddit(username, subreddit_name)
            msg = f"{actor_display_name} left r/{subreddit_name}."
            committed = True
        except ValueError as e:
            msg = f"Error leaving subreddit: {e}"
            committed = False
        self._print(msg, emoji="🚪")
        if not committed:
            return ActionResult(msg, committed=False)
        self._log_action_event(
            source_user=actor_display_name,
            label="leave_subreddit",
            data={"subreddit_name": subreddit_name},
        )
        return msg

    @app_action
    def get_subreddit_feed(
        self, agent_name: str, subreddit_name: str, limit: int
    ) -> str | ActionResult:
        """Read the feed for a specific subreddit.

        Args:
            agent_name: The full display name of the user browsing.
            subreddit_name: The name of the subreddit to browse.
            limit: Maximum number of posts to retrieve.
        """
        actor_display_name = str(agent_name)
        try:
            feed = self._platform.get_subreddit_feed(subreddit_name, limit=limit)
            posts = feed.get("posts", [])
            str_feed = self.format_timeline_for_observation(posts)
            msg = f"r/{subreddit_name} Feed for {actor_display_name}:\n{str_feed}"
            committed = True
        except ValueError as e:
            msg = f"Error fetching subreddit feed: {e}"
            committed = False
        self._print(msg, emoji="📰")
        if not committed:
            return ActionResult(msg, committed=False)
        self._log_action_event(
            source_user=actor_display_name,
            label="get_subreddit_feed",
            data={"subreddit_name": subreddit_name},
        )
        return msg
