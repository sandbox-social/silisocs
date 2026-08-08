"""Native action request and response types for Silisocs runtime orchestration."""

# ruff: noqa: D105,UP042

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class OutputType(str, Enum):
    """Agent-facing output categories."""

    TEXT = "text"
    CHOICE = "choice"
    FLOAT = "float"
    TOOL_CALLS = "tool_calls"
    STRUCTURED = "structured"
    SKIP = "skip"


FREE_ACTION_TYPES = frozenset({OutputType.TEXT})
CHOICE_ACTION_TYPES = frozenset({OutputType.CHOICE})

# Structured-payload key a harness agent uses to hand its completed turn (executed tool
# calls, usage, finished flag) to the ``harness`` resolve component. Lives here as a
# neutral contract constant so neither the agent layer nor the GM component layer needs
# to import the other. See ``silisocs.agents.harness`` and the harness resolve component.
HARNESS_TURN_KEY = "harness_turn"

# The backend-action parameter names the RUNTIME supplies rather than the acting agent:
# the actor argument is injected by whatever executes the call (a GM resolve component or
# a harness Tool Bridge), never chosen by the agent. Neutral contract constants for the
# same reason as ``HARNESS_TURN_KEY``: the backend layer, the GM component layer, and the
# agent layer all speak this convention, so none of them has to import another.
RUNTIME_AGENT_PARAM = "agent_name"
RUNTIME_OWNED_ACTION_PARAMS = frozenset({RUNTIME_AGENT_PARAM})


@dataclass(frozen=True, init=False)
class ActionSpec:
    """Prompt and response contract for one runtime action request."""

    prompt: str
    output_type: OutputType | str
    options: tuple[str, ...] = ()
    tag: str | None = None
    extra_args: Mapping[str, Any] = field(default_factory=dict)

    def __init__(
        self,
        prompt: str | None = None,
        output_type: OutputType | str = OutputType.TEXT,
        options: Iterable[str] = (),
        tag: str | None = None,
        extra_args: Mapping[str, Any] | None = None,
    ) -> None:
        if prompt is None:
            prompt = ""
        object.__setattr__(self, "prompt", str(prompt or ""))
        if not isinstance(output_type, OutputType):
            object.__setattr__(self, "output_type", OutputType(str(output_type)))
        else:
            object.__setattr__(self, "output_type", output_type)
        object.__setattr__(self, "options", tuple(str(option) for option in options))
        object.__setattr__(self, "tag", tag)
        object.__setattr__(self, "extra_args", dict(extra_args or {}))

    def __str__(self) -> str:
        output_value = (
            self.output_type.value
            if isinstance(self.output_type, OutputType)
            else str(self.output_type)
        )
        return f"prompt: {self.prompt} ;;type: {output_value}"


@dataclass(frozen=True)
class ToolCall:
    """A backend action selected by an agent/model."""

    name: str
    arguments: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "name", str(self.name or "").strip())
        object.__setattr__(self, "arguments", dict(self.arguments or {}))
        if not self.name:
            raise ValueError("ToolCall.name cannot be empty.")


