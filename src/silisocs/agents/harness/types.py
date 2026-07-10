"""Typed results exchanged between a harness adapter, the Tool Bridge, and the GM.

These are plain frozen dataclasses so they serialize cleanly into checkpoints and the
``harness_events.jsonl`` log and carry no behavior. A harness *turn* runs the harness's
own agentic loop to completion (many model->tool->model calls) and reports back what it
executed via :class:`HarnessTurnResult`. A probe answers a single evaluation question and
is just a plain ``str`` (the adapters' ``run_probe`` return).

:func:`extract_text` / :func:`extract_usage` normalize the str-or-dict shapes real
harnesses return, shared by every adapter so there is one place that shape lives.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any


def extract_text(result: Any, keys: tuple[str, ...]) -> str:
    """Best-effort final text from a harness result (a ``str`` or a ``{key: text}`` dict)."""
    if isinstance(result, str):
        return result
    if isinstance(result, dict):
        for key in keys:
            value = result.get(key)
            if isinstance(value, str) and value.strip():
                return value
    return str(result or "")


def extract_usage(result: Any) -> dict[str, Any]:
    """Extract the provider ``usage`` block from a harness result, or ``{}`` if absent."""
    if isinstance(result, dict) and isinstance(result.get("usage"), dict):
        return dict(result["usage"])
    return {}


@dataclass(frozen=True)
class ExecutedToolCall:
    """One backend action the harness invoked through the Tool Bridge this turn.

    ``ok`` is ``True`` when the call reached the backend and returned a result (the
    ``result`` string is what the harness saw next); ``False`` when the Bridge refused
    the call (per-flow filter, reserved actor argument) or the backend raised — in which
    case ``error`` names why and ``result`` carries the message fed back to the harness.
    """

    name: str
    arguments: Mapping[str, Any] = field(default_factory=dict)
    result: str = ""
    ok: bool = True
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "arguments": dict(self.arguments),
            "result": self.result,
            "ok": self.ok,
            "error": self.error,
        }


@dataclass(frozen=True)
class HarnessTurnResult:
    """The outcome of one full harness turn, returned by an adapter's ``run_turn``.

    ``final_text`` is the harness's closing assistant message (recorded as the turn's
    action text). ``executed`` is every tool call the Bridge ran, in order. ``usage`` is
    the harness's own token accounting when it reports any (``{}`` otherwise; the Model
    Proxy is the authoritative usage path). ``finished`` says the harness turn ran to
    completion — the harness owns its own loop, so a turn is one complete unit of work.
    """

    final_text: str = ""
    executed: tuple[ExecutedToolCall, ...] = ()
    usage: Mapping[str, Any] = field(default_factory=dict)
    finished: bool = True
    raw: Any = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "final_text": self.final_text,
            "executed": [call.to_dict() for call in self.executed],
            "usage": dict(self.usage),
            "finished": self.finished,
        }
