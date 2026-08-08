"""The Tool Bridge: exposes a GM backend's action catalog to a harness agent.

A :class:`ToolSurface` is built per turn from the acting agent's GM backend. It is the
single, backend-agnostic seam every harness adapter consumes. It adds no filtering of its
own — the backend already owns that: :meth:`schemas` returns
``backend.generate_tool_schemas()`` (already restricted to the backend's
``enabled_actions``/``excluded_actions``), and :meth:`execute` forwards to
``backend.invoke_action_with_kwargs`` (which validates the call, rejects unknown/excluded
actions, and logs the action event just like a native turn). The surface only adds the
harness concerns: injecting the runtime-owned actor argument (via
``backend.action_accepts_param``, shared with the native resolve components), turning
exceptions into results the harness loop reacts to instead of crashing, and recording each
call to ``harness_events.jsonl``.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from silisocs.agents.harness.types import ExecutedToolCall
from silisocs.runtime.telemetry.collector import SimMetricsCollector
from silisocs.runtime.types import RUNTIME_AGENT_PARAM, RUNTIME_OWNED_ACTION_PARAMS

HARNESS_TOOL_FAILURES_COUNTER = "harness_tool_failures"

# harness_events.jsonl row discriminators (the EventLogger fixes event_type="harness").
EVENT_TOOL_EXECUTED = "tool_executed"
EVENT_TOOL_FAILED = "tool_failed"
EVENT_TURN_COMPLETED = "turn_completed"
EVENT_PROBE_ANSWERED = "probe_answered"

_RESERVED_ACTOR_ARGS = RUNTIME_OWNED_ACTION_PARAMS
_RESULT_PREVIEW_CHARS = 500


def _preview(text: str) -> str:
    text = str(text or "")
    return text if len(text) <= _RESULT_PREVIEW_CHARS else text[:_RESULT_PREVIEW_CHARS] + "…"


class ToolSurface:
    """Per-turn view of a backend's action catalog for one harness agent."""

    def __init__(
        self,
        *,
        backend: Any,
        agent_name: str,
        harness_logger: Any = None,
    ) -> None:
        self._backend = backend
        self._agent_name = str(agent_name)
        self._harness_logger = harness_logger
        self._executed: list[ExecutedToolCall] = []

    @property
    def agent_name(self) -> str:
        return self._agent_name

    @property
    def executed(self) -> tuple[ExecutedToolCall, ...]:
        """Every tool call run through this surface so far this turn, in order."""
        return tuple(self._executed)

    # ------------------------------------------------------------------ #
    # Discovery
    # ------------------------------------------------------------------ #

    def schemas(self) -> list[dict[str, Any]]:
        """Return the OpenAI-compatible tool schemas this agent may call.

        Already restricted to the backend's ``enabled_actions``/``excluded_actions`` —
        the surface adds no filtering of its own.
        """
        generate = getattr(self._backend, "generate_tool_schemas", None)
        return list(generate() or []) if callable(generate) else []

    def tool_names(self) -> list[str]:
        """Return just the callable action names (schema name field)."""
        return [
            str(((schema or {}).get("function") or {}).get("name") or "").strip()
            for schema in self.schemas()
        ]

    # ------------------------------------------------------------------ #
    # Execution
    # ------------------------------------------------------------------ #

    def execute(self, name: str, arguments: Mapping[str, Any] | None = None) -> ExecutedToolCall:
        """Validate and run one tool call, returning the result for the harness loop."""
        call = self._execute(str(name or "").strip(), dict(arguments or {}))
        self._executed.append(call)
        self._log_event(call)
        return call

    def _execute(self, name: str, arguments: dict[str, Any]) -> ExecutedToolCall:
        if not name:
            return ExecutedToolCall(
                name=name,
                arguments=arguments,
                ok=False,
                error="empty-name",
                result="No action name was provided.",
            )

        reserved = sorted(set(arguments) & _RESERVED_ACTOR_ARGS)
        if reserved:
            return ExecutedToolCall(
                name=name,
                arguments=arguments,
                ok=False,
                error="reserved-argument",
                result=(
                    "These arguments are managed by the platform and must not be supplied: "
                    + ", ".join(reserved)
                ),
            )

        payload = dict(arguments)
        if self._backend.action_accepts_param(name, RUNTIME_AGENT_PARAM):
            payload[RUNTIME_AGENT_PARAM] = self._agent_name

        try:
            result = str(self._backend.invoke_action_with_kwargs(name, payload))
        except Exception as exc:  # defensive: backends normally return error strings
            SimMetricsCollector.get().increment_counter(HARNESS_TOOL_FAILURES_COUNTER)
            return ExecutedToolCall(
                name=name,
                arguments=arguments,
                ok=False,
                error=str(exc),
                result=f"Action '{name}' could not be completed: {exc}",
            )

        return ExecutedToolCall(name=name, arguments=arguments, ok=True, result=result)

    # ------------------------------------------------------------------ #
    # Telemetry
    # ------------------------------------------------------------------ #

    def _log_event(self, call: ExecutedToolCall) -> None:
        log = getattr(self._harness_logger, "log", None)
        if not callable(log):
            return
        log(
            {
                "kind": EVENT_TOOL_EXECUTED if call.ok else EVENT_TOOL_FAILED,
                "agent_name": self._agent_name,
                "action": call.name,
                "arguments": dict(call.arguments),
                "ok": call.ok,
                "error": call.error,
                "result_preview": _preview(call.result),
            }
        )
