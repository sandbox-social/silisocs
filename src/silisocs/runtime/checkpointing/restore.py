"""Checkpoint restore strategies."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from silisocs.runtime.types import ActionOutput, ToolCall


class CheckpointRestoreStrategy:
    """Checkpoint-owned runtime restore hook."""

    def restore(
        self,
        *,
        game_masters: Sequence[Any],
        action_events_file: Path,
        checkpoint_step: int,
    ) -> None:
        del game_masters, action_events_file, checkpoint_step


class SocialActionEventReplayRestore(CheckpointRestoreStrategy):
    """Replay social backend action events through the native GM resolve surface."""

    def restore(
        self,
        *,
        game_masters: Sequence[Any],
        action_events_file: Path,
        checkpoint_step: int,
    ) -> None:
        if not game_masters:
            raise ValueError("Checkpoint restore requires at least one game master.")
        for row in _read_jsonl(action_events_file):
            if str(row.get("event_type", "")) != "action":
                continue
            episode = row.get("episode")
            try:
                if episode is None or int(episode) >= int(checkpoint_step):
                    continue
            except (TypeError, ValueError) as exc:
                raise ValueError(f"Malformed action event episode: {row}") from exc
            label = str(row.get("label", "") or "").strip()
            if label in _IGNORED_ACTION_LABELS:
                continue
            source_user = str(row.get("source_user", "") or "").strip()
            if not source_user:
                raise ValueError(f"Action event missing source_user: {row}")
            data = row.get("data")
            if not isinstance(data, Mapping):
                raise ValueError(f"Action event data must be a mapping: {row}")
            tool_call = _event_to_tool_call(label, data)
            _first_game_master_for_agent(source_user, game_masters).resolve_action(
                source_user,
                ActionOutput.from_tool_calls([tool_call]),
            )


def build_checkpoint_restore(slot_cfg: Any) -> CheckpointRestoreStrategy:
    """Build the configured checkpoint restore strategy."""
    if slot_cfg is None:
        raise ValueError("sim.checkpoint.source_run requires sim.checkpoint.restore.")
    built_in = str(getattr(slot_cfg, "built_in", "") or "").strip()
    class_path = str(getattr(slot_cfg, "class_path", "") or "").strip()
    if class_path:
        raise ValueError("Custom checkpoint restore class_path is not implemented yet.")
    if built_in == "social_action_event_replay":
        return SocialActionEventReplayRestore()
    raise ValueError(
        "Unknown sim.checkpoint.restore.built_in "
        f"{built_in!r}. Available: social_action_event_replay."
    )


_IGNORED_ACTION_LABELS = {
    "initialize",
    "init_create_user",
    "init_follow",
    "read_profile",
    "get_own_timeline",
    "get_home_feed",
    "timeline_retrieval",
    "timeline_retrieval_error",
    "recsys_init",
    "recsys_update",
    "get_trending",
    "do_nothing",
}


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    try:
        with path.open(encoding="utf-8") as f:
            for line_no, line in enumerate(f, start=1):
                stripped = line.strip()
                if not stripped:
                    continue
                parsed = json.loads(stripped)
                if not isinstance(parsed, dict):
                    raise ValueError(f"JSONL row {line_no} must be an object.")
                rows.append(parsed)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Malformed action_events JSONL: {path}") from exc
    return rows


def _event_to_tool_call(label: str, data: Mapping[str, Any]) -> ToolCall:
    if label == "post":
        text = (
            data.get("post_text") or data.get("text") or data.get("content") or data.get("status")
        )
        if not text:
            raise ValueError(f"Post event missing text: {data}")
        return ToolCall("create_tweet", {"status": str(text)})
    if label in {"like", "like_toot"}:
        return ToolCall("like_tweet", {"post_id": _target_id("Like", data)})
    if label in {"repost", "boost_toot"}:
        return ToolCall("repost_tweet", {"post_id": _target_id("Repost", data)})
    if label == "reply":
        target = _target_id("Reply", data)
        text = (
            data.get("post_text") or data.get("text") or data.get("content") or data.get("status")
        )
        if not text:
            raise ValueError(f"Reply event missing text: {data}")
        return ToolCall("reply_to_tweet", {"post_id": target, "status": str(text)})
    if label in {"follow", "unfollow"}:
        follow_target = data.get("target_user") or data.get("target") or data.get("user")
        if not follow_target:
            raise ValueError(f"{label} event missing target user: {data}")
        return ToolCall(f"{label}_user", {"target_user": str(follow_target)})
    raise ValueError(f"Unknown social action event label for checkpoint restore: {label}")


def _target_id(label: str, data: Mapping[str, Any]) -> str:
    target = (
        data.get("post_id") or data.get("tweet_id") or data.get("toot_id") or data.get("target_id")
    )
    if target is None:
        raise ValueError(f"{label} event missing target id: {data}")
    return str(target)


def _first_game_master_for_agent(agent_name: str, game_masters: Sequence[Any]) -> Any:
    del agent_name
    return game_masters[0]
