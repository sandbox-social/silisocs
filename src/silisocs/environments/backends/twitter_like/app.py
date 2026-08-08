"""Twitter-like social media app for simulation.

Wraps ``TwitterLikePlatform`` (SQLite-backed engine) with the
``SocialBackendApp`` interface so it can be used as a drag-and-drop
replacement for the Mastodon app in the simulation.
"""

from __future__ import annotations

import dataclasses
import logging
import os
from collections.abc import Mapping, Sequence
from typing import Any

from silisocs.environments.backends.base import (
    ActionResult,
    SocialBackendApp,
    VisualizerSpec,
    app_action,
    record_invalid_action_target,
    record_unexpected_action_error,
)
from silisocs.environments.backends.event_semantics import social_event_semantics
from silisocs.environments.backends.sqlite_state import (
    restore_sqlite_database,
    snapshot_sqlite_database,
)
from silisocs.environments.backends.twitter_like.engine import TwitterLikePlatform

_LOGGER = logging.getLogger(__name__)

# Custom-mode verbs whose TARGET ID must be a numeric post id.
_TARGET_REQUIRED_ACTIONS = frozenset({"reply", "like", "repost", "retweet", "boost"})


def _coerce_post_id(target_id: Any) -> int | None:
    """Parse a custom-mode TARGET ID, or None when it is not a numeric id."""
    try:
        return int(str(target_id).strip())
    except (TypeError, ValueError):
        return None


