# mypy: disable-error-code="arg-type"
# ^ Relaxed for this experimental suite's minimal fake model (not a LanguageModel subclass).
"""Contract tests for the OpenClaw harness pieces (no Node runtime required).

Covers the silisocs-side implementation: the MCP tool server routing calls to per-agent
ToolSurfaces, gateway config generation, workspace seeding, and the adapter's turn logic
with a fake WS transport that simulates OpenClaw calling MCP tools. The live gateway
process + real WebSocket client are the opt-in integration path (``SILISOCS_OPENCLAW_BIN``)
and are not exercised here.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from silisocs.agents.harness.adapter import HarnessProbeRequest, HarnessTurnRequest
from silisocs.agents.harness.base import HarnessAgent
from silisocs.agents.harness.bridge import ToolSurface
from silisocs.agents.harness.gateway import build_gateway_config, seed_workspace
from silisocs.agents.harness.mcp_server import (
    MCP_PROTOCOL_VERSION,
    OpenClawMcpTools,
    SurfaceRegistry,
)
from silisocs.agents.harness.openclaw import OpenClawAdapter, OpenClawAgent
from silisocs.environments.backends.twitter_like.app import TwitterLikeApp


@pytest.fixture
def twitter(tmp_path: Any) -> Any:
    app = TwitterLikeApp(db_path=str(tmp_path / "tw.db"))
    app.setup_social_state(agent_names=["Alice"], following_graph={})
    yield app
    app.shutdown()


# --------------------------------------------------------------------------- #
# MCP tool server
# --------------------------------------------------------------------------- #


def test_mcp_lists_and_calls_tools(twitter: Any) -> None:
    registry = SurfaceRegistry()
    mcp = OpenClawMcpTools(registry)
    surface = ToolSurface(backend=twitter, agent_name="Alice")
    registry.bind("Alice", surface)

    tools = mcp.list_tools("Alice")
    names = {tool["name"] for tool in tools}
    assert "create_tweet" in names
    assert all("inputSchema" in tool for tool in tools)

    text, is_error = mcp.call_tool("Alice", "create_tweet", {"status": "openclaw post"})
    assert not is_error
    assert "posted a tweet" in text
    assert surface.executed[0].name == "create_tweet"


def test_mcp_call_without_bound_surface_is_error() -> None:
    mcp = OpenClawMcpTools(SurfaceRegistry())
    text, is_error = mcp.call_tool("Ghost", "create_tweet", {"status": "x"})
    assert is_error
    assert "No active tool surface" in text


def test_mcp_handle_rpc(twitter: Any) -> None:
    registry = SurfaceRegistry()
    mcp = OpenClawMcpTools(registry)
    registry.bind("Alice", ToolSurface(backend=twitter, agent_name="Alice"))

    init = mcp.handle_rpc({"jsonrpc": "2.0", "id": 1, "method": "initialize"}, agent_name="Alice")
    assert init["result"]["protocolVersion"] == MCP_PROTOCOL_VERSION

    listed = mcp.handle_rpc({"id": 2, "method": "tools/list"}, agent_name="Alice")
    assert any(t["name"] == "create_tweet" for t in listed["result"]["tools"])

    called = mcp.handle_rpc(
        {
            "id": 3,
            "method": "tools/call",
            "params": {"name": "create_tweet", "arguments": {"status": "hi"}},
        },
        agent_name="Alice",
    )
    assert called["result"]["isError"] is False
    assert "posted a tweet" in called["result"]["content"][0]["text"]

    unknown = mcp.handle_rpc({"id": 4, "method": "does/not/exist"}, agent_name="Alice")
    assert unknown["error"]["code"] == -32601


# --------------------------------------------------------------------------- #
# Gateway config + workspace
# --------------------------------------------------------------------------- #


def test_gateway_config_denies_tools_and_routes_to_proxy() -> None:
    config = build_gateway_config(
        agents=["Alice", "Bob"],
        proxy_base_url="http://127.0.0.1:9/v1",
        agent_tokens={"Alice": "tok-a", "Bob": "tok-b"},
        mcp_command=["python", "-m", "silisocs.agents.harness.mcp_stdio"],
        model_name="gpt-4o-mini",
    )
    assert config["models"]["providers"][0]["baseUrl"] == "http://127.0.0.1:9/v1"
    assert config["heartbeat"]["enabled"] is False
    assert config["cron"]["enabled"] is False
    assert config["mcp"]["servers"]["silisocs"]["command"] == "python"
    alice = next(a for a in config["agents"] if a["name"] == "Alice")
    assert alice["apiKey"] == "tok-a"
    assert alice["tools"]["deny"] == ["*"]


def test_seed_workspace_writes_persona_files(tmp_path: Any) -> None:
    paths = seed_workspace(tmp_path / "ws", agent_name="Alice", persona="Alice is a teacher.")
    assert paths["SOUL.md"].read_text(encoding="utf-8").startswith("# Alice")
    assert "Alice is a teacher." in paths["SOUL.md"].read_text(encoding="utf-8")
    assert paths["MEMORY.md"].is_file()
    assert paths["AGENTS.md"].is_file()


# --------------------------------------------------------------------------- #
# Adapter (fake transport simulating OpenClaw calling MCP tools)
# --------------------------------------------------------------------------- #


def _fake_transport(mcp: OpenClawMcpTools, agent_name: str) -> Any:
    async def agent_call(message: str) -> dict[str, Any]:
        # Simulate OpenClaw discovering and calling one tool over MCP.
        mcp.list_tools(agent_name)
        mcp.call_tool(agent_name, "create_tweet", {"status": "openclaw turn"})
        return {"output": "turn complete", "usage": {"prompt_tokens": 7, "completion_tokens": 2}}

    return agent_call


def test_adapter_run_turn_async_routes_via_registry(twitter: Any) -> None:
    registry = SurfaceRegistry()
    mcp = OpenClawMcpTools(registry)
    adapter = OpenClawAdapter(name="Alice", registry=registry)
    adapter.bind_gateway(registry, _fake_transport(mcp, "Alice"))
    surface = ToolSurface(backend=twitter, agent_name="Alice")

    result = asyncio.run(
        adapter.run_turn_async(HarnessTurnRequest(agent_name="Alice", prompt="go", surface=surface))
    )
    assert result.final_text == "turn complete"
    assert result.usage["prompt_tokens"] == 7
    assert surface.executed[0].name == "create_tweet"
    # Surface unbound after the turn.
    assert registry.get("Alice") is None


def test_adapter_run_turn_sync_floor(twitter: Any) -> None:
    registry = SurfaceRegistry()
    mcp = OpenClawMcpTools(registry)
    adapter = OpenClawAdapter(name="Alice", registry=registry)
    adapter.bind_gateway(registry, _fake_transport(mcp, "Alice"))
    surface = ToolSurface(backend=twitter, agent_name="Alice")
    result = adapter.run_turn(HarnessTurnRequest(agent_name="Alice", prompt="go", surface=surface))
    assert result.final_text == "turn complete"
    assert surface.executed[0].ok


def test_adapter_without_transport_raises() -> None:
    adapter = OpenClawAdapter(name="Alice")
    with pytest.raises(RuntimeError, match="no transport"):
        asyncio.run(adapter.run_turn_async(HarnessTurnRequest(agent_name="Alice", prompt="go")))


def test_adapter_snapshot_restore_workspace(tmp_path: Any) -> None:
    ws = tmp_path / "ws"
    seed_workspace(ws, agent_name="Alice", persona="p")
    adapter = OpenClawAdapter(name="Alice", workspace_dir=ws)
    (ws / "MEMORY.md").write_text("# Memory\n\nlearned something\n", encoding="utf-8")
    snap = adapter.snapshot()
    assert "learned something" in snap["workspace"]["MEMORY.md"]

    other = tmp_path / "ws2"
    restored = OpenClawAdapter(name="Alice", workspace_dir=other)
    restored.restore(snap)
    assert "learned something" in (other / "MEMORY.md").read_text(encoding="utf-8")


def test_openclaw_agent_is_harness_agent_and_seeds_workspace(tmp_path: Any) -> None:
    class _Model:
        _model_name = "gpt-4o-mini"

    agent = OpenClawAgent(
        _Model(),
        name="Alice",
        workspace_dir=str(tmp_path / "ws"),
        context="Alice is a teacher.",
    )
    assert isinstance(agent, HarnessAgent)
    assert (tmp_path / "ws" / "SOUL.md").is_file()
    assert "Alice is a teacher." in (tmp_path / "ws" / "SOUL.md").read_text(encoding="utf-8")


def test_openclaw_probe_without_transport_returns_empty() -> None:
    adapter = OpenClawAdapter(name="Alice")
    result = adapter.run_probe(HarnessProbeRequest(agent_name="Alice", prompt="?"))
    assert result == ""
