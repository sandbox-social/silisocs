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

    def __init__(self, config: Any, resolve_file_path: Callable[[str], Path]):
        self.config = config
        self.resolve_file_path = resolve_file_path

    def load_fixed_action_sets(self) -> dict[str, list[dict[str, Any]]]:
        cfg = to_plain(getattr(self.config, "fixed_action_sets", {})) or {}
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
    ) -> dict[str, Any] | None:
        cfg = to_plain(class_cfg) or {}
        if not isinstance(cfg, Mapping) or not bool(cfg.get("enabled", False)):
            return None

        set_ref = str(cfg.get("action_set_ref", "")).strip()
        if not set_ref:
            raise ValueError("fixed_action.enabled=true requires fixed_action.action_set_ref")
        if set_ref not in fixed_action_sets:
            available = ", ".join(sorted(fixed_action_sets.keys())) or "<none>"
            raise ValueError(
                f"fixed_action.action_set_ref '{set_ref}' not found. Available sets: {available}"
            )

        rendered_actions: list[dict[str, Any]] = []
        for raw_action in fixed_action_sets[set_ref]:
            action_name = str(raw_action.get("action", "")).strip()
            if not action_name:
                continue
            args = raw_action.get("args") or {}
            if not isinstance(args, Mapping):
                args = {}
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
    def normalize_fixed_action_plan(actions: Any) -> dict[int, list[dict[str, Any]]]:
        if not isinstance(actions, list):
            return {}

        plan: dict[int, list[dict[str, Any]]] = {}
        for item in actions:
            if not isinstance(item, Mapping):
                continue
            if "action_type" in item:
                try:
                    episode = int(item.get("episode", 0))
                except (TypeError, ValueError):
                    episode = 0
                plan.setdefault(episode, []).append(dict(item))
                continue

            action_name = str(item.get("action", "")).strip()
            if not action_name:
                continue
            args = item.get("args") or {}
            if not isinstance(args, Mapping):
                args = {}
            try:
                episode = int(item.get("episode", args.get("episode", 0)))
            except (TypeError, ValueError):
                episode = 0
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
