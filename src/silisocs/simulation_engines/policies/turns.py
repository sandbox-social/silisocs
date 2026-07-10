"""Built-in Engine turn policies.

Each policy states its per-turn logic ONCE as a ``_drive`` generator that yields
``observe_before_action`` for each agent step and receives the resulting
:class:`AgentStepResult`. Two mechanical drivers execute that generator against
the engine: ``run`` (sync, via ``engine.run_agent_step``) and ``run_async``
(via ``engine.run_agent_step_async``), so the policies behave identically under
the threaded and asyncio turn executors. ``run_async`` is optional on the
``TurnPolicy`` seam — the engine runs a custom sync-only policy on a helper
thread under the asyncio executor.
"""

from __future__ import annotations

import re
from collections.abc import Generator
from dataclasses import dataclass
from typing import Any, Literal

from silisocs.runtime.types import ActionOutput, OutputType

# One policy drive: yields observe_before_action per agent step, receives the
# engine's AgentStepResult, and returns the policy's final action.
_PolicyDrive = Generator[bool, Any, Any]


def _drive_policy(
    drive: _PolicyDrive,
    *,
    engine: Any,
    game_master: Any,
    agent: Any,
    action_spec: Any,
    verbose: bool,
) -> Any:
    """Execute one policy drive synchronously via ``engine.run_agent_step``."""
    try:
        observe = next(drive)
        while True:
            result = engine.run_agent_step(
                game_master=game_master,
                agent=agent,
                action_spec=action_spec,
                verbose=verbose,
                observe_before_action=observe,
            )
            observe = drive.send(result)
    except StopIteration as stop:
        return stop.value


async def _drive_policy_async(
    drive: _PolicyDrive,
    *,
    engine: Any,
    game_master: Any,
    agent: Any,
    action_spec: Any,
    verbose: bool,
) -> Any:
    """Execute one policy drive on the event loop via ``engine.run_agent_step_async``."""
    try:
        observe = next(drive)
        while True:
            result = await engine.run_agent_step_async(
                game_master=game_master,
                agent=agent,
                action_spec=action_spec,
                verbose=verbose,
                observe_before_action=observe,
            )
            observe = drive.send(result)
    except StopIteration as stop:
        return stop.value


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


class _DrivenTurnPolicy:
    """Shared sync/async drivers over a policy's ``_drive`` generator."""

    def _drive(self) -> _PolicyDrive:
        raise NotImplementedError

    def run(
        self,
        *,
        engine: Any,
        game_master: Any,
        agent: Any,
        action_spec: Any,
        verbose: bool,
    ) -> str:
        return _drive_policy(
            self._drive(),
            engine=engine,
            game_master=game_master,
            agent=agent,
            action_spec=action_spec,
            verbose=verbose,
        )

    async def run_async(
        self,
        *,
        engine: Any,
        game_master: Any,
        agent: Any,
        action_spec: Any,
        verbose: bool,
    ) -> str:
        return await _drive_policy_async(
            self._drive(),
            engine=engine,
            game_master=game_master,
            agent=agent,
            action_spec=action_spec,
            verbose=verbose,
        )


@dataclass
class SingleActionTurnPolicy(_DrivenTurnPolicy):
    """Default policy: one action per active agent per step."""

    observe_before_act: str = "first"
    name: str = "single_action"

    def _drive(self) -> _PolicyDrive:
        mode = _normalize_observe_before_act(self.observe_before_act)
        result = yield _should_observe(mode, action_index=0)
        return result.rendered_action


@dataclass
class FixedCountTurnPolicy(_DrivenTurnPolicy):
    """Execute exactly N actions per active agent each step.

    By default the budget counts EMITTED actions: every action the agent produces
    consumes from ``count`` whether or not it committed a backend change. Set
    ``count_committed: true`` to count only actions that COMMITTED (validated +
    executed) — a tool call that fails resolve (bad args, unknown action, execution
    error, flow-filtered) no longer burns the budget, so the agent gets ``count``
    real actions. ``max_attempts`` bounds the retry loop in that mode (default
    ``2 * count``) so an agent that keeps emitting invalid actions can't loop
    forever. Both are no-ops when ``count_committed`` is false (identical to before).
    """

    count: int = 2
    observe_before_act: str = "first"
    count_committed: bool = False
    max_attempts: int = 0
    name: str = "fixed_count"

    def _drive(self) -> _PolicyDrive:
        last_action = ""
        remaining_actions = max(1, self.count)
        mode = _normalize_observe_before_act(self.observe_before_act)
        # In committed mode, cap total attempts so repeated invalid emissions
        # (which never decrement the budget) can't spin forever.
        attempts_left = (
            (self.max_attempts if self.max_attempts > 0 else 2 * max(1, self.count))
            if self.count_committed
            else -1  # unbounded: emitted-counting always makes progress
        )
        action_index = 0

        while remaining_actions > 0 and attempts_left != 0:
            action_result = yield _should_observe(mode, action_index=action_index)
            raw_action = action_result.raw_action
            rendered_action = action_result.rendered_action

            action = rendered_action or raw_action
            if action:
                last_action = action
            else:
                break

            emitted = max(1, _count_structured_actions(raw_action or action))
            if self.count_committed:
                # Prefer the resolve report's committed count; fall back to emitted
                # when the resolver can't report per-call outcomes (committed is None).
                committed = getattr(action_result.resolved_result, "committed", None)
                remaining_actions -= emitted if committed is None else committed
                attempts_left -= 1
            else:
                remaining_actions -= emitted
            action_index += 1
        return last_action


@dataclass
class OpenEndedTurnPolicy(_DrivenTurnPolicy):
    """Execute actions until the agent emits a terminal action or max cap is reached."""

    max_actions: int = 3
    finished_action_signal: str = "FINISHED"
    observe_before_act: str = "first"
    name: str = "open_ended"

    def _drive(self) -> _PolicyDrive:
        last_action = ""
        max_actions = max(1, self.max_actions)
        finished_signal = self.finished_action_signal.strip().upper()
        mode = _normalize_observe_before_act(self.observe_before_act)

        actions_used = 0
        action_index = 0
        while actions_used < max_actions:
            action_result = yield _should_observe(mode, action_index=action_index)

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
