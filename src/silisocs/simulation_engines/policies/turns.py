"""Built-in Engine turn policies."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Literal

from silisocs.runtime.types import ActionOutput, OutputType

ObserveBeforeAct = Literal["first", "always", "never"]

_FINISH_ACTION_ALIASES = {
    "FINISHED",
    "FINISH",
    "FINISH_ACTION_EPISODE",
}


def _normalize_observe_before_act(value: str) -> ObserveBeforeAct:
    normalized = str(value or "first").strip().lower()
    if normalized not in {"first", "always", "never"}:
        raise ValueError("observe_before_act must be one of: first, always, never.")
    return normalized  # type: ignore[return-value]


def _should_observe(mode: ObserveBeforeAct, *, action_index: int) -> bool:
    if mode == "always":
        return True
    if mode == "never":
        return False
    return action_index == 0


def _extract_structured_action_names(raw_action: ActionOutput | str) -> list[str]:
    if isinstance(raw_action, ActionOutput) and raw_action.output_type == OutputType.TOOL_CALLS:
        return [call.name for call in raw_action.tool_calls]
    text = (
        raw_action.text.strip()
        if isinstance(raw_action, ActionOutput)
        else str(raw_action or "").strip()
    )
    if not text:
        return []

    action_type_match = re.search(r"(?im)^\s*ACTION TYPE\s*:\s*(.+?)\s*$", text)
    if action_type_match:
        return [action_type_match.group(1).strip()]

    action_match = re.search(r"(?im)^\s*ACTION\s*:\s*(.+?)\s*$", text)
    if action_match:
        return [action_match.group(1).strip()]

    return []


def _count_structured_actions(raw_action: ActionOutput | str) -> int:
    if isinstance(raw_action, ActionOutput):
        if raw_action.output_type == OutputType.TOOL_CALLS:
            return max(1, len(raw_action.tool_calls))
        if raw_action.output_type == OutputType.SKIP:
            return 0
        return 1 if raw_action.text.strip() else 0
    return 1 if str(raw_action or "").strip() else 0


def _is_finished_event(
    *, raw_action: ActionOutput | str, resolved_result: str, finished_signal: str
) -> bool:
    signal = str(finished_signal or "").strip().upper() or "FINISHED"
    aliases = set(_FINISH_ACTION_ALIASES)
    aliases.add(signal)

    for action_name in _extract_structured_action_names(raw_action):
        if action_name.strip().upper() in aliases:
            return True

    raw_upper = str(raw_action or "").strip().upper()
    if raw_upper in aliases:
        return True

    resolved_lines = [line.strip().upper() for line in str(resolved_result or "").splitlines()]
    for line in resolved_lines:
        if line.startswith("FINISHED ACTION EPISODE"):
            return True
        if line.startswith("FINISHED:"):
            return True

    return False


@dataclass
class SingleActionTurnPolicy:
    """Default policy: one action per active agent per step."""

    observe_before_act: str = "first"
    name: str = "single_action"

    def run(
        self,
        *,
        engine: Any,
        game_master: Any,
        agent: Any,
        action_spec: Any,
        verbose: bool,
    ) -> str:
        mode = _normalize_observe_before_act(self.observe_before_act)
        return engine.run_agent_step(
            game_master=game_master,
            agent=agent,
            action_spec=action_spec,
            verbose=verbose,
            observe_before_action=_should_observe(mode, action_index=0),
        ).rendered_action


@dataclass
class FixedCountTurnPolicy:
    """Execute exactly N actions per active agent each step."""

    count: int = 2
    observe_before_act: str = "first"
    name: str = "fixed_count"

    def run(
        self,
        *,
        engine: Any,
        game_master: Any,
        agent: Any,
        action_spec: Any,
        verbose: bool,
    ) -> str:
        last_action = ""
        remaining_actions = max(1, self.count)
        action_index = 0
        mode = _normalize_observe_before_act(self.observe_before_act)

        while remaining_actions > 0:
            action_result = engine.run_agent_step(
                game_master=game_master,
                agent=agent,
                action_spec=action_spec,
                verbose=verbose,
                observe_before_action=_should_observe(mode, action_index=action_index),
            )
            raw_action = action_result.raw_action
            rendered_action = action_result.rendered_action

            action = rendered_action or raw_action
            if action:
                last_action = action
            else:
                break

            consumed = _count_structured_actions(raw_action or action)
            remaining_actions -= max(1, consumed)
            action_index += 1
        return last_action


@dataclass
class OpenEndedTurnPolicy:
    """Execute actions until the agent emits a terminal action or max cap is reached."""

    max_actions: int = 3
    finished_action_signal: str = "FINISHED"
    observe_before_act: str = "first"
    name: str = "open_ended"

    def run(
        self,
        *,
        engine: Any,
        game_master: Any,
        agent: Any,
        action_spec: Any,
        verbose: bool,
    ) -> str:
        last_action = ""
        max_actions = max(1, self.max_actions)
        finished_signal = self.finished_action_signal.strip().upper()
        mode = _normalize_observe_before_act(self.observe_before_act)

        actions_used = 0
        action_index = 0
        while actions_used < max_actions:
            action_result = engine.run_agent_step(
                game_master=game_master,
                agent=agent,
                action_spec=action_spec,
                verbose=verbose,
                observe_before_action=_should_observe(mode, action_index=action_index),
            )

            raw_action = action_result.raw_action
            rendered_action = action_result.rendered_action
            resolved_result = action_result.resolved_result
            action = rendered_action or raw_action

            if not action:
                break

            actions_used += max(1, _count_structured_actions(raw_action or action))
            action_index += 1

            if _is_finished_event(
                raw_action=raw_action,
                resolved_result=resolved_result,
                finished_signal=finished_signal,
            ):
                break

            last_action = action

        return last_action
