"""Native resolve components for social-media game masters."""

from __future__ import annotations

import logging
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from omegaconf import DictConfig, ListConfig, OmegaConf

from silisocs.environments.backends.base import (
    LEGACY_RUNTIME_AGENT_PARAMS,
    RUNTIME_AGENT_PARAM,
)
from silisocs.environments.gm.components.base import (
    ComponentState,
    ResolveComponent,
)
from silisocs.runtime.telemetry.collector import SimMetricsCollector
from silisocs.runtime.types import ActionOutput, OutputType

_LOGGER = logging.getLogger(__name__)

PARSE_FAILURE_COUNTER = "action_parse_failures"
INVALID_TARGET_COUNTER = "action_invalid_targets"
ACTION_FLOW_FILTERED_COUNTER = "action_flow_filtered"

DEFAULT_FLOW_TAG = "default"

# Outputs that are legitimate non-actions (turn-policy signals), not parse failures.
_EXPECTED_NON_ACTION_OUTPUTS = {
    "FINISHED",
    "FINISH",
    "FINISH_ACTION_EPISODE",
    "FINISHED ACTION EPISODE",
}

# Terminal turn-policy signals are never blocked by per-flow action filters, so an
# open-ended turn policy can always terminate even for a restricted flow.
_FINISH_TOKENS = {token.strip().lower() for token in _EXPECTED_NON_ACTION_OUTPUTS}


def _to_plain(value: Any) -> Any:
    """Materialize OmegaConf containers into plain Python structures."""
    if isinstance(value, (DictConfig, ListConfig)):
        return OmegaConf.to_container(value, resolve=True)
    return value


def _merge_overlapping_groups(groups: list[set[str]]) -> list[set[str]]:
    """Union-merge token groups that share any member.

    e.g. ``[{create_tweet, post}, {create_tweet}]`` -> ``[{create_tweet, post}]``.
    """
    merged: list[set[str]] = []
    for group in groups:
        current = set(group)
        overlapping = [existing for existing in merged if existing & current]
        for existing in overlapping:
            merged.remove(existing)
            current |= existing
        merged.append(current)
    return merged


