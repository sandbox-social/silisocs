"""The single adapter seam every concrete harness plugs into.

:class:`HarnessAgent` (the silisocs-facing ``Agent``) owns observe-buffering, ActionSpec
dispatch, probe handling, checkpoint state, and telemetry. A :class:`HarnessAdapter`
owns exactly one responsibility: *run one harness turn* (and, optionally, one probe).
This keeps every backend/GM/engine concern on the silisocs side and every
harness-specific concern (Hermes' ``run_conversation``, OpenClaw's WebSocket turn)
behind a ~4-method contract, so a new harness is a new adapter and nothing else.

The contract is a structural :class:`typing.Protocol` — there is no base class to
subclass. An adapter is any object providing ``run_turn``; ``run_turn_async``,
``run_probe``, ``snapshot``, and ``restore`` are optional (the base agent supplies
sensible fallbacks: a thread for async, the model path for probes, empty state).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from silisocs.agents.harness.bridge import ToolSurface
from silisocs.agents.harness.types import HarnessTurnResult


@dataclass(frozen=True)
class HarnessTurnRequest:
    """Everything an adapter needs to run one turn.

    ``surface`` is ``None`` only for tool-free one-shot text turns (e.g. seed-post
    initialization) — a normal turn always carries the acting agent's Tool Bridge.
    """

    agent_name: str
    prompt: str
    observations: str = ""
    surface: ToolSurface | None = None


@dataclass(frozen=True)
class HarnessProbeRequest:
    """A single evaluation question routed to the harness (``probe_mode: harness``)."""

    agent_name: str
    prompt: str
    options: tuple[str, ...] = ()


@runtime_checkable
class HarnessAdapter(Protocol):
    """Structural contract a harness backend implements to act in silisocs."""

    def run_turn(self, request: HarnessTurnRequest) -> HarnessTurnResult:
        """Run one full harness turn, executing tools via ``request.surface``."""
        ...

    # Optional members below are declared for documentation/typing only; the base
    # agent probes for them with ``getattr`` and falls back when absent, so an adapter
    # need not provide them.

    async def run_turn_async(self, request: HarnessTurnRequest) -> HarnessTurnResult:
        """Loop-native turn for I/O-bound harnesses (e.g. OpenClaw's WebSocket wait)."""
        ...

    def run_probe(self, request: HarnessProbeRequest) -> str:
        """Answer one evaluation question with the harness (``probe_mode: harness``)."""
        ...

    def snapshot(self) -> dict[str, Any]:
        """Return checkpointable adapter state (conversation history, workspace, ...)."""
        ...

    def restore(self, state: dict[str, Any]) -> None:
        """Restore adapter state from a snapshot (never replay-restored)."""
        ...
