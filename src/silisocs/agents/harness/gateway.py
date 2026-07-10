"""OpenClaw gateway configuration and per-agent workspace seeding.

Running real OpenClaw agents means: one gateway process per run, configured to (a) list
our agents, (b) deny all built-in real-world tools, (c) mount our MCP tool server, (d)
point its model provider at the run's Model Proxy (per-agent routing tokens, so real
provider keys never touch the OpenClaw state dir), and (e) disable heartbeat/cron so
nothing fires outside the step loop. This module builds that config and seeds each
agent's workspace persona files from the persona pipeline — the memory-seeding seam.

Everything here is pure (dict/file generation) and unit-tested. The gateway *process*
lifecycle (spawn/monitor/shutdown) is a thin shell that consumes these outputs and is
exercised only by the opt-in live integration test.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any


def build_gateway_config(
    *,
    agents: list[str],
    proxy_base_url: str,
    agent_tokens: dict[str, str],
    mcp_command: list[str],
    model_name: str,
) -> dict[str, Any]:
    """Build the ``openclaw.json`` config for a run.

    ``agent_tokens`` maps each agent to its Model Proxy routing token (used as the
    provider api key), so credentials stay in the proxy. ``mcp_command`` launches our
    stdio MCP tool server. Heartbeat/cron are disabled so agents act only when driven.
    """
    return {
        "models": {
            "providers": [
                {
                    "name": "silisocs-proxy",
                    "baseUrl": proxy_base_url,
                    # A single shared placeholder; per-agent tokens are applied per agent
                    # below so each agent's calls are attributable at the proxy.
                    "apiKey": "per-agent",
                }
            ],
            "default": {"provider": "silisocs-proxy", "model": model_name},
        },
        "mcp": {
            "servers": {"silisocs": {"command": mcp_command[0], "args": list(mcp_command[1:])}}
        },
        "heartbeat": {"enabled": False},
        "cron": {"enabled": False},
        "agents": [
            {
                "name": agent,
                "provider": "silisocs-proxy",
                "model": model_name,
                "apiKey": agent_tokens.get(agent, ""),
                "tools": {"deny": ["*"], "allow": ["silisocs__*"]},
            }
            for agent in agents
        ],
    }


def seed_workspace(workspace_dir: str | Path, *, agent_name: str, persona: str) -> dict[str, Path]:
    """Write an OpenClaw agent workspace (SOUL.md / MEMORY.md / AGENTS.md); return paths.

    The persona-pipeline persona seeds ``SOUL.md`` (injected at session start).
    ``MEMORY.md`` starts empty (the agent curates it), and ``AGENTS.md`` carries the
    operating instructions. This is the memory-seeding seam from the MoltBook analysis.
    """
    root = Path(workspace_dir)
    root.mkdir(parents=True, exist_ok=True)
    files = {
        "SOUL.md": f"# {agent_name}\n\n{persona.strip()}\n"
        if persona.strip()
        else f"# {agent_name}\n",
        "MEMORY.md": "# Memory\n\n(Empty — curate as you learn.)\n",
        "AGENTS.md": (
            "# Operating instructions\n\n"
            "You act in a simulated social environment. Use only the provided silisocs "
            "tools to observe and act. Do not attempt real-world actions.\n"
        ),
    }
    written: dict[str, Path] = {}
    for filename, content in files.items():
        path = root / filename
        path.write_text(content, encoding="utf-8")
        written[filename] = path
    return written
