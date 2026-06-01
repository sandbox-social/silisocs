"""Native resolve components for social-media game masters."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from silisocs.environments.backends.base import (
    LEGACY_RUNTIME_AGENT_PARAMS,
    RUNTIME_AGENT_PARAM,
)
from silisocs.environments.gm.components.base import (
    ComponentState,
    ResolveComponent,
)
from silisocs.runtime.types import ActionOutput, OutputType

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
_RUNTIME_ARG_PATTERN = re.compile(
    r"(?im)^\s*(?:"
    + "|".join(
        re.escape(name) for name in sorted({RUNTIME_AGENT_PARAM, *LEGACY_RUNTIME_AGENT_PARAMS})
    )
    + r")\s*:"
)


def _normalize_target_id(action_type: str, target_id: str) -> str:
    """_normalize_target_id.

    :param str action_type:
    :type action_type: str
    :param str target_id:
    :type target_id: str

    :returns: str
    :rtype: str
    """
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


@dataclass(eq=False)
class _BaseResolveComponent(ResolveComponent):
    """Base class for resolve components used by native game masters."""

    backend: Any
    action_prompt_template: str = ""
    model: Any = None

    def resolve_action(self, agent_name: str, action: ActionOutput | str) -> str:
        """Resolve raw action text for one agent."""
        return self.resolve(active_agent=agent_name, action=action)

    def _action_has_parameter(self, action_name: str, parameter_name: str) -> bool:
        """Return whether an app action accepts the named parameter."""
        actions = getattr(self.backend, "actions", None)
        if not callable(actions):
            return False
        for action in actions():
            if action_name not in {action.name, action.selectable_name}:
                continue
            return any(param.name == parameter_name for param in action.parameters)
        return False

    def resolve(self, *, active_agent: str, action: ActionOutput | str) -> str:
        """Resolve raw action text into backend operation result."""
        raise NotImplementedError

    def get_state(self) -> ComponentState:
        """Return serializable component state."""
        return {}

    def set_state(self, state: ComponentState) -> None:
        """Restore component state."""
        del state


@dataclass(eq=False)
class ParsedActionResolveComponent(_BaseResolveComponent):
    """Resolve using ACTION TYPE/TARGET ID/CONTENT parser output."""

    def resolve(self, *, active_agent: str, action: ActionOutput | str) -> str:
        """Resolve.

        :returns: str
        :rtype: str
        """
        action_text = action.text if isinstance(action, ActionOutput) else str(action)
        action_data = find_and_parse_action_data(action_text)
        if action_data is None:
            return ""
        return self.backend.parse_and_resolve_action(active_agent, action_data)


@dataclass(eq=False)
class GenericActionResolveComponent(_BaseResolveComponent):
    """Resolve generic ACTION: name / param: value format."""

    def resolve(self, *, active_agent: str, action: ActionOutput | str) -> str:
        """Resolve.

        :returns: str
        :rtype: str
        """
        action_text = action.text if isinstance(action, ActionOutput) else str(action)
        action_match = re.search(r"(?i)ACTION:\s*(\w+)", action_text)
        if not action_match:
            return ""
        action_name = action_match.group(1).strip()
        args_text = action_text[action_match.end() :].strip()
        if _RUNTIME_ARG_PATTERN.search(args_text):
            raise ValueError(
                "Agent action output must not include runtime-owned actor arguments "
                f"({RUNTIME_AGENT_PARAM} or legacy current_user)."
            )
        if self._action_has_parameter(action_name, RUNTIME_AGENT_PARAM):
            args_text = f"{RUNTIME_AGENT_PARAM}: {active_agent}" + (
                f"\n{args_text}" if args_text else ""
            )
        return self.backend.invoke_action_by_name(action_name, args_text) or ""


@dataclass(eq=False)
class ToolCallingResolveComponent(_BaseResolveComponent):
    """Resolve typed tool-call invocations from an agent action output."""

    def resolve(self, *, active_agent: str, action: ActionOutput | str) -> str:
        """Handle typed tool calls from an agent."""
        if not isinstance(action, ActionOutput) or action.output_type != OutputType.TOOL_CALLS:
            raise TypeError("ToolCallingResolveComponent requires ActionOutput.TOOL_CALLS.")
        tool_calls = [(call.name, dict(call.arguments)) for call in action.tool_calls]
        if tool_calls:
            normalized_calls: list[tuple[str, dict[str, Any]]] = []
            for tool_name, payload in tool_calls:
                payload = dict(payload)
                provided_actor_args = sorted(
                    set(payload) & ({RUNTIME_AGENT_PARAM} | set(LEGACY_RUNTIME_AGENT_PARAMS))
                )
                if provided_actor_args:
                    raise ValueError(
                        "Agent tool calls must not include runtime-owned actor arguments: "
                        + ", ".join(provided_actor_args)
                    )
                if self._action_has_parameter(tool_name, RUNTIME_AGENT_PARAM):
                    payload[RUNTIME_AGENT_PARAM] = active_agent
                normalized_calls.append((tool_name, payload))
            results = [
                str(self.backend.invoke_action_with_kwargs(tool_name, payload))
                for tool_name, payload in normalized_calls
            ]
            return "\n".join(result for result in results if result)

        raise ValueError("TOOL_CALLS action output must include at least one tool call.")