@dataclass(frozen=True, init=False)
class ActionOutput:
    """Typed action response returned by agents."""

    output_type: OutputType | str
    _text: str = ""
    _tool_calls: tuple[ToolCall, ...] = ()
    structured: Mapping[str, Any] | None = None
    choice: str | None = None
    number: float | None = None
    raw: Any | None = None

    def __init__(
        self,
        output_type: OutputType | str,
        text: str = "",
        tool_calls: Iterable[ToolCall] = (),
        structured: Mapping[str, Any] | None = None,
        choice: str | None = None,
        number: float | None = None,
        raw: Any | None = None,
    ) -> None:
        if not isinstance(output_type, OutputType):
            output_type = OutputType(str(output_type))
        object.__setattr__(self, "output_type", output_type)
        object.__setattr__(self, "_text", str(text or ""))
        object.__setattr__(self, "_tool_calls", tuple(tool_calls or ()))
        object.__setattr__(self, "structured", dict(structured) if structured is not None else None)
        object.__setattr__(self, "choice", choice)
        object.__setattr__(self, "number", number)
        object.__setattr__(self, "raw", raw)

    @property
    def text(self) -> str:
        """Return the textual representation payload."""
        return self._text

    @property
    def tool_calls(self) -> tuple[ToolCall, ...]:
        """Return typed tool calls carried by this output."""
        return self._tool_calls

    @classmethod
    def from_text(cls, text: str, *, raw: Any | None = None) -> ActionOutput:
        return cls(output_type=OutputType.TEXT, text=str(text or ""), raw=raw)

    @classmethod
    def from_tool_calls(
        cls,
        calls: Iterable[ToolCall | tuple[str, Mapping[str, Any]]],
        *,
        raw: Any | None = None,
    ) -> ActionOutput:
        normalized: list[ToolCall] = []
        for call in calls:
            if isinstance(call, ToolCall):
                normalized.append(call)
            else:
                name, arguments = call
                normalized.append(ToolCall(str(name), dict(arguments or {})))
        return cls(output_type=OutputType.TOOL_CALLS, tool_calls=tuple(normalized), raw=raw)

    @classmethod
    def structured_response(
        cls,
        payload: Mapping[str, Any],
        *,
        raw: Any | None = None,
    ) -> ActionOutput:
        return cls(output_type=OutputType.STRUCTURED, structured=dict(payload), raw=raw)

    @classmethod
    def choice_response(cls, choice: str, *, raw: Any | None = None) -> ActionOutput:
        return cls(output_type=OutputType.CHOICE, choice=str(choice), text=str(choice), raw=raw)

    @classmethod
    def float_response(cls, value: float, *, raw: Any | None = None) -> ActionOutput:
        return cls(
            output_type=OutputType.FLOAT, number=float(value), text=str(float(value)), raw=raw
        )

    @classmethod
    def skip(cls) -> ActionOutput:
        return cls(output_type=OutputType.SKIP)

    def __str__(self) -> str:
        if self.output_type == OutputType.TOOL_CALLS:
            return ", ".join(call.name for call in self._tool_calls)
        if self.output_type == OutputType.STRUCTURED:
            return str(dict(self.structured or {}))
        return self.text

    def to_dict(self) -> dict[str, Any]:
        """Serialize this action output to a JSON-friendly mapping."""
        output_type = (
            self.output_type.value
            if isinstance(self.output_type, OutputType)
            else str(self.output_type)
        )
        return {
            "output_type": output_type,
            "text": self._text,
            "tool_calls": [
                {"name": call.name, "arguments": dict(call.arguments)} for call in self._tool_calls
            ],
            "structured": dict(self.structured) if self.structured is not None else None,
            "choice": self.choice,
            "number": self.number,
            "raw": self.raw,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> ActionOutput:
        """Deserialize an action output mapping."""
        output_type = payload.get("output_type", OutputType.TEXT)
        text = str(payload.get("text", "") or "")
        tool_calls_raw = payload.get("tool_calls", [])
        tool_calls: list[ToolCall] = []
        if isinstance(tool_calls_raw, list):
            for item in tool_calls_raw:
                if not isinstance(item, Mapping):
                    continue
                tool_name = str(item.get("name", "") or "").strip()
                if not tool_name:
                    continue
                arguments = item.get("arguments", {})
                if not isinstance(arguments, Mapping):
                    arguments = {}
                tool_calls.append(ToolCall(tool_name, dict(arguments)))

        structured = payload.get("structured")
        if structured is not None and not isinstance(structured, Mapping):
            raise ValueError("ActionOutput.structured payload must be a mapping when provided.")
        choice = payload.get("choice")
        number = payload.get("number")
        if number is not None:
            number = float(number)
        return cls(
            output_type=output_type,
            text=text,
            tool_calls=tuple(tool_calls),
            structured=structured,
            choice=str(choice) if choice is not None else None,
            number=number,
            raw=payload.get("raw"),
        )


class ResolveReport(str):
    """A resolve result string that also carries per-turn commit accounting.

    Resolve components return the joined human-readable result string (which every
    caller ``str``-treats: fed to ``agent.observe``, logged, compared in tests). This
    subclass additively attaches how many of the emitted actions actually COMMITTED a
    backend state change vs how many were ATTEMPTED — the signal a committed-counting
    turn policy needs. Being a ``str``, it is transparent everywhere else; consumers
    that want the counts read ``.committed`` / ``.attempted`` (``None`` = unknown, e.g.
    a resolver that can't report per-call outcomes, in which case the policy falls back
    to counting emitted actions).
    """

    committed: int | None
    attempted: int | None

    def __new__(
        cls, value: str = "", *, committed: int | None = None, attempted: int | None = None
    ) -> ResolveReport:
        report = super().__new__(cls, value)
        report.committed = committed
        report.attempted = attempted
        return report
