"""OpenClaw (out-of-process, Node/TypeScript) as a silisocs harness.

OpenClaw can't be imported into Python, so a run drives it out-of-process: one gateway
process hosts all agents, our :mod:`mcp_server` exposes the backend as MCP tools, and the
adapter drives one turn over the gateway's WebSocket JSON-RPC API (``agent`` +
``agent.wait``). The adapter is async-native — ``run_turn_async`` awaits the WS turn —
which is loop-native under the asyncio executor (thousands of in-flight waits are cheap);
``run_turn`` is the required sync floor.

The WebSocket transport is an injectable seam (``agent_call``): the turn LOGIC (bind the
surface into the shared :class:`SurfaceRegistry` so MCP calls route back to it, dispatch,
collect results, snapshot the workspace) is unit-tested with a fake transport that
simulates OpenClaw calling MCP tools. The live WS client + gateway process lifecycle are
the opt-in integration path (``SILISOCS_OPENCLAW_BIN``), kept out of the tested core so
this module imports and is contract-tested with no Node runtime.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from silisocs.agents.harness.adapter import HarnessProbeRequest, HarnessTurnRequest
from silisocs.agents.harness.base import HarnessAgent, compose_persona
from silisocs.agents.harness.gateway import seed_workspace
from silisocs.agents.harness.mcp_server import SurfaceRegistry
from silisocs.agents.harness.types import HarnessTurnResult, extract_text, extract_usage
from silisocs.runtime.language_models import LanguageModel

# An async transport: given the turn message, drive one OpenClaw WS turn and return its
# terminal snapshot (text or a dict carrying "output"/"usage").
AgentCall = Callable[[str], Awaitable[Any]]

# Keys an OpenClaw WS turn result may carry its closing message under.
_OPENCLAW_TEXT_KEYS = ("output", "text", "final", "content")


def _run_coro_sync(coro: Any) -> Any:
    """Run a coroutine to completion from sync code, even inside a running loop."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)  # no loop on this thread
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(lambda: asyncio.run(coro)).result()


class OpenClawAdapter:
    """Drive one OpenClaw agent turn over a WS transport, routing tools via MCP."""

    def __init__(
        self,
        *,
        name: str,
        persona: str = "",
        model_name: str = "gpt-4o-mini",
        registry: SurfaceRegistry | None = None,
        agent_call: AgentCall | None = None,
        workspace_dir: str | Path | None = None,
    ) -> None:
        self._name = str(name)
        self._persona = str(persona or "")
        self._model_name = str(model_name)
        self._registry = registry
        self._agent_call = agent_call
        self._workspace_dir = Path(workspace_dir) if workspace_dir else None
        self._base_url = ""
        self._api_key = ""

    # ---- runtime injection (post-construction; see docs) ------------------ #

    def bind_model_proxy(self, base_url: str, api_key: str) -> None:
        self._base_url = str(base_url)
        self._api_key = str(api_key)

    def bind_gateway(self, registry: SurfaceRegistry, agent_call: AgentCall) -> None:
        """Attach the run's shared MCP surface registry and WS transport."""
        self._registry = registry
        self._agent_call = agent_call

    # ---- turns ------------------------------------------------------------ #

    @staticmethod
    def _compose(request: HarnessTurnRequest) -> str:
        parts = [request.observations.strip(), request.prompt.strip()]
        return "\n\n".join(part for part in parts if part)

    async def run_turn_async(self, request: HarnessTurnRequest) -> HarnessTurnResult:
        if self._agent_call is None:
            raise RuntimeError(
                "OpenClawAdapter has no transport; bind a live gateway (SILISOCS_OPENCLAW_BIN) "
                "or inject agent_call. See docs/harness_agents.md."
            )
        surface = request.surface
        if surface is not None and self._registry is not None:
            self._registry.bind(self._name, surface)
        try:
            result = await self._agent_call(self._compose(request))
        finally:
            if surface is not None and self._registry is not None:
                self._registry.clear(self._name)
        return HarnessTurnResult(
            final_text=extract_text(result, _OPENCLAW_TEXT_KEYS),
            usage=extract_usage(result),
            finished=True,
            raw=result,
        )

    def run_turn(self, request: HarnessTurnRequest) -> HarnessTurnResult:
        return _run_coro_sync(self.run_turn_async(request))

    def run_probe(self, request: HarnessProbeRequest) -> str:
        if self._agent_call is None:
            return ""
        instruction = request.prompt
        if request.options:
            instruction += "\nAnswer with exactly one of: " + ", ".join(request.options)
        result = _run_coro_sync(self._agent_call(instruction))
        return extract_text(result, _OPENCLAW_TEXT_KEYS)

    # ---- checkpoint (bounded workspace snapshot) -------------------------- #

    def snapshot(self) -> dict[str, Any]:
        if self._workspace_dir is None or not self._workspace_dir.is_dir():
            return {}
        files: dict[str, str] = {}
        for path in sorted(self._workspace_dir.glob("*.md")):
            files[path.name] = path.read_text(encoding="utf-8")
        return {"workspace": files}

    def restore(self, state: dict[str, Any]) -> None:
        files = state.get("workspace")
        if not isinstance(files, dict) or self._workspace_dir is None:
            return
        self._workspace_dir.mkdir(parents=True, exist_ok=True)
        for name, content in files.items():
            (self._workspace_dir / str(name)).write_text(str(content), encoding="utf-8")


class OpenClawAgent(HarnessAgent):
    """A silisocs agent backed by an out-of-process OpenClaw agent.

    Referenceable via ``persona_pipeline.classes.<class>.class_path``:
    ``silisocs.agents.harness.openclaw.OpenClawAgent``. The persona-pipeline fields seed
    the workspace ``SOUL.md`` (via :func:`~silisocs.agents.harness.gateway.seed_workspace`,
    called by the gateway service at run start); the run's shared MCP registry and WS
    transport are injected post-construction with ``adapter.bind_gateway``.
    """

    def __init__(
        self,
        model: LanguageModel,
        *,
        name: str,
        persona: str = "",
        probe_mode: str = "model",
        openclaw_model: str | None = None,
        workspace_dir: str | None = None,
        **persona_fields: Any,
    ) -> None:
        composed = compose_persona(persona, **persona_fields)
        model_name = str(openclaw_model or getattr(model, "_model_name", "") or "gpt-4o-mini")
        if workspace_dir:
            seed_workspace(workspace_dir, agent_name=name, persona=composed)
        adapter = OpenClawAdapter(
            name=name,
            persona=composed,
            model_name=model_name,
            workspace_dir=workspace_dir,
        )
        super().__init__(model, name=name, adapter=adapter, persona=composed, probe_mode=probe_mode)
