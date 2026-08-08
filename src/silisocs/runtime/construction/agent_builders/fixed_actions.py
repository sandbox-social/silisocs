"""Fixed-action rendering helpers for agent builders."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

import yaml

from silisocs.runtime.construction.agent_builders.common import resolve_source, to_plain


class FixedActionBuilder:
    """Load fixed action sets and render per-agent fixed-action config."""

    def __init__(
        self,
        config: Any,
        resolve_file_path: Callable[[str], Path],
        *,
        root_sets: Any = None,
    ):
        self.config = config
        self.resolve_file_path = resolve_file_path
        # Root-level `fixed_action_sets:` (declared in a `@package _global_` world
        # file) is an accepted spelling — config validation and the class-pipeline
        # `action_set_ref` check both read it — but this builder is constructed with
        # `cfg.agents`, so the root block has to be threaded in or a config that
        # validates would silently load no sets at all.
        self.root_sets = root_sets

    def load_fixed_action_sets(self) -> dict[str, list[dict[str, Any]]]:
        raw = getattr(self.config, "fixed_action_sets", None)
        if not raw:
            raw = self.root_sets
        cfg = to_plain(raw or {}, where="fixed_action_sets") or {}
        inline_sets = cfg.get("inline", {}) if isinstance(cfg, Mapping) else {}
        file_path = cfg.get("file") if isinstance(cfg, Mapping) else None

        merged: dict[str, list[dict[str, Any]]] = {}
        if file_path:
            path = self.resolve_file_path(str(file_path))
            with open(path) as f:
                from_file = json.load(f) if path.suffix.lower() == ".json" else yaml.safe_load(f)
            merged.update(self.parse_fixed_action_sets(from_file or {}))

        merged.update(self.parse_fixed_action_sets(inline_sets))
        return merged

    @staticmethod
    def parse_fixed_action_sets(data: Any) -> dict[str, list[dict[str, Any]]]:
        raw = to_plain(data) or {}
        if not isinstance(raw, Mapping):
            return {}

        parsed: dict[str, list[dict[str, Any]]] = {}
        for set_name, payload in raw.items():
            if not isinstance(payload, Mapping):
                continue
            items = payload.get("actions", payload)
            if isinstance(items, Mapping):
                items = [items]
            if not isinstance(items, list):
                continue
            action_items = [dict(item) for item in items if isinstance(item, Mapping)]
            if action_items:
                parsed[str(set_name)] = action_items
        return parsed

    def build_fixed_action_config(
        self,
        *,
        class_cfg: Any,
        fixed_action_sets: dict[str, list[dict[str, Any]]],
        render_context: Mapping[str, Any],
        where: str = "fixed_action",
    ) -> dict[str, Any] | None:
        cfg = to_plain(class_cfg, where=where) or {}
        if not isinstance(cfg, Mapping) or not bool(cfg.get("enabled", False)):
            return None

        set_ref = str(cfg.get("action_set_ref", "")).strip()
        if not set_ref:
            raise ValueError(f"{where}.enabled=true requires {where}.action_set_ref")
        if set_ref not in fixed_action_sets:
            available = ", ".join(sorted(fixed_action_sets.keys())) or "<none>"
            raise ValueError(
                f"{where}.action_set_ref '{set_ref}' not found. Available sets: {available}"
            )

        rendered_actions: list[dict[str, Any]] = []
        for index, raw_action in enumerate(fixed_action_sets[set_ref]):
            entry = f"fixed_action_sets[{set_ref!r}][{index}]"
            action_name = str(raw_action.get("action", "")).strip()
            if not action_name:
                raise ValueError(
                    f"{entry} (referenced by {where}.action_set_ref) declares no "
                    "`action`; every fixed-action entry must name a backend action."
                )
            args = raw_action.get("args") or {}
            if not isinstance(args, Mapping):
                raise ValueError(f"{entry}.args must be a mapping; got {type(args).__name__}.")
            item: dict[str, Any] = {
                "action": action_name,
                "args": {str(k): self.render_template(v, render_context) for k, v in args.items()},
            }
            if "weight" in raw_action:
                item["weight"] = raw_action.get("weight")
            rendered_actions.append(item)

        return {
            "enabled": True,
            "action_set_ref": set_ref,
            "selection_policy": str(cfg.get("selection_policy", "round_robin")),
            "on_exhaustion": str(cfg.get("on_exhaustion", "loop")),
            "actions": rendered_actions,
        }

    @staticmethod
    def _episode_index(raw: Any, *, where: str) -> int:
        """Coerce a plan entry's ``episode`` to an int, raising on anything else.

        Defaulting a malformed value to 0 silently reschedules the action to the
        very first step, which is indistinguishable from an intentional step-0
        action in the logs.
        """
        if raw is None:
            return 0
        if isinstance(raw, bool) or not isinstance(raw, (int, float, str)):
            raise ValueError(f"{where} has a non-numeric `episode`: {raw!r}.")
        try:
            episode = int(raw)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{where} has a non-numeric `episode`: {raw!r}.") from exc
        if episode < 0:
            raise ValueError(f"{where} has a negative `episode`: {episode}.")
        return episode

    @staticmethod
    def normalize_fixed_action_plan(
        actions: Any, *, where: str = "fixed_action.actions"
    ) -> dict[int, list[dict[str, Any]]]:
        """Group a fixed-action list into an episode -> actions plan.

        Every rejection raises rather than dropping the entry: a plan that silently
        loses an action produces a run that looks fine and does the wrong thing.
        """
        if actions is None:
            return {}
        if not isinstance(actions, list):
            raise ValueError(
                f"{where} must be a list of action entries; got {type(actions).__name__}."
            )

        plan: dict[int, list[dict[str, Any]]] = {}
        for index, item in enumerate(actions):
            entry = f"{where}[{index}]"
            if not isinstance(item, Mapping):
                raise ValueError(f"{entry} must be a mapping; got {type(item).__name__}.")
            if "action_type" in item:
                episode = FixedActionBuilder._episode_index(item.get("episode"), where=entry)
                plan.setdefault(episode, []).append(dict(item))
                continue

            action_name = str(item.get("action", "")).strip()
            if not action_name:
                raise ValueError(
                    f"{entry} declares neither `action` nor `action_type`; every "
                    "fixed-action entry must name the backend action to invoke."
                )
            args = item.get("args") or {}
            if not isinstance(args, Mapping):
                raise ValueError(f"{entry}.args must be a mapping; got {type(args).__name__}.")
            raw_episode = item.get("episode", args.get("episode"))
            episode = FixedActionBuilder._episode_index(raw_episode, where=entry)
            normalized = {
                "action_type": action_name,
                "target_id": str(args.get("post_id", args.get("target_id", "")) or ""),
                "content": str(args.get("status", args.get("content", "")) or ""),
                "reasoning": str(args.get("reasoning", "Fixed action set item.") or ""),
                "tool_kwargs": {
                    str(key): value for key, value in args.items() if str(key) != "episode"
                },
            }
            plan.setdefault(episode, []).append(normalized)
        return plan

    def render_template(self, value: Any, context: Mapping[str, Any]) -> Any:
        if isinstance(value, str):
            return resolve_source(context, value) if "{" in value and "}" in value else value
        if isinstance(value, list):
            return [self.render_template(item, context) for item in value]
        if isinstance(value, Mapping):
            return {str(k): self.render_template(v, context) for k, v in value.items()}
        return value