def _record_parse_failure(agent_name: str, action_text: str, reason: str) -> None:
    """Surface a dropped agent action instead of failing silently."""
    normalized = action_text.strip().upper().rstrip(".!")
    if not normalized or normalized in _EXPECTED_NON_ACTION_OUTPUTS:
        return
    SimMetricsCollector.get().increment_counter(PARSE_FAILURE_COUNTER)
    snippet = action_text.strip().replace("\n", " | ")
    if len(snippet) > 300:
        snippet = snippet[:300] + "…"
    _LOGGER.warning(
        "Dropped action from agent '%s' (%s). Raw output: %s",
        agent_name,
        reason,
        snippet,
    )


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
    """Base class for resolve components used by native game masters.

    ``flow_action_filters`` is an optional ``{flow_tag: {enabled_actions,
    excluded_actions}}`` map that further-restricts (never widens) the actions an
    agent may execute, keyed by the agent's flow. ``agent_flow_tags`` supplies the
    agent -> flow mapping used to look up the right filter at resolve time. When no
    filter applies to an agent's flow the resolve path is a pure pass-through, so
    omitting both keys preserves current behavior exactly.
    """

    backend: Any
    action_prompt_template: str = ""
    model: Any = None
    flow_action_filters: Any = None
    agent_flow_tags: Any = None

    def __post_init__(self) -> None:
        self._flow_tags: dict[str, str] = {
            str(name): str(flow)
            for name, flow in dict(_to_plain(self.agent_flow_tags) or {}).items()
        }
        self._alias_index: dict[str, frozenset[str]] = {}
        self._flow_filters: dict[str, tuple[frozenset[str] | None, frozenset[str]]] = {}
        if not self.flow_action_filters:
            return
        self._alias_index = self._build_descriptor_alias_index()
        self._flow_filters = self._build_flow_filters(self.flow_action_filters)

    def resolve_action(self, agent_name: str, action: ActionOutput | str) -> str:
        """Resolve raw action text for one agent."""
        return self.resolve(active_agent=agent_name, action=action)

    # ------------------------------------------------------------------ #
    # Per-flow action filtering
    # ------------------------------------------------------------------ #

    def _build_descriptor_alias_index(self) -> dict[str, frozenset[str]]:
        """Map each lowercased action token to its full synonym group.

        Folds together two sources so a per-flow filter matches regardless of the
        vocabulary the agent emits: (1) each backend descriptor's canonical and
        selectable names, and (2) the backend's declared ``action_aliases()``
        groups, which bridge custom-mode domain verbs (``post``) to canonical
        method names (``create_tweet``). Overlapping groups are union-merged so a
        filter written in either vocabulary always matches.
        """
        groups: list[set[str]] = []
        all_actions = getattr(self.backend, "_all_actions", None)
        if callable(all_actions):
            for descriptor in all_actions():
                group = {
                    str(name).strip().lower()
                    for name in (descriptor.name, descriptor.selectable_name)
                    if str(name).strip()
                }
                if group:
                    groups.append(group)
        action_aliases = getattr(self.backend, "action_aliases", None)
        if callable(action_aliases):
            for raw_group in action_aliases():
                group = {str(name).strip().lower() for name in raw_group if str(name).strip()}
                if group:
                    groups.append(group)
        index: dict[str, frozenset[str]] = {}
        for merged in _merge_overlapping_groups(groups):
            frozen = frozenset(merged)
            for token in merged:
                index[token] = frozen
        return index

    def _token_aliases(self, token: str) -> frozenset[str]:
        return self._alias_index.get(token, frozenset({token}))

    def _expand_filter_names(self, raw_names: Any, unknown: set[str]) -> frozenset[str] | None:
        if raw_names is None:
            return None
        raw_names = _to_plain(raw_names)
        if isinstance(raw_names, str):
            raw_names = [raw_names]
        tokens: set[str] = set()
        for name in raw_names:
            norm = str(name).strip().lower()
            if not norm:
                continue
            group = self._alias_index.get(norm)
            if group is None:
                unknown.add(str(name).strip())
                tokens.add(norm)
            else:
                tokens |= group
        return frozenset(tokens)

    def _build_flow_filters(
        self, raw: Any
    ) -> dict[str, tuple[frozenset[str] | None, frozenset[str]]]:
        raw = _to_plain(raw)
        if not isinstance(raw, Mapping):
            raise ValueError(
                "flow_action_filters must be a mapping of "
                "flow_tag -> {enabled_actions, excluded_actions}."
            )
        unknown: set[str] = set()
        filters: dict[str, tuple[frozenset[str] | None, frozenset[str]]] = {}
        for flow, spec in raw.items():
            flow_name = str(flow).strip()
            if not flow_name:
                raise ValueError("flow_action_filters contains an empty flow name.")
            spec = _to_plain(spec) or {}
            if not isinstance(spec, Mapping):
                raise ValueError(
                    f"flow_action_filters['{flow_name}'] must be a mapping of "
                    "{enabled_actions, excluded_actions}."
                )
            enabled = self._expand_filter_names(spec.get("enabled_actions"), unknown)
            excluded = (
                self._expand_filter_names(spec.get("excluded_actions"), unknown) or frozenset()
            )
            filters[flow_name] = (enabled, excluded)
        if unknown:
            _LOGGER.warning(
                "flow_action_filters reference action name(s) not declared on backend %s: %s. "
                "Custom-mode verbs (e.g. 'post'/'like') are matched literally; check for typos.",
                type(self.backend).__name__,
                sorted(unknown),
            )
        return filters

    def _filter_for_agent(
        self, agent_name: str
    ) -> tuple[frozenset[str] | None, frozenset[str]] | None:
        flow = self._flow_tags.get(str(agent_name), DEFAULT_FLOW_TAG)
        spec = self._flow_filters.get(flow)
        if spec is None:
            spec = self._flow_filters.get(DEFAULT_FLOW_TAG)
        return spec

    def _action_allowed(self, agent_name: str, action_name: str) -> bool:
        """Return whether ``agent_name``'s flow permits ``action_name``."""
        if not self._flow_filters:
            return True
        spec = self._filter_for_agent(agent_name)
        if spec is None:
            return True
        enabled, excluded = spec
        norm = str(action_name).strip().lower()
        if not norm or norm in _FINISH_TOKENS:
            return True
        tokens = self._token_aliases(norm)
        if enabled is not None and tokens.isdisjoint(enabled):
            return False
        return not (tokens & excluded)

    def _reject_filtered_action(self, agent_name: str, action_name: str) -> str:
        SimMetricsCollector.get().increment_counter(ACTION_FLOW_FILTERED_COUNTER)
        flow = self._flow_tags.get(str(agent_name), DEFAULT_FLOW_TAG)
        _LOGGER.warning(
            "Agent '%s' (flow '%s') attempted disallowed action '%s'; blocked by per-flow filter.",
            agent_name,
            flow,
            action_name,
        )
        return (
            f"Action '{action_name}' is not permitted for your role in this scenario. "
            "Choose a different action from your available options."
        )

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
        """Resolve raw action text into a backend operation result."""
        action_text = action.text if isinstance(action, ActionOutput) else str(action)
        action_data = find_and_parse_action_data(action_text)
        if action_data is None:
            _record_parse_failure(active_agent, action_text, "no ACTION TYPE block found")
            return ""
        action_type = action_data["action_type"].strip().lower()
        if not self._action_allowed(active_agent, action_type):
            return self._reject_filtered_action(active_agent, action_type)
        target_id = action_data["target_id"]
        if action_type in _TARGET_REQUIRED_ACTIONS and not target_id.isdigit():
            SimMetricsCollector.get().increment_counter(INVALID_TARGET_COUNTER)
            _LOGGER.warning(
                "Agent '%s' action '%s' has invalid TARGET ID %r (numeric post id required).",
                active_agent,
                action_type,
                target_id,
            )
            return (
                f"Could not perform '{action_type}': TARGET ID {target_id!r} is not a "
                "valid numeric post id. Use a post id from the timeline."
            )
        return self.backend.parse_and_resolve_action(active_agent, action_data)


