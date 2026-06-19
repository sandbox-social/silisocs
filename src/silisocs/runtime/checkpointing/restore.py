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
            _game_master_for_agent_action(source_user, tool_call, game_masters).resolve_action(
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


def _game_master_for_agent_action(
    agent_name: str,
    tool_call: ToolCall,
    game_masters: Sequence[Any],
) -> Any:
    """Return the GM that should replay one action event for an agent."""
    if not game_masters:
        raise ValueError("Checkpoint replay requires at least one game master.")
    if len(game_masters) == 1:
        return game_masters[0]

    by_name = {str(getattr(gm, "name", "")): gm for gm in game_masters}
    unnamed = [gm for gm in game_masters if not str(getattr(gm, "name", "")).strip()]
    if unnamed:
        raise ValueError("Checkpoint replay requires every game master to have a name.")
    if len(by_name) != len(game_masters):
        raise ValueError("Checkpoint replay requires unique game master names.")

    agent_flow = _flow_for_agent(agent_name, game_masters)
    chain = _flow_chain_for_flow(agent_flow, game_masters)
    unknown = [name for name in chain if name not in by_name]
    if unknown:
        raise ValueError(
            f"Checkpoint replay flow '{agent_flow}' references unknown GM(s): {unknown}"
        )
    chain_candidates = [by_name[name] for name in chain]
    matches = [gm for gm in chain_candidates if _gm_backend_exposes_action(gm, tool_call.name)]
    if not matches:
        raise ValueError(
            "Checkpoint replay could not route action "
            f"'{tool_call.name}' for agent '{agent_name}' in flow '{agent_flow}'."
        )
    if len(matches) > 1:
        names = [str(getattr(gm, "name", "")) for gm in matches]
        raise ValueError(
            "Checkpoint replay action routing is ambiguous for "
            f"'{tool_call.name}' in flow '{agent_flow}': {names}"
        )
    return matches[0]


def _flow_for_agent(agent_name: str, game_masters: Sequence[Any]) -> str:
    flows: set[str] = set()
    for gm in game_masters:
        flow_tags = getattr(gm, "agent_flow_tags", None)
        if isinstance(flow_tags, Mapping):
            flow = str(flow_tags.get(agent_name, "") or "").strip()
            if flow:
                flows.add(flow)
    if not flows:
        raise ValueError(f"Checkpoint replay requires agent_flow_tags for agent '{agent_name}'.")
    if len(flows) > 1:
        raise ValueError(
            f"Checkpoint replay found conflicting flows for agent '{agent_name}': {sorted(flows)}"
        )
    return next(iter(flows))


def _flow_chain_for_flow(flow: str, game_masters: Sequence[Any]) -> list[str]:
    chains: set[tuple[str, ...]] = set()
    for gm in game_masters:
        flow_chains = getattr(gm, "flow_chains", None)
        if isinstance(flow_chains, Mapping):
            raw_chain = flow_chains.get(flow)
            if isinstance(raw_chain, str):
                chain = tuple([raw_chain.strip()] if raw_chain.strip() else [])
            elif isinstance(raw_chain, Sequence) and not isinstance(raw_chain, (str, bytes)):
                chain = tuple(str(name).strip() for name in raw_chain if str(name).strip())
            elif raw_chain is None:
                continue
            else:
                raise ValueError(
                    f"Checkpoint replay flow chain for '{flow}' must be a string or list."
                )
            if chain:
                chains.add(chain)
    if not chains:
        raise ValueError(f"Checkpoint replay requires flow_chains metadata for flow '{flow}'.")
    if len(chains) > 1:
        raise ValueError(
            f"Checkpoint replay found conflicting flow chains for flow '{flow}': {sorted(chains)}"
        )
    return list(next(iter(chains)))


def _gm_backend_exposes_action(game_master: Any, action_name: str) -> bool:
    backend = getattr(game_master, "backend", None)
    actions = getattr(backend, "actions", None)
    if not callable(actions):
        return False
    for action in actions():
        if action_name in {
            str(getattr(action, "name", "")),
            str(getattr(action, "selectable_name", "")),
        }:
            return True
    return False
