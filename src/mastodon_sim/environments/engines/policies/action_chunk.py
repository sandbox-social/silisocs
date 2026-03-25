"""Engine action-loop policies.

These policies control how many actions an entity may execute per engine step.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

_FINISH_ACTION_ALIASES = {
    "FINISHED",
    "FINISH",
    "FINISH_ACTION_EPISODE",
}


def _extract_structured_action_name(raw_action: str) -> str:
    text = str(raw_action or "").strip()
    if not text:
        return ""

    # Tool-calling mode payload.
    try:
        payload = json.loads(text)
    except (TypeError, ValueError, json.JSONDecodeError):
        payload = None

    if isinstance(payload, dict):
        tool_call = payload.get("tool_call")
        if isinstance(tool_call, dict):
            name = tool_call.get("name")
            if isinstance(name, str):
                return name.strip()

        for key in ("action_type", "action", "name"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()

    # Parsed-action mode payload.
    action_type_match = re.search(r"(?im)^\s*ACTION TYPE\s*:\s*(.+?)\s*$", text)
    if action_type_match:
        return action_type_match.group(1).strip()

    # Generic-action mode payload.
    action_match = re.search(r"(?im)^\s*ACTION\s*:\s*(.+?)\s*$", text)
    if action_match:
        return action_match.group(1).strip()

    return ""


def _is_finished_event(*, raw_action: str, resolved_result: str, finished_signal: str) -> bool:
    signal = str(finished_signal or "").strip().upper() or "FINISHED"
    aliases = set(_FINISH_ACTION_ALIASES)
    aliases.add(signal)

    action_name = _extract_structured_action_name(raw_action)
    if action_name and action_name.strip().upper() in aliases:
        return True

    # Legacy fallback: allow exact token equality only (no substring matching).
    raw_upper = str(raw_action or "").strip().upper()
    if raw_upper in aliases:
        return True

    # Backend terminal action result text.
    resolved_upper = str(resolved_result or "").strip().upper()
    if resolved_upper.startswith("FINISHED ACTION EPISODE"):
        return True
    if resolved_upper.startswith("FINISHED:"):
        return True

    return False


@dataclass
class SingleActionChunkPolicy:
    """Default policy: one action per active entity per step."""

    name: str = "single_action"

    def run(
        self,
        *,
        engine: Any,
        game_master: Any,
        entity: Any,
        action_spec: Any,
        skip_actions: bool,
        verbose: bool,
    ) -> str:
        """Execute a single observe -> act -> resolve cycle."""
        return engine._run_single_entity_action(
            game_master=game_master,
            entity=entity,
            action_spec=action_spec,
            skip_actions=skip_actions,
            verbose=verbose,
        )


@dataclass
class FixedCountActionChunkPolicy:
    """Execute exactly N actions per active entity each step."""

    count: int = 2
    name: str = "fixed_count"

    def run(
        self,
        *,
        engine: Any,
        game_master: Any,
        entity: Any,
        action_spec: Any,
        skip_actions: bool,
        verbose: bool,
    ) -> str:
        """Execute a fixed number of actions, reusing the same action spec."""
        if skip_actions:
            return ""

        last_action = ""
        for _ in range(max(1, self.count)):
            action = engine._run_single_entity_action(
                game_master=game_master,
                entity=entity,
                action_spec=action_spec,
                skip_actions=False,
                verbose=verbose,
                observe_before_action=not bool(last_action),
            )
            if action:
                last_action = action
        return last_action


@dataclass
class OpenEndedActionChunkPolicy:
    """Execute actions until agent emits a 'Finished' action or max cap is reached.

    In open-ended mode, the agent can take multiple sequential actions within a single
    engine step. To support this, a special "Finished action episode" action is injected
    into the LLM's action prompts, allowing the agent to signal when it has concluded
    all desired actions for this cycle.

    When the agent outputs a Finished action, the policy stops iteration and returns
    to the engine loop. Otherwise, it continues up to max_actions iterations.
    """

    max_actions: int = 3
    finished_action_signal: str = "FINISHED"
    done_token: str | None = None
    name: str = "open_ended"

    def __post_init__(self) -> None:
        # Backward compatibility for existing config/dashboard payloads.
        if self.done_token:
            self.finished_action_signal = str(self.done_token)

    def run(
        self,
        *,
        engine: Any,
        game_master: Any,
        entity: Any,
        action_spec: Any,
        skip_actions: bool,
        verbose: bool,
    ) -> str:
        """Execute repeated actions with explicit agent-controlled stop support.

        The agent can output any valid action or the special instruction to finish
        the action chunk. When "Finished action episode" is detected, iteration stops.

        Returns
        -------
            str: The last executed action before termination (or empty string if skipped).
        """
        if skip_actions:
            return ""

        last_action = ""
        max_actions = max(1, self.max_actions)
        finished_signal = self.finished_action_signal.strip().upper()

        for _ in range(max_actions):
            action_result = engine._run_single_entity_action(
                game_master=game_master,
                entity=entity,
                action_spec=action_spec,
                skip_actions=False,
                verbose=verbose,
                observe_before_action=not bool(last_action),
                return_raw_action=True,
            )

            raw_action = ""
            rendered_action = ""
            resolved_result = ""

            if isinstance(action_result, dict):
                raw_action = str(action_result.get("raw", "") or "")
                rendered_action = str(action_result.get("rendered", "") or "")
                resolved_result = str(action_result.get("resolved", "") or "")
            else:
                rendered_action = str(action_result or "")
                raw_action = rendered_action

            action = rendered_action or raw_action

            if not action:
                break

            # Stop only on explicit terminal actions/results.
            if _is_finished_event(
                raw_action=raw_action,
                resolved_result=resolved_result,
                finished_signal=finished_signal,
            ):
                break

            last_action = action

        return last_action
