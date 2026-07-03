"""Mastodon social network app for simulation.

This module provides ``SocialNetworkApp``, the Mastodon implementation of
the ``SocialBackendApp`` interface.  It wraps the Mastodon API via
``mastodon_ops`` and exposes ``@app_action`` decorated methods for the
simulation game master.

The generic ``BackendApp`` base class, ``SocialBackendApp`` ABC, and supporting
utilities (``app_action``, ``Parameter``, ``ActionDescriptor``, etc.) come
from ``silisocs.environments.backends.base``.
"""

import dataclasses
import json
import logging
import os
import re
from collections.abc import Mapping
from html import unescape
from typing import Any

# Re-export COLOR_TYPE and action descriptors for downstream backend modules.
from silisocs.environments.backends.base import (  # noqa: F401
    COLOR_TYPE,
    RUNTIME_AGENT_PARAM,
    ActionArgumentError,
    ActionDescriptor,
    BackendApp,
    Parameter,
    SocialBackendApp,
    app_action,
)
from silisocs.runtime.types import ActionOutput, ToolCall

_LOGGER = logging.getLogger(__name__)

_MASTODON_EXTRA_INSTALL_HINT = (
    "Mastodon live operations require the optional Mastodon dependencies. "
    "Install them with `pip install 'silisocs[mastodon]'` or "
    "`uv sync --extra mastodon` before setting "
    "`env.gm.backend.params.perform_operations=true`."
)


def _load_mastodon_ops() -> Any:
    """Import the optional Mastodon operations module only for live mode."""
    try:
        from silisocs.environments.backends.mastodon import mastodon_ops
    except ModuleNotFoundError as exc:
        if exc.name in {"loguru", "mastodon"} or str(exc.name).startswith("mastodon."):
            raise RuntimeError(_MASTODON_EXTRA_INSTALL_HINT) from exc
        raise
    return mastodon_ops


def _display_name_key(display_name: str) -> str:
    """Return the historical Mastodon account lookup key for a display name."""
    parts = str(display_name).strip().split()
    if len(parts) >= 2:
        return f"{parts[0]}{parts[1]}"
    if parts:
        return parts[0]
    return ""


def _display_name_aliases(display_name: str) -> set[str]:
    """Return all display-name aliases accepted by the Mastodon backend."""
    raw_name = str(display_name).strip()
    parts = raw_name.split()
    aliases = {raw_name, _display_name_key(raw_name)}
    if parts:
        aliases.add(parts[0])
    return {alias for alias in aliases if alias}


# region[Mastodon Social Network App]


