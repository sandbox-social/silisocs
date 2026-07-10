"""A Python MCP tool server exposing silisocs backends to OpenClaw agents.

OpenClaw agents (Node/TypeScript, out-of-process) consume tools via MCP. This module is
the piece that lets an OpenClaw agent act in a silisocs environment: it turns the acting
agent's :class:`ToolSurface` into MCP tools and routes every ``tools/call`` back to that
surface (which forwards to the backend and logs the action event, exactly like a native
turn).

Routing is by agent name: the :class:`SurfaceRegistry` maps each agent to its currently
bound surface (the adapter binds it right before dispatching the agent's WS turn, and
clears it after). :class:`OpenClawMcpTools` holds the transport-free JSON-RPC core
(``initialize`` / ``tools/list`` / ``tools/call``) so it is unit-testable in-process; the
stdio/HTTP transport that wraps it for the live gateway is a thin, separately-run shell.
"""

from __future__ import annotations

import threading
from typing import Any

from silisocs.agents.harness.bridge import ToolSurface

MCP_PROTOCOL_VERSION = "2024-11-05"


class SurfaceRegistry:
    """Thread-safe map of agent name -> the ToolSurface bound for its current turn."""

    def __init__(self) -> None:
        self._surfaces: dict[str, ToolSurface] = {}
        self._lock = threading.Lock()

    def bind(self, agent_name: str, surface: ToolSurface) -> None:
        with self._lock:
            self._surfaces[str(agent_name)] = surface

    def clear(self, agent_name: str) -> None:
        with self._lock:
            self._surfaces.pop(str(agent_name), None)

    def get(self, agent_name: str) -> ToolSurface | None:
        with self._lock:
            return self._surfaces.get(str(agent_name))


def _to_mcp_tool(schema: dict[str, Any]) -> dict[str, Any]:
    """Convert one OpenAI-style tool schema to the MCP ``tools/list`` shape."""
    function = (schema or {}).get("function") or {}
    return {
        "name": str(function.get("name") or ""),
        "description": str(function.get("description") or ""),
        "inputSchema": function.get("parameters")
        or {"type": "object", "properties": {}, "required": []},
    }


class OpenClawMcpTools:
    """Transport-free MCP tool server routing calls to per-agent ToolSurfaces."""

    def __init__(self, registry: SurfaceRegistry, *, server_name: str = "silisocs") -> None:
        self._registry = registry
        self._server_name = server_name

    def list_tools(self, agent_name: str) -> list[dict[str, Any]]:
        surface = self._registry.get(agent_name)
        if surface is None:
            return []
        return [_to_mcp_tool(schema) for schema in surface.schemas()]

    def call_tool(self, agent_name: str, name: str, arguments: dict[str, Any]) -> tuple[str, bool]:
        """Execute one tool; return (text_result, is_error)."""
        surface = self._registry.get(agent_name)
        if surface is None:
            return ("No active tool surface for this agent's turn.", True)
        call = surface.execute(str(name), dict(arguments or {}))
        return (call.result, not call.ok)

    def handle_rpc(self, request: dict[str, Any], *, agent_name: str) -> dict[str, Any]:
        """Handle one JSON-RPC request (``initialize`` / ``tools/list`` / ``tools/call``)."""
        method = str(request.get("method") or "")
        request_id = request.get("id")
        result = self._dispatch(method, request.get("params") or {}, agent_name=agent_name)
        if result is None:
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {"code": -32601, "message": f"Method not found: {method}"},
            }
        return {"jsonrpc": "2.0", "id": request_id, "result": result}

    def _dispatch(
        self, method: str, params: dict[str, Any], *, agent_name: str
    ) -> dict[str, Any] | None:
        if method == "initialize":
            return {
                "protocolVersion": MCP_PROTOCOL_VERSION,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": self._server_name, "version": "1"},
            }
        if method in ("notifications/initialized", "ping"):
            return {}
        if method == "tools/list":
            return {"tools": self.list_tools(agent_name)}
        if method == "tools/call":
            text, is_error = self.call_tool(
                agent_name, str(params.get("name") or ""), params.get("arguments") or {}
            )
            return {"content": [{"type": "text", "text": text}], "isError": is_error}
        return None