@dataclass(eq=False)
class GenericActionResolveComponent(_BaseResolveComponent):
    """Resolve generic ACTION: name / param: value format."""

    def resolve(self, *, active_agent: str, action: ActionOutput | str) -> str:
        """Resolve generic action text into a backend operation result."""
        action_text = action.text if isinstance(action, ActionOutput) else str(action)
        action_match = re.search(r"(?i)ACTION:\s*(\w+)", action_text)
        if not action_match:
            _record_parse_failure(active_agent, action_text, "no ACTION: line found")
            return ""
        action_name = action_match.group(1).strip()
        if not self._action_allowed(active_agent, action_name):
            return self._reject_filtered_action(active_agent, action_name)
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
        if not tool_calls:
            raise ValueError("TOOL_CALLS action output must include at least one tool call.")

        # Two passes (order-preserving): validate every call first — raising on a
        # forbidden actor arg BEFORE any backend side effect — then dispatch the
        # allowed calls. This keeps a multi-tool batch atomic, so a forbidden call
        # later in the batch cannot leave earlier calls partially committed.
        plans: list[tuple[str, Any]] = []  # ("reject", message) | ("call", (name, payload))
        for tool_name, payload in tool_calls:
            if not self._action_allowed(active_agent, tool_name):
                plans.append(("reject", self._reject_filtered_action(active_agent, tool_name)))
                continue
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
            plans.append(("call", (tool_name, payload)))

        results: list[str] = []
        for kind, value in plans:
            if kind == "reject":
                results.append(str(value))
            else:
                tool_name, payload = value
                results.append(str(self.backend.invoke_action_with_kwargs(tool_name, payload)))
        return "\n".join(result for result in results if result)
