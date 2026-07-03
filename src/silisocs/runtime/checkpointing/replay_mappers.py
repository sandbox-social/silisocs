"""Action-event → replay-action mappers for the built-in replay restore strategy.

Checkpoint *mechanisms* are pluggable via ``sim.checkpoint.restore`` (a
``{built_in|class_path}`` strategy slot) — replay is just one such mechanism.
Rather than bolt a replay-specific method onto every backend, the replay
mechanism owns its per-backend knowledge here: a small registry keyed by
``backend_type`` maps a backend family's logged action vocabulary to the actions
that reconstruct it. Backends stay clean (they implement only ``get_state`` /
``set_state`` for the authoritative-snapshot path); a backend "supports replay"
exactly when its ``backend_type`` has a registered mapper.

Custom backends opt into the built-in replay strategy with
:func:`register_replay_mapper` (mirroring ``register_llm_provider``) — no core
edit and no backend method required. A backend needing bespoke restore logic
that a stateless mapper cannot express writes a custom
``sim.checkpoint.restore.class_path`` strategy instead.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from silisocs.runtime.types import ActionOutput, ToolCall

# A mapper translates one logged action event ``(label, data)`` into the action
# that replays it, or ``None`` for a non-replayable bookkeeping label.
ReplayEventMapper = Callable[[str, Mapping[str, Any]], ActionOutput | None]


def _replay_target_id(label: str, data: Mapping[str, Any]) -> str:
    """Extract the post/toot id a replayed social action targets."""
    target = (
        data.get("post_id") or data.get("tweet_id") or data.get("toot_id") or data.get("target_id")
    )
    if target is None:
        raise ValueError(f"{label} event missing target id: {data}")
    return str(target)


def microblog_event_to_replay_action(label: str, data: Mapping[str, Any]) -> ActionOutput | None:
    """Map a logged microblog action event to its replayable action.

    The canonical microblog vocabulary (create/like/repost/reply/follow) shared by
    Twitter-like backends; returns ``None`` for a non-replayable bookkeeping label.
    """
    if label == "post":
        text = (
            data.get("post_text") or data.get("text") or data.get("content") or data.get("status")
        )
        if not text:
            raise ValueError(f"Post event missing text: {data}")
        return ActionOutput.from_tool_calls([ToolCall("create_tweet", {"status": str(text)})])
    if label in {"like", "like_toot"}:
        return ActionOutput.from_tool_calls(
            [ToolCall("like_tweet", {"post_id": _replay_target_id("Like", data)})]
        )
    if label in {"repost", "boost_toot"}:
        return ActionOutput.from_tool_calls(
            [ToolCall("repost_tweet", {"post_id": _replay_target_id("Repost", data)})]
        )
    if label == "reply":
        text = (
            data.get("post_text") or data.get("text") or data.get("content") or data.get("status")
        )
        if not text:
            raise ValueError(f"Reply event missing text: {data}")
        return ActionOutput.from_tool_calls(
            [
                ToolCall(
                    "reply_to_tweet",
                    {"post_id": _replay_target_id("Reply", data), "status": str(text)},
                )
            ]
        )
    if label in {"follow", "unfollow"}:
        target = data.get("target_user") or data.get("target") or data.get("user")
        if not target:
            raise ValueError(f"{label} event missing target user: {data}")
        return ActionOutput.from_tool_calls(
            [ToolCall(f"{label}_user", {"target_user": str(target)})]
        )
    raise ValueError(f"Unknown microblog action event label for checkpoint replay: {label}")


# backend_type -> mapper. Seeded with the built-in social backends the built-in
# replay strategy knows how to reconstruct; extend it with register_replay_mapper.
_REPLAY_MAPPERS: dict[str, ReplayEventMapper] = {
    "twitter_like": microblog_event_to_replay_action,
}


def register_replay_mapper(backend_type: str, mapper: ReplayEventMapper) -> None:
    """Register a replay mapper for ``backend_type`` (idempotent overwrite).

    Call before a resume runs so the built-in ``social_action_event_replay``
    strategy can reconstruct a custom backend from its logged action events.
    """
    key = str(backend_type or "").strip()
    if not key:
        raise ValueError("register_replay_mapper requires a non-empty backend_type.")
    _REPLAY_MAPPERS[key] = mapper


def get_replay_mapper(backend_type: str) -> ReplayEventMapper | None:
    """Return the replay mapper for ``backend_type``, or ``None`` if none is registered."""
    return _REPLAY_MAPPERS.get(str(backend_type or "").strip())