@dataclasses.dataclass
class TwitterLikeApp(SocialBackendApp):
    """Twitter-like social media app.

    A microblogging platform where users post tweets, reply, like, repost,
    and follow other users.  Uses a local SQLite database as the backend.
    """

    action_logger: Any = None
    # Authoritative checkpoint state: full SQLite snapshot + user mapping.
    provides_checkpoint_state = True
    # Self-described analysis semantics (custom-mode labels the decorators can't
    # see); decorator-declared tags on new actions merge in on top.
    event_semantics = social_event_semantics(
        roots={"post", "quote_repost"},
        replies={"reply"},
        reactions={"like", "repost"},
        follows={"follow", "unfollow", "mute_user", "unmute_user"},
        negative={"dislike_post", "report_post"},
        reads={"get_own_timeline", "get_trending", "timeline_retrieval"},
        content_ids=("post_id", "tweet_id"),
        response_ids=("post_id",),
        parent_ids=("reply_to_id", "target_id"),
    )
    visualizer = VisualizerSpec(
        "TWITTER_LIKE_DB",
        "silisocs.environments.backends.twitter_like.visualizer.server",
        8002,
        app_factory=(
            "silisocs.environments.backends.twitter_like.visualizer.server:create_viewer_app"
        ),
    )
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
    # SocialBackendApp required interface
    # ------------------------------------------------------------------ #

    def name(self) -> str:
        """Return the name of the app."""
        return "TwitterLikeApp"

    def description(self) -> str:
        """Return a description of the app."""
        return self.app_description

    def initialize(self, agent_names: list[str], **kwargs: Any) -> None:
        """Compatibility no-op; runtime initializers own social setup."""
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
        """Set up users and apply the initializer-provided follow network."""
        del sim_roles, graph_config
        following = dict(following_graph or {})
        agent_bios = dict(agent_bios or {})

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

        # Apply the follow graph in ONE bulk transaction; log only applied edges
        # (mirrors the per-edge path, which logged only successful follows).
        display_by_username = {v: k for k, v in self._user_mapping.items()}
        edges = [
            (self._get_username(display_name), self._get_username(followee))
            for display_name, followees in following.items()
            for followee in followees
        ]
        for src, tgt in self._platform.add_follows(edges):
            self._log_action_event(
                display_by_username.get(src, src),
                "init_follow",
                {"target_user": display_by_username.get(tgt, tgt)},
            )

        follow_edges = sum(len(v) for v in following.values())
        self._last_initialization_stats = {
            "platform": "twitter_like",
            "num_users": len(agent_names),
            "num_follow_edges": follow_edges,
        }
        self._print(f"Initialized {len(agent_names)} users ({follow_edges} follow edges)")

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
        # A failed read PROPAGATES: swallowing it here would turn a transient DB
        # error into a legitimately-empty timeline, and get_own_timeline would log
        # a committed ``num_posts_retrieved: 0`` row for a read that never happened.
        feed = self._platform.get_feed("chronological_home", username, limit=limit)
        return feed.get("posts", [])

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

    def init_recsys(
        self,
        recsys_type: str = "twitter",
        user_context_recent_posts: int = 0,
        include_like_trace: bool = False,
        like_trace_window: int = 5,
        like_trace_weight: float = 0.0,
        include_like_trace_in_context: bool = True,
    ) -> None:
        """Initialize recommendation algorithm(s) on the underlying platform."""
        self._platform.init_recsys(
            recsys_type=recsys_type,
            user_context_recent_posts=user_context_recent_posts,
            include_like_trace=include_like_trace,
            like_trace_window=like_trace_window,
            like_trace_weight=like_trace_weight,
            include_like_trace_in_context=include_like_trace_in_context,
        )
        self._log_action_event(
            source_user="system",
            label="recsys_init",
            data={
                "recsys_type": str(recsys_type),
                "user_context_recent_posts": int(user_context_recent_posts),
                "include_like_trace": bool(include_like_trace),
                "like_trace_window": int(like_trace_window),
                "like_trace_weight": float(like_trace_weight),
                "include_like_trace_in_context": bool(include_like_trace_in_context),
            },
        )

    def recsys_active_types(self) -> set[str]:
        """Return recsys types currently live on the platform (empty after restore)."""
        return set(getattr(self._platform, "_recsys_types", {}) or {})

    def update_recommendations(
        self,
        active_user_ids: list[int] | None = None,
        max_posts: int = 10,
        active_agent_names: Sequence[str] | None = None,
    ) -> None:
        """Update recommendation rows via the underlying platform and log counts.

        ``active_agent_names`` scopes the recompute to those agents' users (their
        rows are replaced; everyone else's are left as-is) — the O(active) path
        the recommendation-update component uses. ``active_user_ids`` remains for
        callers that already hold platform ids. Neither set = full recompute.
        """
        if active_agent_names is not None and active_user_ids is None:
            active_user_ids = self._resolve_active_user_ids(active_agent_names)
            if not active_user_ids:
                # A scoped update matching no platform users refreshes nothing;
                # deliberately NOT a fall-through to a full-population recompute.
                self._log_action_event(
                    source_user="system",
                    label="recsys_update",
                    data={
                        "active_user_ids_count": 0,
                        "max_posts": int(max_posts),
                        "recommendation_rows": 0,
                        "scoped": True,
                    },
                )
                return
        # The platform reports rows written this pass — no COUNT(*) scan needed.
        # (Scoped updates replace only the active users' rows, so this is the
        # refresh size, not the live table size.)
        rec_count = int(
            self._platform.update_recommendations(
                active_user_ids=active_user_ids, max_posts=max_posts
            )
        )
        self._log_action_event(
            source_user="system",
            label="recsys_update",
            data={
                "active_user_ids_count": len(active_user_ids or []),
                "max_posts": int(max_posts),
                "recommendation_rows": rec_count,
                "scoped": active_user_ids is not None,
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
                f"\n\nUser: {post['username']}\n"
                f"Content: {post['content']}\n"
                f"Tweet ID: {post['id']}\n"
                f"Likes: {post['likes_count']}, "
                f"Reposts: {post['reposts_count']}, "
                f"Replies: {post.get('reply_count', 0)}\n"
            )
        return result

    def action_aliases(self) -> list[set[str]]:
        """Domain-verb <-> method-name synonyms (keep in sync with parse_and_resolve_action)."""
        return [
            {"post", "create_tweet"},
            {"reply", "reply_to_tweet"},
            {"like", "like_tweet"},
            {"repost", "retweet", "boost", "repost_tweet"},
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

        post_id = _coerce_post_id(target_id)
        if action_type in _TARGET_REQUIRED_ACTIONS and post_id is None:
            return record_invalid_action_target(action_type, target_id)

        try:
            if action_type in {"finished", "finish", "finish_action_episode"}:
                return self.finish_action_episode()
            if action_type in {"post", "create_tweet"}:
                return self.create_tweet(user_name, content)
            if action_type == "reply":
                return self.reply_to_tweet(user_name, content, int(post_id or 0))
            if action_type == "like":
                return self.like_tweet(user_name, int(post_id or 0))
            if action_type in ("repost", "retweet", "boost"):
                return self.repost_tweet(user_name, int(post_id or 0))
            return f"Unknown action type: {action_type}"
        except ValueError as e:
            # The platform's language for a legitimate rejection (unknown user,
            # missing post): the agent sees the message, nothing is counted.
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

    def get_state(self) -> dict[str, Any]:
        """Return serializable backend state for checkpoints."""
        return {
            "db_snapshot_b64": snapshot_sqlite_database(self.db_path),
            "user_mapping": dict(self._user_mapping),
            "last_initialization_stats": dict(getattr(self, "_last_initialization_stats", {})),
            "committed_events": self._committed_events_state(),
        }

    def set_state(self, state: dict[str, Any]) -> None:
        """Restore backend state from a checkpoint payload."""
        if not isinstance(state, Mapping):
            raise TypeError("TwitterLikeApp checkpoint state must be a mapping.")
        snapshot = state.get("db_snapshot_b64")
        if not isinstance(snapshot, str) or not snapshot:
            raise ValueError("TwitterLikeApp checkpoint state requires db_snapshot_b64.")
        self._close_platform_for_restore()
        restore_sqlite_database(self.db_path, snapshot)
        self._platform = TwitterLikePlatform(self.db_path, use_queue=True)
        user_mapping = state.get("user_mapping", {})
        if not isinstance(user_mapping, Mapping):
            raise TypeError("TwitterLikeApp checkpoint user_mapping must be a mapping.")
        self._user_mapping = {str(k): str(v) for k, v in user_mapping.items()}
        stats = state.get("last_initialization_stats", {})
        if isinstance(stats, Mapping):
            self._last_initialization_stats = dict(stats)
        self._restore_committed_events(state.get("committed_events"))

    def _close_platform_for_restore(self) -> None:
        # Best-effort: the DB file is replaced right after this, so a failed
        # shutdown must not abort the restore — but it must be visible, because
        # a connection left open is exactly what corrupts the replacement.
        try:
            self._platform.shutdown()
        except Exception:
            _LOGGER.warning(
                "Failed to shut down the twitter_like platform before checkpoint restore "
                "of %s; open connections may interfere with the database replacement.",
                self.db_path,
                exc_info=True,
            )

    # ------------------------------------------------------------------ #
    # @app_action methods
    # ------------------------------------------------------------------ #

    @app_action
    def create_tweet(self, agent_name: str, status: str) -> str:
        """Post a new tweet to the timeline.

        Args:
            agent_name: The display name of the character you are currently simulating.
            status: The text content of the tweet (max 280 characters).
        """
        actor_display_name = str(agent_name)
        username = self._get_username(agent_name)
        post_id = self._platform.create_post(username, status)
        result_msg = f'{actor_display_name} posted a tweet (ID: {post_id}): "{status}"'
        self._print(result_msg, emoji="📝")
        self._log_action_event(
            source_user=actor_display_name,
            label="post",
            data={"post_id": str(post_id), "post_text": status},
        )
        return result_msg

    @app_action
    def reply_to_tweet(self, agent_name: str, status: str, post_id: int) -> str:
        """Reply to an existing tweet.

        Args:
            agent_name: The full display name of the user replying.
            status: The text content of the reply.
            post_id: The ID of the tweet being replied to.
        """
        actor_display_name = str(agent_name)
        username = self._get_username(agent_name)
        reply_id = self._platform.create_post(username, status, reply_to_id=post_id)
        result_msg = f'{actor_display_name} replied to tweet {post_id}: "{status}"'
        self._print(result_msg, emoji="💬")
        self._log_action_event(
            source_user=actor_display_name,
            label="reply",
            data={
                "post_id": str(reply_id),
                "reply_to_id": str(post_id),
                "post_text": status,
            },
        )
        return result_msg

    @app_action
    def like_tweet(self, agent_name: str, post_id: int) -> str | ActionResult:
        """Like (favorite) a tweet.

        Args:
            agent_name: The full display name of the user liking the tweet.
            post_id: The ID of the tweet to like.
        """
        actor_display_name = str(agent_name)
        username = self._get_username(agent_name)
        try:
            result = self._platform.like(username, post_id)
            committed = result is not False
            if committed:
                like_msg = f"{actor_display_name} liked tweet {post_id}."
            else:
                like_msg = f"{actor_display_name} has already liked tweet {post_id}."
        except ValueError as e:
            committed = False
            like_msg = f"Error liking tweet {post_id}: {e}"
        self._print(like_msg, emoji="❤️")
        # Committed state changes only: failures and already-liked no-ops leave no
        # row in the canonical action log (replay/eval consume it as ground truth).
        if not committed:
            return ActionResult(like_msg, committed=False)
        self._log_action_event(
            source_user=actor_display_name,
            label="like",
            data={"post_id": str(post_id)},
        )
        return like_msg

    @app_action
    def repost_tweet(self, agent_name: str, post_id: int) -> str | ActionResult:
        """Repost (retweet) an existing tweet to share it with your followers.

        Args:
            agent_name: The full display name of the user reposting.
            post_id: The ID of the tweet to repost.
        """
        actor_display_name = str(agent_name)
        username = self._get_username(agent_name)
        try:
            repost_id = self._platform.repost(username, post_id)
            repost_msg = f"{actor_display_name} reposted tweet {post_id} (new ID: {repost_id})."
            committed = True
        except ValueError as e:
            repost_msg = f"Error reposting tweet {post_id}: {e}"
            committed = False
        self._print(repost_msg, emoji="🔁")
        if not committed:
            return ActionResult(repost_msg, committed=False)
        self._log_action_event(
            source_user=actor_display_name,
            label="repost",
            data={"post_id": str(post_id)},
        )
        return repost_msg

    @app_action
    def follow_user(self, agent_name: str, target_user: str) -> str | ActionResult:
        """Follow another user to see their tweets in your timeline.

        Args:
            agent_name: The full display name of the user who wants to follow.
            target_user: The full display name of the user to follow.
        """
        actor_display_name = str(agent_name)
        target_user_full = str(target_user)
        src_username = self._get_username(agent_name)
        tgt_username = self._get_username(target_user)
        try:
            # Returns False for a self-follow or an already-following no-op.
            committed = self._platform.follow(src_username, tgt_username) is not False
            follow_msg = (
                f"{actor_display_name} followed {target_user_full}."
                if committed
                else f"{actor_display_name} already follows {target_user_full}."
            )
        except ValueError as e:
            follow_msg = f"Error following {target_user_full}: {e}"
            committed = False
        self._print(follow_msg, emoji="➕")
        if not committed:
            return ActionResult(follow_msg, committed=False)
        self._log_action_event(
            source_user=actor_display_name,
            label="follow",
            data={"target_user": target_user_full},
        )
        return follow_msg

    @app_action
    def unfollow_user(self, agent_name: str, target_user: str) -> str | ActionResult:
        """Unfollow a user to stop seeing their tweets.

        Args:
            agent_name: The full display name of the user who wants to unfollow.
            target_user: The full display name of the user to unfollow.
        """
        actor_display_name = str(agent_name)
        target_user_full = str(target_user)
        src_username = self._get_username(agent_name)
        tgt_username = self._get_username(target_user)
        try:
            self._platform.unfollow(src_username, tgt_username)
            msg = f"{actor_display_name} unfollowed {target_user_full}."
            committed = True
        except ValueError as e:
            msg = f"Error unfollowing {target_user_full}: {e}"
            committed = False
        self._print(msg, emoji="➖")
        if not committed:
            return ActionResult(msg, committed=False)
        self._log_action_event(
            source_user=actor_display_name,
            label="unfollow",
            data={"target_user": target_user_full},
        )
        return msg

    @app_action
    def get_own_timeline(self, agent_name: str, limit: int) -> str:
        """Read your home timeline showing tweets from users you follow.

        Args:
            agent_name: The full display name of the user reading the timeline.
            limit: Maximum number of tweets to retrieve.
        """
        actor_display_name = str(agent_name)
        timeline = self.get_timeline(agent_name, limit)
        str_timeline = self.format_timeline_for_observation(timeline)
        self._print(f"Retrieved {len(timeline)} tweets for {actor_display_name}", emoji="📊")
        self._log_action_event(
            source_user=actor_display_name,
            label="get_own_timeline",
            data={"num_posts_retrieved": len(timeline)},
        )
        return f"Twitter Timeline for {actor_display_name}:\n{str_timeline}"

    @app_action
    def update_profile(self, agent_name: str, bio: str) -> str | ActionResult:
        """Update your profile bio.

        Args:
            agent_name: The full display name of the user updating their profile.
            bio: The new bio text for the profile.
        """
        actor_display_name = str(agent_name)
        username = self._get_username(agent_name)
        # TwitterLikePlatform doesn't have update_profile, use raw SQL. A DB error
        # propagates to the counted invoke boundary; only a genuinely unmatched
        # username is an (uncommitted) rejection.
        with self._platform.get_connection() as conn:
            cursor = conn.execute("UPDATE users SET bio = ? WHERE username = ?", (bio, username))
            conn.commit()
        if not cursor.rowcount:
            msg = f"Profile not updated: no user matching {actor_display_name}."
            self._print(msg, emoji="✏️")
            return ActionResult(msg, committed=False)
        msg = f'Profile updated for {actor_display_name}: "{bio}"'
        self._print(msg, emoji="✏️")
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
                    f"  Posts: {profile.get('post_count', 0)}\n"
                    f"  Followers: {profile.get('followers_count', 0)}\n"
                    f"  Following: {profile.get('following_count', 0)}\n"
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
    def unlike_tweet(self, agent_name: str, post_id: int) -> str | ActionResult:
        """Remove your like from a tweet.

        Args:
            agent_name: The full display name of the user unliking.
            post_id: The ID of the tweet to unlike.
        """
        actor_display_name = str(agent_name)
        username = self._get_username(agent_name)
        try:
            # Returns False for a never-liked post (a no-op that changed nothing).
            committed = self._platform.unlike(username, post_id) is not False
            msg = (
                f"{actor_display_name} unliked tweet {post_id}."
                if committed
                else f"{actor_display_name} had not liked tweet {post_id}."
            )
        except ValueError as e:
            msg = f"Error unliking tweet {post_id}: {e}"
            committed = False
        self._print(msg, emoji="💔")
        if not committed:
            return ActionResult(msg, committed=False)
        self._log_action_event(
            source_user=actor_display_name,
            label="unlike",
            data={"post_id": str(post_id)},
        )
        return msg

    @app_action
    def quote_repost_tweet(self, agent_name: str, post_id: int, status: str) -> str | ActionResult:
        """Quote-repost a tweet, adding your own commentary.

        Args:
            agent_name: The full display name of the user quote-reposting.
            post_id: The ID of the tweet to quote.
            status: Your commentary text to add above the quoted tweet.
        """
        actor_display_name = str(agent_name)
        username = self._get_username(agent_name)
        try:
            new_id = self._platform.quote_repost(username, post_id, status)
            msg = f'{actor_display_name} quote-reposted tweet {post_id} (new ID: {new_id}): "{status}"'
            committed = True
        except ValueError as e:
            msg = f"Error quote-reposting tweet {post_id}: {e}"
            committed = False
        self._print(msg, emoji="🔁💬")
        if not committed:
            return ActionResult(msg, committed=False)
        self._log_action_event(
            source_user=actor_display_name,
            label="quote_repost",
            data={"post_id": str(post_id), "content": status},
        )
        return msg

    @app_action
    def search_posts(self, agent_name: str, query: str, limit: int = 20) -> str | ActionResult:
        """Search for tweets containing specific text.

        Args:
            agent_name: The full display name of the user searching.
            query: The search text to look for in tweet content.
            limit: Maximum number of results to return.
        """
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
        except ValueError as e:
            msg = f"Error searching posts: {e}"
            self._print(msg, emoji="🔍")
            return ActionResult(msg, committed=False)
        self._print(msg, emoji="🔍")
        return msg

    # ================================================================ #
    # Extended social actions
    # ================================================================ #

    @app_action
    def unlike_post(self, agent_name: str, post_id: int) -> str | ActionResult:
        """Remove a like from a previously liked post.

        Args:
            agent_name: The full display name of the user removing the like.
            post_id: The ID of the post to unlike.
        """
        actor_display_name = str(agent_name)
        username = self._get_username(agent_name)
        result = self._platform.unlike_post(username, post_id)
        msg = f"{actor_display_name} {'unliked' if result else 'could not unlike'} post {post_id}."
        self._print(msg, emoji="🚫❤️")
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
        """Dislike (downvote) a post.

        Args:
            agent_name: The full display name of the user disliking the post.
            post_id: The ID of the post to dislike.
        """
        actor_display_name = str(agent_name)
        username = self._get_username(agent_name)
        result = self._platform.dislike_post(username, post_id)
        msg = (
            f"{actor_display_name} {'disliked' if result else 'could not dislike'} post {post_id}."
        )
        self._print(msg, emoji="👎")
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
        """Remove a dislike from a post.

        Args:
            agent_name: The full display name of the user removing the dislike.
            post_id: The ID of the post to undo dislike for.
        """
        actor_display_name = str(agent_name)
        username = self._get_username(agent_name)
        result = self._platform.undo_dislike_post(username, post_id)
        msg = f"{actor_display_name} {'removed dislike from' if result else 'could not remove dislike from'} post {post_id}."
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
        """Mute another user to hide their posts from your timeline.

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
        """Unmute a previously muted user.

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
        """Report a post for violation of community guidelines.

        Args:
            agent_name: The full display name of the user reporting.
            post_id: The ID of the post to report.
            reason: The reason for reporting (default: Inappropriate content).
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
                        f"  ID:{post['id']} | @{post['username']}: "
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
