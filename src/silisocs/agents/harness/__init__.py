"""Harness-backed agents: real agent harnesses (Hermes, OpenClaw) as silisocs agents.

A harness agent runs its own agentic loop inside one turn, executing tools against any
silisocs backend through the :class:`ToolSurface` Tool Bridge. The pieces:

- :class:`HarnessAgent` — the silisocs ``Agent`` façade (observe/act/probe/checkpoint).
- :class:`HarnessAdapter` — the ~4-method seam a concrete harness implements.
- :class:`ToolSurface` — the per-turn Tool Bridge (backend catalog -> harness tools).
- :class:`FakeHarnessAdapter` / :class:`FakeHarnessAgent` — deterministic, dependency
  -free reference harness used by the contract tests and available as a dry run.

See ``docs/harness_agents.md`` and AGENTS.md §5.
"""

from __future__ import annotations

from silisocs.agents.harness.adapter import (
    HarnessAdapter,
    HarnessProbeRequest,
    HarnessTurnRequest,
)
from silisocs.agents.harness.base import HarnessAgent, compose_persona
from silisocs.agents.harness.bridge import ToolSurface
from silisocs.agents.harness.fake import FakeHarnessAdapter, FakeHarnessAgent
from silisocs.agents.harness.proxy import HarnessModelProxy, UsageAccumulator
from silisocs.agents.harness.types import ExecutedToolCall, HarnessTurnResult

__all__ = [
    "ExecutedToolCall",
    "FakeHarnessAdapter",
    "FakeHarnessAgent",
    "HarnessAdapter",
    "HarnessAgent",
    "HarnessModelProxy",
    "HarnessProbeRequest",
    "HarnessTurnRequest",
    "HarnessTurnResult",
    "ToolSurface",
    "UsageAccumulator",
    "compose_persona",
]
