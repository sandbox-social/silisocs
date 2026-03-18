"""Concordia-native resolve components for social-media game masters."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from concordia.typing import entity as entity_lib
from concordia.typing import entity_component

from mastodon_sim.runtime.config import ConfigStore

_ACTION_BLOCK_PATTERN = re.compile(
    r"(?ims)^\s*(?P<label>ACTION TYPE|TARGET ID|CONTENT|REASONING)\s*:\s*"
    r"(?P<value>.*?)(?=^\s*(?:ACTION TYPE|TARGET ID|CONTENT|REASONING)\s*:|\Z)"
)
_POST_TARGET_PLACEHOLDERS = {
    "",
    "n/a",
    "na",
    "none",
    "null",
    "[n/a]",
    "[none]",
    "[none - new post]",
    "[n/a - new post]",
}
_TARGET_REQUIRED_ACTIONS = {
    "reply",
    "comment",
    "like",
    "upvote",
    "downvote",
    "repost",
    "retweet",
    "boost",
}


def _normalize_target_id(action_type: str, target_id: str) -> str:
    cleaned = target_id.strip()
    if not cleaned:
        return ""

    normalized = re.sub(r"\s+", " ", cleaned.strip("[]").strip().lower())
    if action_type.strip().lower() == "post" and (
        normalized in _POST_TARGET_PLACEHOLDERS or normalized.startswith(("none", "n/a"))
    ):
        return ""

    if action_type.strip().lower() in _TARGET_REQUIRED_ACTIONS:
        digit_match = re.search(r"\d+", cleaned)
        if digit_match:
            return digit_match.group(0)

    return cleaned


def find_and_parse_action_data(data_string: str) -> dict[str, str] | None:
    """Find and parse an ACTION TYPE/TARGET ID/CONTENT/REASONING block."""
    parsed_sections: dict[str, str] = {}
    for match in _ACTION_BLOCK_PATTERN.finditer(data_string):
        label = match.group("label").strip().lower().replace(" ", "_")
        parsed_sections[label] = match.group("value").strip()

    action_type = parsed_sections.get("action_type", "").strip()
    if not action_type:
        return None

    return {
        "action_type": action_type,
        "target_id": _normalize_target_id(action_type, parsed_sections.get("target_id", "")),
        "content": parsed_sections.get("content", "").strip(),
        "reasoning": parsed_sections.get("reasoning", "").strip(),
    }


@dataclass
class _BaseResolveComponent(
    entity_component.ContextComponent, entity_component.ComponentWithLogging
):
    """Base class for resolve components consumed by SwitchAct."""

    sm_app: Any
    call_to_action_str: str = ""
    model: Any = None

    def pre_act(self, action_spec: entity_lib.ActionSpec) -> str:
        """Execute resolve logic when the game master asks for RESOLVE output."""
        if action_spec.output_type != entity_lib.OutputType.RESOLVE:
            return ""

        if ":" not in action_spec.call_to_action:
            return ""
        active_entity, action_text = action_spec.call_to_action.split(":", 1)
        result = self.resolve(active_entity=active_entity, action_text=action_text)
        self._logging_channel(
            {
                "Key": "resolve",
                "Summary": result,
                "Value": result,
                "Action": action_spec.call_to_action,
            }
        )
        return result

    def resolve(self, *, active_entity: str, action_text: str) -> str:
        """Resolve raw action text into backend operation result."""
        raise NotImplementedError

    def get_state(self) -> entity_component.ComponentState:
        """Return serializable component state."""
        return {}

    def set_state(self, state: entity_component.ComponentState) -> None:
        """Restore component state."""
        del state


@dataclass
class ParsedActionResolveComponent(_BaseResolveComponent):
    """Resolve using ACTION TYPE/TARGET ID/CONTENT parser output."""

    def resolve(self, *, active_entity: str, action_text: str) -> str:
        action_data = find_and_parse_action_data(action_text)
        if action_data is None:
            return ""
        return self.sm_app.parse_and_resolve_action(active_entity, action_data)


@dataclass
class GenericActionResolveComponent(_BaseResolveComponent):
    """Resolve generic ACTION: name / param: value format."""

    def resolve(self, *, active_entity: str, action_text: str) -> str:
        action_match = re.search(r"(?i)ACTION:\s*(\w+)", action_text)
        if not action_match:
            return ""
        action_name = action_match.group(1).strip()
        args_text = action_text[action_match.end() :].strip()
        return self.sm_app.invoke_action_by_name(action_name, args_text) or ""


@dataclass
class ToolCallingResolveComponent(_BaseResolveComponent):
    """Resolve through tool-calling with backend actions as tools."""

    include_yaml_action_instructions: bool = True
    include_generic_catalog: bool = True

    def resolve(self, *, active_entity: str, action_text: str) -> str:
        if self.model is None or not hasattr(self.model, "sample_tool_call"):
            msg = (
                "Tool-calling resolve mode requires a model with sample_tool_call(); "
                f"{type(self.model).__name__ if self.model is not None else 'None'} does not support it."
            )
            return msg

        cfg = ConfigStore.get_config()
        timeline_posts = getattr(cfg.sim, "timeline_posts", 10)
        timeline = self.sm_app.get_timeline(active_entity, timeline_posts)
        observation = self.sm_app.format_timeline_for_observation(timeline)

        prompt_parts = [
            f"Active user: {active_entity}",
            f"Timeline for {active_entity}:\n{observation}",
            f"User's proposed action or intent:\n{action_text.strip()}",
        ]

        if self.include_yaml_action_instructions and self.call_to_action_str:
            prompt_parts.append(
                "YAML-defined action guidance for this environment:\n"
                f"{self.call_to_action_str.strip()}"
            )

        if self.include_generic_catalog:
            prompt_parts.append(
                f"Available backend action API:\n{self.sm_app.generate_generic_action_prompt()}"
            )

        prompt_parts.append(
            "Choose exactly one tool call that best matches the intent and the available timeline IDs."
        )
        prompt = "\n\n".join(prompt_parts)

        tools = self.sm_app.generate_tool_schemas()
        tool_name, args = self.model.sample_tool_call(prompt, tools)
        if not tool_name:
            return f"Tool call returned no function name for {active_entity}."

        payload = dict(args or {})
        if "current_user" not in payload:
            payload["current_user"] = active_entity

        return self.sm_app.invoke_action_with_kwargs(tool_name, payload)