@dataclasses.dataclass
class SocialNetworkApp(SocialBackendApp):
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
    # Mastodon's authoritative state lives on a live server a checkpoint cannot
    # snapshot, so its checkpoint "state" IS its replayable action history:
    # ``get_state`` embeds the logged actions and ``set_state`` replays them to
    # rebuild the server, via the normal set_state path like every other backend.
    # On resume the server is reset and posts/follows replay directly; like/boost/
    # reply targets are remapped from the old toot id to the new one. Replay yields
    # a *similar*, not identical, server state.
    provides_checkpoint_state = True
    # Mastodon restores via set_state (above): its embedded action history is
    # replayed through its own ``_replay_event_to_action`` mapping, which is a
    # private detail of that snapshot restore, not a shared backend seam.
    perform_operations: bool = False
    reset_server_on_setup: bool = False
    app_description: str = "MastodonSocialNetworkApp"
    _log_color: COLOR_TYPE = dataclasses.field(default="blue", init=False)
    _mastodon_ops: Any = dataclasses.field(default=None, init=False)
    _user_mapping: dict[str, str] = dataclasses.field(default_factory=dict, init=False)
    # Checkpoint-replay state: maps a pre-resume (logged) toot id to the new id
    # the server assigns when the post is re-created during replay.
    _replay_id_map: dict[str, str] = dataclasses.field(default_factory=dict, init=False)
    _pending_replay_old_id: str | None = dataclasses.field(default=None, init=False)
    # Incremental action-log read cache: ``get_state`` runs every checkpoint, so
    # re-reading all of ``action_events.jsonl`` each time is O(n^2) over a run. We
    # keep the parsed events and consumed byte offset and read only newly appended
    # bytes; ``_action_log_path`` guards against the logger switching files.
    _cached_action_events: list[dict[str, Any]] = dataclasses.field(
        default_factory=list, init=False
    )
    _action_log_offset: int = dataclasses.field(default=0, init=False)
    _action_log_path: str | None = dataclasses.field(default=None, init=False)

    def __post_init__(self) -> None:  # noqa: D105
        super().__init__()
        if self.perform_operations:
            self._mastodon_ops = _load_mastodon_ops()

    def begin_action_replay(self) -> None:
        """Reset per-replay state before replaying logged events.

        The live server itself is wiped via ``reset_server_on_setup`` during the
        resume's game-master initialization; here we only clear the old->new toot
        id remap. Warn loudly if the server was not configured to reset, since
        replaying onto a non-empty server duplicates content.
        """
        self._replay_id_map.clear()
        self._pending_replay_old_id = None
        if self.perform_operations and not self.reset_server_on_setup:
            _LOGGER.warning(
                "Mastodon action replay without reset_server_on_setup=true will duplicate "
                "content on the live server (the original run's toots/follows are still there)."
            )

    def get_state(self) -> dict[str, Any]:
        """Embed this run's replayable action history so ``set_state`` can rebuild it.

        Mastodon cannot snapshot the live server, so its checkpoint state is the
        list of actions it performed; they are read back from its own action log.
        """
        state = super().get_state()
        events = self._read_logged_action_events()
        if events:
            state["replay_events"] = events
        return state

    def set_state(self, state: dict[str, Any]) -> None:
        """Rebuild server state by re-running the checkpointed action events.

        The live server is wiped during resume init (``reset_server_on_setup``);
        here each logged action is remapped (old->new toot ids) and re-executed as
        its original user, in chronological order so reply/like/boost targets exist
        before they are referenced.
        """
        events = state.get("replay_events") if isinstance(state, Mapping) else None
        if not events:
            return
        self.begin_action_replay()
        for event in events:
            if isinstance(event, Mapping):
                self._replay_logged_action(event)

    def _read_logged_action_events(self) -> list[dict[str, Any]]:
        """Read this backend's own action log into compact replay events.

        Incremental: only the bytes appended since the previous call are parsed,
        and parsed events accumulate in ``_cached_action_events``. The full history
        up to now is always returned. The read falls back to a complete re-parse if
        the log path changed, the file is missing, or it was truncated/rotated
        (current size smaller than the offset already consumed).
        """
        path = str(getattr(self.action_logger, "output_filename", "") or "")
        if not path or not os.path.isfile(path):
            # No usable log (unset path, or the file is missing/rotated away): forget
            # any cached state so a re-created log at this path is read from the start
            # on a later call, and report an empty history (matches the original
            # missing-file behavior).
            self._reset_action_log_cache(path or None)
            return []

        try:
            current_size = os.path.getsize(path)
        except OSError:
            return list(self._cached_action_events)

        # A new path or a shrunken file (truncation/rotation in place) invalidates
        # the byte offset, so re-read from the beginning.
        if path != self._action_log_path or current_size < self._action_log_offset:
            self._reset_action_log_cache(path)

        if current_size <= self._action_log_offset:
            return list(self._cached_action_events)

        # Read appended bytes and only consume up to the final newline, so a
        # half-written trailing record is left for the next call (the offset never
        # advances past an incomplete line).
        with open(path, "rb") as handle:
            handle.seek(self._action_log_offset)
            chunk = handle.read(current_size - self._action_log_offset)
        last_newline = chunk.rfind(b"\n")
        if last_newline == -1:
            return list(self._cached_action_events)
        complete = chunk[: last_newline + 1]
        for raw_line in complete.split(b"\n"):
            stripped = raw_line.decode("utf-8", errors="replace").strip()
            if stripped:
                self._append_parsed_action_event(stripped)
        self._action_log_offset += len(complete)
        return list(self._cached_action_events)

    def _reset_action_log_cache(self, path: str | None) -> None:
        """Forget cached events/offset and rebind to ``path`` for a full re-read."""
        self._cached_action_events.clear()
        self._action_log_offset = 0
        self._action_log_path = path

    def _append_parsed_action_event(self, stripped_line: str) -> None:
        """Parse one non-empty JSONL line and cache it if it is an action event."""
        try:
            row = json.loads(stripped_line)
        except json.JSONDecodeError:
            return
        if not isinstance(row, dict) or str(row.get("event_type", "")) != "action":
            return
        self._cached_action_events.append(
            {
                "label": str(row.get("label", "")),
                "source_user": str(row.get("source_user", "")),
                "data": row.get("data") or {},
            }
        )

    def _replay_logged_action(self, event: Mapping[str, Any]) -> None:
        """Remap and re-execute one logged action as its original user."""
        action = self._replay_event_to_action(str(event.get("label", "")), event.get("data") or {})
        if action is None:
            return
        agent = str(event.get("source_user", "") or "")
        lookup = self._action_lookup()
        for tool_call in action.tool_calls:
            payload = dict(tool_call.arguments)
            descriptor = lookup.get(tool_call.name)
            if descriptor is not None and any(
                param.name == RUNTIME_AGENT_PARAM for param in descriptor.parameters
            ):
                payload[RUNTIME_AGENT_PARAM] = agent
            self.invoke_action_with_kwargs(tool_call.name, payload)

    def _replay_event_to_action(self, label: str, data: Mapping[str, Any]) -> ActionOutput | None:
        """Map a logged Mastodon action event to a replayable action.

        Private to Mastodon's snapshot restore (``set_state`` replays its embedded
        action history through this). Unlike the stateless microblog mapper, it is
        instance-stateful: it remaps old->new toot ids for references.

        Creates (``post``) and follow/unfollow replay directly. ``like``/
        ``boost``/``reply`` reference a server-assigned toot id that changes when
        the post is re-created, so they are remapped through ``_replay_id_map``
        (populated by ``post_toot`` during replay); an unmapped reference is
        skipped. Read-only and bookkeeping labels return ``None``.
        """
        if label in {"post", "post_status"}:
            text = data.get("post_text") or data.get("status") or data.get("text")
            if not text:
                raise ValueError(f"Mastodon post event missing text: {data}")
            old_id = data.get("toot_id") or data.get("post_id")
            self._pending_replay_old_id = str(old_id) if old_id not in (None, "", "None") else None
            return ActionOutput.from_tool_calls([ToolCall("post_toot", {"status": str(text)})])
        if label in {"follow", "unfollow"}:
            target = data.get("target_user") or data.get("target") or data.get("user")
            if not target:
                raise ValueError(f"Mastodon {label} event missing target user: {data}")
            return ActionOutput.from_tool_calls(
                [ToolCall(f"{label}_user", {"target_user": str(target)})]
            )
        if label == "update_profile":
            bio = data.get("new_bio") or data.get("bio") or ""
            return ActionOutput.from_tool_calls([ToolCall("update_profile", {"bio": str(bio)})])
        if label in {"like", "like_toot"}:
            new_id = self._remapped_toot_id(data)
            return (
                None
                if new_id is None
                else ActionOutput.from_tool_calls([ToolCall("like_toot", {"toot_id": new_id})])
            )
        if label in {"boost", "boost_toot", "repost"}:
            new_id = self._remapped_toot_id(data)
            return (
                None
                if new_id is None
                else ActionOutput.from_tool_calls([ToolCall("boost_toot", {"toot_id": new_id})])
            )
        if label in {"reply", "reply_to_toot"}:
            text = data.get("post_text") or data.get("status") or data.get("text")
            # The parent being answered lives under reply_to.toot_id; data.toot_id
            # is the reply's *own* id (captured below for downstream references).
            reply_to = data.get("reply_to")
            parent_old = (
                reply_to.get("toot_id")
                if isinstance(reply_to, Mapping)
                else (data.get("in_reply_to_id") or data.get("reply_to_id"))
            )
            parent_new = self._lookup_new_id(parent_old)
            if parent_new is None or not text:
                return None
            own_old = data.get("toot_id")
            self._pending_replay_old_id = (
                str(own_old) if own_old not in (None, "", "None") else None
            )
            return ActionOutput.from_tool_calls(
                [ToolCall("reply_to_toot", {"in_reply_to_id": parent_new, "status": str(text)})]
            )
        # Read-only / bookkeeping labels are not replayable.
        return None

    def _remapped_toot_id(self, data: Mapping[str, Any]) -> str | None:
        """Return the new server id for a logged (old) toot id, or None to skip."""
        return self._lookup_new_id(
            data.get("toot_id") or data.get("post_id") or data.get("target_id")
        )

    def _lookup_new_id(self, old_id: Any) -> str | None:
        """Translate a logged (pre-resume) toot id to its new replay id, or None."""
        if old_id in (None, "", "None"):
            return None
        new_id = self._replay_id_map.get(str(old_id))
        if new_id is None:
            _LOGGER.warning(
                "Mastodon replay: toot id %s referenced before it was re-created; skipping.",
                old_id,
            )
        return new_id

    def _record_replayed_post_id(self, new_toot_id: str | None) -> None:
        """Map the pending old toot id to the new server id after a replayed post."""
        if self._pending_replay_old_id is not None and new_toot_id not in (None, "", "None"):
            self._replay_id_map[self._pending_replay_old_id] = str(new_toot_id)
        self._pending_replay_old_id = None

    def name(self) -> str:
        """Define the name of the app."""
        return "MastodonSocialNetworkApp"

    def description(self) -> str:
        """Define the description of the app."""
        return self.app_description

    def _log_action_event(self, *args: Any, **kwargs: Any) -> None:
        """Write an action event when the runtime provided an action logger."""
        if len(args) == 1 and isinstance(args[0], dict) and not kwargs:
            event = args[0]
        elif len(args) >= 3:
            event = {
                "source_user": args[0],
                "label": args[1],
                "data": args[2],
            }
        else:
            event = dict(kwargs)
        if self.action_logger is not None and hasattr(self.action_logger, "log"):
            self.action_logger.log(event)

    def _require_mastodon_ops(self) -> Any:
        """Return the live Mastodon operations module, importing it if needed."""
        if self._mastodon_ops is None:
            self._mastodon_ops = _load_mastodon_ops()
        return self._mastodon_ops

    # ------------------------------------------------------------------ #
    # SocialBackendApp interface
    # ------------------------------------------------------------------ #

    def initialize(self, agent_names: list[str], **kwargs: Any) -> None:
        """Compatibility no-op; runtime initializers own Mastodon setup."""
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
        """Set up Mastodon user mapping, bios, and initializer-provided follows."""
        del sim_roles, graph_config
        following = dict(following_graph or {})
        agent_bios = dict(agent_bios or {})

        # Build user mapping.
        user_mapping = {}
        for i, display_name in enumerate(agent_names):
            username = f"user{i + 1:04d}"
            for alias in _display_name_aliases(display_name):
                user_mapping[alias] = username
        self.set_user_mapping(user_mapping)

        # Set bios/profiles.
        for display_name, bio in agent_bios.items():
            if bio:
                try:
                    self.update_profile(display_name, bio)
                except Exception as e:
                    self._print(f"Error setting bio for {display_name}: {e}", color="red")

        for display_name, followees in following.items():
            for followee in followees:
                try:
                    self.follow_user(display_name, followee)
                except Exception as e:
                    self._print(f"Follow error ({display_name}->{followee}): {e}", color="red")

        follow_edges = sum(len(v) for v in following.values())
        self._last_initialization_stats = {
            "platform": "mastodon",
            "num_users": len(agent_names),
            "num_follow_edges": follow_edges,
        }
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
            agent_name = _display_name_key(user_name)
            username = self._get_username(agent_name)
            if self.perform_operations:
                timeline = self._require_mastodon_ops().get_own_timeline(username, limit=limit)
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

    def action_aliases(self) -> list[set[str]]:
        """Domain-verb <-> method-name synonyms (keep in sync with parse_and_resolve_action)."""
        return [
            {"post", "post_toot"},
            {"reply", "reply_to_toot"},
            {"like", "like_toot"},
            {"boost", "repost", "boost_toot"},
        ]

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
        expanded_mapping = {}
        for display_name, username in mapping.items():
            for alias in _display_name_aliases(display_name):
                expanded_mapping[alias] = username
        self._user_mapping = expanded_mapping
        self._print(f"Updated user mapping with {len(mapping)} entries", emoji="🔄")
        if self.perform_operations and self.reset_server_on_setup:
            mastodon_ops = self._require_mastodon_ops()
            mastodon_ops.check_env()
            mastodon_ops.clear_mastodon_server(len(set(self._user_mapping.values())) + 1)

    def get_user_mapping(self) -> dict[str, str]:
        """Get the mapping of display names to usernames."""
        return self._user_mapping

    def _get_username(self, display_name: str) -> str:
        """Get the username for a given display name."""
        username = None
        for alias in _display_name_aliases(display_name):
            username = self._user_mapping.get(alias)
            if username:
                break
        # self._print(f"Mapped {display_name} to @{username}", emoji="🔗")
        if not username:
            raise ValueError(f"No username found for display name: {display_name}")
        return username

    def public_get_username(self, display_name: str) -> str:
        """Public interface to get the username."""
        return self._get_username(display_name)

    @app_action
    def update_profile(self, agent_name: str, bio: str) -> str:
        """Update the user's bio."""
        actor_display_name = str(agent_name)
        agent_name = _display_name_key(agent_name)

        username = self._get_username(agent_name)
        self._print(f"Updating profile for @{username}: {agent_name}", emoji="✏️")
        if self.perform_operations:
            self._require_mastodon_ops().update_bio(username, agent_name, bio)
        else:
            self._print(
                "Skipping real Mastodon API call since perform_operations is set to False",
                color="light_grey",
            )
        bio_message = f'Profile updated successfully: "{bio}"'
        self._print(bio_message, emoji="✅")
        self._log_action_event(
            {"source_user": actor_display_name, "label": "update_profile", "data": {"new_bio": bio}}
        )

        return bio_message

    @app_action
    def read_profile(self, agent_name: str, target_user: str) -> tuple[str, str]:
        """Read a user's profile on Mastodon social network."""
        actor_display_name = str(agent_name)
        agent_name = _display_name_key(agent_name)
        target_user_full = str(target_user)
        target_user = _display_name_key(target_user)

        current_username = self._get_username(agent_name)
        target_username = self._get_username(target_user)
        self._print(f"@{current_username} reading profile of @{target_username}", emoji="👀")
        if self.perform_operations:
            try:
                display_name, bio = self._require_mastodon_ops().read_bio(
                    current_username, target_username
                )
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
                "source_user": actor_display_name,
                "label": "read_profile",
                "data": {"target_user": target_user_full, "bio": bio},
            }
        )
        return display_name, bio

    @app_action
    def follow_user(self, agent_name: str, target_user: str) -> str:
        """Follow a user on Mastodon social network."""
        actor_display_name = str(agent_name)
        agent_name = _display_name_key(agent_name)
        target_user_full = str(target_user)
        target_user = _display_name_key(target_user)
        current_username = self._get_username(agent_name)
        target_username = self._get_username(target_user)
        if self.perform_operations:
            self._require_mastodon_ops().follow(current_username, target_username)
        else:
            self._print(
                "Skipping real Mastodon API call since perform_operations is set to False",
                color="light_grey",
            )
        follow_message = f"{actor_display_name} (@{current_username}) followed {target_user_full} (@{target_username})"
        self._print(follow_message, emoji="➕")  # noqa: RUF001
        self._log_action_event(
            {
                "source_user": actor_display_name,
                "label": "follow",
                "data": {"target_user": target_user_full},
            }
        )
        return follow_message

    @app_action
    def unfollow_user(self, agent_name: str, target_user: str) -> str:
        """Unfollow a user."""
        actor_display_name = str(agent_name)
        agent_name = _display_name_key(agent_name)
        target_user_full = str(target_user)
        target_user = _display_name_key(target_user)
        current_username = self._get_username(agent_name)
        target_username = self._get_username(target_user)
        self._print(
            f"@{current_username} unfollowing user: @{target_username}",
            emoji="➖",  # noqa: RUF001
        )
        if self.perform_operations:
            self._require_mastodon_ops().unfollow(current_username, target_username)
        else:
            self._print(
                "Skipping real Mastodon API call since perform_operations is set to False",
                color="light_grey",
            )
        unfollow_message = f"{actor_display_name} (@{current_username}) unfollowed {target_user_full} (@{target_username})"
        self._print(unfollow_message, emoji="✅")
        self._log_action_event(
            {
                "source_user": actor_display_name,
                "label": "unfollow",
                "data": {"target_user": target_user_full},
            }
        )
        return unfollow_message

    @app_action
    def post_status(
        self,
        agent_name: str,
        status: str,
        visibility: str | None = None,
        sensitive: bool = False,
        spoiler_text: str | None = None,
        in_reply_to_id: int | None = None,
        media_files: list[str] | None = None,
    ) -> str:
        """Post a status with visibility, content-warning, reply, and media controls.

        Args:
            agent_name (str): The display name of the user posting the status.
            status (str): The text content of the status update.
            visibility (str | None): Visibility level: 'public', 'unlisted', 'private', or 'direct'.
            sensitive (bool): Whether the post should be marked as sensitive content.
            spoiler_text (str | None): Content-warning text shown before the status.
            in_reply_to_id (int | None): The ``toot_id`` of the status this post replies to.
            media_files (list[str] | None): Paths to media files to attach to the post.

        Raises
        ------
            ValueError: If the input parameters are invalid.
            Exception: For any other unexpected errors during posting.
        """
        return_val = None
        actor_display_name = str(agent_name)
        try:
            agent_name = _display_name_key(agent_name)
            username = self._get_username(agent_name)
            if self.perform_operations:
                return_val = self._require_mastodon_ops().post_status(
                    login_user=username,
                    status=status,
                    visibility=visibility,
                    sensitive=sensitive,
                    spoiler_text=spoiler_text,
                    in_reply_to_id=in_reply_to_id,
                    media_files=media_files,
                )
            else:
                self._print(
                    "Skipping real Mastodon API call since perform_operations is set to False",
                    color="light_grey",
                )

            self._print(
                f'Status posted for user: {agent_name} ({username}): "{status}"',
                emoji="📝",
            )
            if media_files:
                self._print(f"Attached {len(media_files)} media file(s).", emoji="📎")

        except ValueError as e:
            self._print(f"Invalid input: {e!s}", emoji="❌")
            raise
        except Exception as e:
            self._print(f"An unexpected error occurred: {e!s}", emoji="❌")
            raise

        toot_id = None
        if return_val:
            return_msg = (
                f"{agent_name} posted a status with Toot ID: {return_val['id']} --- {status}\n"
            )
            toot_id = return_val["id"]
        else:
            return_msg = f'{agent_name} posted a status!: "{status}"\n'
        self._log_action_event(
            {
                "source_user": actor_display_name,
                "label": "post_status",
                "data": {
                    "toot_id": str(toot_id),
                    "post_text": status,
                    "visibility": visibility,
                },
            }
        )
        return return_msg

    @app_action
    def post_toot(
        self,
        agent_name: str,
        status: str,
        media_links: list[str] | None = None,
    ) -> str:
        """Post a new toot to the Mastodon-like social network.

        Args:
            agent_name (str): The username of the user posting the status.
            status (str): The text content of the status update.

        Raises
        ------
            ValueError: If the input parameters are invalid.
            Exception: For any other unexpected errors during posting.
        """
        return_val = None
        actor_display_name = str(agent_name)
        try:
            agent_name = _display_name_key(agent_name)
            username = self._get_username(agent_name)
            if self.perform_operations:
                return_val = self._require_mastodon_ops().post_status(
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
                f'Status posted for user: {agent_name} ({username}): "{status}"',
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
                f"{agent_name} posted a toot with Toot ID: {return_val['id']} --- {status}\n"
            )
            toot_id = return_val["id"]
        else:
            return_msg = f'{agent_name} posted a toot!: "{status}"\n'
        self._log_action_event(
            {
                "source_user": actor_display_name,
                "label": "post",
                "data": {"toot_id": str(toot_id), "post_text": status},
            }
        )
        # During checkpoint replay, map the pre-resume toot id to this new server id
        # so subsequent like/boost/reply events can be retargeted (no-op otherwise).
        self._record_replayed_post_id(toot_id)
        return return_msg

    # ``post_media_toot`` was removed: ``post_toot`` above supersedes it (media via
    # its ``media_links`` argument), and the old version never forwarded media anyway.

    @app_action
    def reply_to_toot(
        self,
        agent_name: str,
        status: str,
        in_reply_to_id: int,
    ) -> str:
        """Post a new status update to the Mastodon-like social network.

        Args:
            agent_name (str): The username of the user posting the status.
            status (str): The text content of the status update.
            in_reply_to_id (int): The `toot_id` of the status this post is replying to.

        Raises
        ------
            ValueError: If the input parameters are invalid.
            Exception: For any other unexpected errors during posting.
        """
        return_val = None
        try:
            actor_display_name = str(agent_name)
            agent_name = _display_name_key(agent_name)
            username = self._get_username(agent_name)
            if self.perform_operations:
                return_val = self._require_mastodon_ops().post_status(
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
            return_msg = f"{agent_name} replied to a toot with toot id {in_reply_to_id} : {status}"
            self._log_action_event(
                {
                    "source_user": actor_display_name,
                    "label": "reply",
                    "data": {
                        "reply_to": {"toot_id": in_reply_to_id},
                        "toot_id": toot_id,
                        "post_text": status,
                    },
                }
            )
            # A reply is itself a new toot: record its old->new id so deeper
            # replies/likes that reference it can be remapped during replay.
            self._record_replayed_post_id(toot_id)
        except ValueError as e:
            self._print(f"Invalid input, regular toot posted: {e!s}", emoji="❌")
            return_msg = f'''There was an error in posting {agent_name}'s reply, response was posted as a new toot!: "{status}"'''
            if (
                self.perform_operations
                and self._mastodon_ops is not None
                and "username" in locals()
            ):
                self._require_mastodon_ops().post_status(
                    login_user=username,
                    status=status,
                )

        except Exception as e:
            self._print(f"An unexpected error occurred, regular toot posted: {e!s}", emoji="❌")
            return_msg = f'''There was an error in posting {agent_name}'s reply, response was posted as a new toot!: "{status}"'''
            if (
                self.perform_operations
                and self._mastodon_ops is not None
                and "username" in locals()
            ):
                self._require_mastodon_ops().post_status(
                    login_user=username,
                    status=status,
                )
        return return_msg

    @app_action
    def get_public_timeline(self, agent_name: str, limit: int) -> str:
        """Read the public Mastodon social network feed."""
        actor_display_name = str(agent_name)
        self._print(f"Fetching public timeline (limit: {limit})", emoji="🌐")
        if self.perform_operations:
            timeline = self._require_mastodon_ops().get_public_timeline(limit=limit)
        else:
            self._print(
                "Skipping real Mastodon API call since perform_operations is set to False",
                color="light_grey",
            )
            timeline = []
        self._print(f"Retrieved {len(timeline)} posts from the public timeline", emoji="📊")
        str_timeline = self.print_and_return_timeline(timeline)
        self._log_action_event(
            {
                "source_user": actor_display_name,
                "label": "get_public_timeline",
                "data": {"num_posts_retrieved": len(timeline)},
            }
        )
        return f"{actor_display_name} viewed the Public Mastodon timeline:\n{str_timeline}"

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
    def get_own_timeline(self, agent_name: str, limit: int, return_str: bool = False) -> str:
        """Read the Mastodon social network feed for the current user."""
        actor_display_name = str(agent_name)
        agent_name = _display_name_key(agent_name)
        username = self._get_username(agent_name)
        self._print(
            f"Fetching @{username}'s timeline (limit: {limit})",
            emoji="🏠",
        )

        if self.perform_operations:
            try:
                timeline = self._require_mastodon_ops().get_own_timeline(username, limit=limit)
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
                "source_user": actor_display_name,
                "label": "get_own_timeline",
                "data": {"num_posts_retreived": len(timeline)},  # TODO: add timeline here
            }
        )

        if return_str:
            str_timeline = self.print_and_return_timeline(timeline)
            return "Own Mastodon Timeline:\n" + str_timeline
        return timeline

    @app_action
    def get_user_timeline(self, agent_name: str, target_user: str, limit: int) -> str:
        """Read a specific user's timeline on the Mastodon social network."""
        actor_display_name = str(agent_name)
        agent_name = _display_name_key(agent_name)
        target_user_full = str(target_user)
        target_user = _display_name_key(target_user)
        current_username = self._get_username(agent_name)
        target_username = self._get_username(target_user)
        self._print(
            f"@{current_username} fetching @{target_username}'s timeline (limit: {limit})",
            emoji="👥",
        )
        if self.perform_operations:
            timeline = self._require_mastodon_ops().get_user_timeline(
                current_username, target_username, limit=limit
            )
        else:
            timeline = []
            self._print(
                "Skipping real Mastodon API call since perform_operations is set to False",
                color="light_grey",
            )
        self._print(
            f"Retrieved {len(timeline)} posts from @{target_username}'s timeline",
            emoji="📊",
        )
        str_timeline = self.print_and_return_timeline(timeline)
        self._log_action_event(
            {
                "source_user": actor_display_name,
                "label": "get_user_timeline",
                "data": {
                    "target_user": target_user_full,
                    "num_posts_retrieved": len(timeline),
                },
            }
        )
        return (
            f"@{target_username}'s Mastodon Timeline (viewed by @{current_username}):\n"
            f"{str_timeline}"
        )

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
    def read_notifications(self, agent_name: str, clear: bool, limit: int) -> str:
        """Read Mastodon social network notifications."""
        actor_display_name = str(agent_name)
        agent_name = _display_name_key(agent_name)

        username = self._get_username(agent_name)
        self._print(
            f"Reading notifications for @{username} (clear: {clear}, limit: {limit})",
            emoji="🔔",
        )
        if self.perform_operations:
            notifications = self._require_mastodon_ops().read_notifications(
                username, clear=clear, limit=limit
            )
        else:
            notifications = []
            self._print(
                "Skipping real Mastodon API call since perform_operations is set to False",
                color="light_grey",
            )

        retrieval_message = f"Retrieved {len(notifications)} notifications for {agent_name}:"
        self._print(retrieval_message, emoji="📬")

        notifications_string = self.print_notifications(notifications)
        full_output = f"{retrieval_message}\n{notifications_string}"
        self._print(full_output)
        self._log_action_event(
            {
                "source_user": actor_display_name,
                "label": "read_notification",
                "data": {
                    "num_notifications_retreived": len(notifications)
                },  # TODO: add notifications timeline here
            }
        )

        return full_output

    @app_action
    def like_toot(self, agent_name: str, toot_id: str) -> str:
        """Like (favorite) a toot."""
        actor_display_name = str(agent_name)
        agent_name = _display_name_key(agent_name)
        current_username = self._get_username(agent_name)
        # self._print(
        #     f"@{current_username} liking post {toot_id}",
        #     emoji="❤️",
        # )
        try:
            like_message = f"{agent_name} (@{current_username}) liked post {toot_id}"
            if self.perform_operations:
                mastodon_ops = self._require_mastodon_ops()
                check = mastodon_ops.like_check(current_username, toot_id)
                if not check:
                    mastodon_ops.like_toot(current_username, toot_id)
                else:
                    like_message = f"{agent_name} (@{current_username}) has previously liked post {toot_id}. Please conduct a different action!!"
            else:
                self._print(
                    "Skipping real Mastodon API call since perform_operations is set to False",
                    color="light_grey",
                )
            self._print(like_message, emoji="✅")
            self._log_action_event(
                {
                    "source_user": actor_display_name,
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
    def boost_toot(self, agent_name: str, toot_id: str) -> str:
        """Boost (reblog) a toot."""
        actor_display_name = str(agent_name)
        agent_name = _display_name_key(agent_name)
        current_username = self._get_username(agent_name)
        self._print(
            f"@{current_username} boosting post {toot_id}",
            emoji="🔁",
        )
        try:
            boost_message = f"{agent_name} (@{current_username}) boosted post {toot_id}"
            if self.perform_operations:
                mastodon_ops = self._require_mastodon_ops()
                check = mastodon_ops.boost_check(current_username, toot_id)
                if not check:
                    mastodon_ops.boost_toot(current_username, toot_id)
                else:
                    boost_message = f"{agent_name} (@{current_username}) has previously boosted post {toot_id}. Please conduct a different action!!"
            self._print(
                f"@{current_username} boosted post {toot_id}",
                emoji="✅",
            )
            self._log_action_event(
                {
                    "source_user": actor_display_name,
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

    @app_action
    def block_user(self, agent_name: str, target_user: str) -> str:
        """Block a user on the Mastodon social network."""
        actor_display_name = str(agent_name)
        agent_name = _display_name_key(agent_name)
        target_user_full = str(target_user)
        target_user = _display_name_key(target_user)
        current_username = self._get_username(agent_name)
        target_username = self._get_username(target_user)
        self._print(f"@{current_username} blocking user: @{target_username}", emoji="🚫")
        if self.perform_operations:
            self._require_mastodon_ops().block_user(current_username, target_username)
        else:
            self._print(
                "Skipping real Mastodon API call since perform_operations is set to False",
                color="light_grey",
            )
        block_message = (
            f"{actor_display_name} (@{current_username}) blocked "
            f"{target_user_full} (@{target_username})"
        )
        self._print(block_message, emoji="✅")
        self._log_action_event(
            {
                "source_user": actor_display_name,
                "label": "block_user",
                "data": {"target_user": target_user_full},
            }
        )
        return block_message

    @app_action
    def unblock_user(self, agent_name: str, target_user: str) -> str:
        """Unblock a previously blocked user."""
        actor_display_name = str(agent_name)
        agent_name = _display_name_key(agent_name)
        target_user_full = str(target_user)
        target_user = _display_name_key(target_user)
        current_username = self._get_username(agent_name)
        target_username = self._get_username(target_user)
        self._print(f"@{current_username} unblocking user: @{target_username}", emoji="✅")
        if self.perform_operations:
            self._require_mastodon_ops().unblock_user(current_username, target_username)
        else:
            self._print(
                "Skipping real Mastodon API call since perform_operations is set to False",
                color="light_grey",
            )
        unblock_message = (
            f"{actor_display_name} (@{current_username}) unblocked "
            f"{target_user_full} (@{target_username})"
        )
        self._print(unblock_message, emoji="✅")
        self._log_action_event(
            {
                "source_user": actor_display_name,
                "label": "unblock_user",
                "data": {"target_user": target_user_full},
            }
        )
        return unblock_message

    @app_action
    def mute_account(
        self,
        agent_name: str,
        target_user: str,
        notifications: bool = False,
        duration: int = 0,
    ) -> str:
        """Mute an account, optionally suppressing notifications for a duration."""
        actor_display_name = str(agent_name)
        agent_name = _display_name_key(agent_name)
        target_user_full = str(target_user)
        target_user = _display_name_key(target_user)
        current_username = self._get_username(agent_name)
        target_username = self._get_username(target_user)
        self._print(
            f"@{current_username} muting @{target_username} "
            f"(notifications: {notifications}, duration: {duration})",
            emoji="🔇",
        )
        if self.perform_operations:
            self._require_mastodon_ops().mute_account(
                current_username,
                target_username,
                notifications=notifications,
                duration=duration,
            )
        else:
            self._print(
                "Skipping real Mastodon API call since perform_operations is set to False",
                color="light_grey",
            )
        mute_message = (
            f"{actor_display_name} (@{current_username}) muted "
            f"{target_user_full} (@{target_username})"
        )
        self._print(mute_message, emoji="✅")
        self._log_action_event(
            {
                "source_user": actor_display_name,
                "label": "mute_account",
                "data": {"target_user": target_user_full},
            }
        )
        return mute_message

    @app_action
    def unmute_account(self, agent_name: str, target_user: str) -> str:
        """Unmute a previously muted account."""
        actor_display_name = str(agent_name)
        agent_name = _display_name_key(agent_name)
        target_user_full = str(target_user)
        target_user = _display_name_key(target_user)
        current_username = self._get_username(agent_name)
        target_username = self._get_username(target_user)
        self._print(f"@{current_username} unmuting @{target_username}", emoji="🔊")
        if self.perform_operations:
            self._require_mastodon_ops().unmute_account(current_username, target_username)
        else:
            self._print(
                "Skipping real Mastodon API call since perform_operations is set to False",
                color="light_grey",
            )
        unmute_message = (
            f"{actor_display_name} (@{current_username}) unmuted "
            f"{target_user_full} (@{target_username})"
        )
        self._print(unmute_message, emoji="✅")
        self._log_action_event(
            {
                "source_user": actor_display_name,
                "label": "unmute_account",
                "data": {"target_user": target_user_full},
            }
        )
        return unmute_message

    @app_action
    def delete_posts(
        self,
        agent_name: str,
        post_ids: list[int] | None = None,
        recent_count: int = 0,
        delete_all: bool = False,
    ) -> str:
        """Delete a user's own posts (by id, recent count, or all)."""
        actor_display_name = str(agent_name)
        agent_name = _display_name_key(agent_name)
        username = self._get_username(agent_name)
        if delete_all:
            self._print(f"Deleting all posts for @{username}", emoji="🗑️")
        elif recent_count:
            self._print(f"Deleting {recent_count} recent posts for @{username}", emoji="🗑️")
        elif post_ids:
            self._print(f"Deleting specific posts for @{username}", emoji="🗑️")
        else:
            self._print("No posts specified for deletion", emoji="❌")
            return f"{actor_display_name} (@{username}) specified no posts to delete."

        if self.perform_operations:
            self._require_mastodon_ops().delete_posts(
                username,
                post_ids=post_ids,
                recent_count=recent_count,
                delete_all=delete_all,
            )
        else:
            self._print(
                "Skipping real Mastodon API call since perform_operations is set to False",
                color="light_grey",
            )
        delete_message = f"{actor_display_name} (@{username}) completed post deletion."
        self._print(delete_message, emoji="✅")
        self._log_action_event(
            {
                "source_user": actor_display_name,
                "label": "delete_posts",
                "data": {
                    "post_ids": [str(pid) for pid in (post_ids or [])],
                    "recent_count": recent_count,
                    "delete_all": delete_all,
                },
            }
        )
        return delete_message

    @app_action
    def send_direct_message(self, agent_name: str, target_user: str, message: str) -> str:
        """Send a direct (private) message to another user."""
        actor_display_name = str(agent_name)
        agent_name = _display_name_key(agent_name)
        target_user_full = str(target_user)
        target_user = _display_name_key(target_user)
        current_username = self._get_username(agent_name)
        target_username = self._get_username(target_user)
        self._print(f"@{current_username} sending DM to @{target_username}", emoji="✉️")
        if self.perform_operations:
            self._require_mastodon_ops().post_status(
                login_user=current_username,
                status=f"@{target_username} {message}",
                visibility="direct",
            )
        else:
            self._print(
                "Skipping real Mastodon API call since perform_operations is set to False",
                color="light_grey",
            )
        dm_message = (
            f"{actor_display_name} (@{current_username}) sent a direct message to "
            f"{target_user_full} (@{target_username})"
        )
        self._print(dm_message, emoji="✅")
        self._log_action_event(
            {
                "source_user": actor_display_name,
                "label": "send_direct_message",
                "data": {"target_user": target_user_full, "message": message},
            }
        )
        return dm_message

    # endregion


# endregion
