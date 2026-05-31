[![CI](https://github.com/sandbox-social/silisocs/actions/workflows/test.yml/badge.svg)](https://github.com/sandbox-social/silisocs/actions/workflows/test.yml)
[![Docs](https://github.com/sandbox-social/silisocs/actions/workflows/docs.yml/badge.svg)](https://sandbox-social.github.io/silisocs/)

# SiliSoCS

SiliSoCS (Silicon Society Sandbox) is a native simulation framework for configurable social and
agent-environment experiments. It provides a YAML-first scenario layer,
environment game masters, local and live backends, evaluation probes, runtime
telemetry, and study tooling. Concordia interoperability is available through an
optional bridge extra.

- 2024 NeurIPS Workshop Paper: [arXiv:2410.13915](http://arxiv.org/abs/2410.13915)
- 2025 IJCAI Demo Paper: [IJCAI 2025](https://www.ijcai.org/proceedings/2025/1271)
- 2026 EASE Configuration: [arXiv:2605.30258](https://arxiv.org/abs/2605.30258)
- Documentation: [sandbox-social.github.io/silisocs](https://sandbox-social.github.io/silisocs)

## Install

The default package is intentionally lean and supports local simulations without
dashboard, Mastodon, HuggingFace, or analysis dependencies:

```sh
pip install silisocs
```

Optional integrations are exposed as extras:

```sh
pip install "silisocs[hf]"        # Hugging Face persona sources
pip install "silisocs[mastodon]"  # real Mastodon backend
pip install "silisocs[dashboard]" # Streamlit launcher
pip install "silisocs[analysis]"  # plotting and analysis dashboards
pip install "silisocs[viz]"       # local backend web visualizers
pip install "silisocs[concordia]" # optional Concordia bridge
```

For contributor work from a checkout:

```sh
git clone https://github.com/sandbox-social/silisocs.git
cd silisocs
uv sync --all-extras --group dev --group docs
```

## Quick Start

Run the built-in package default scenario:

```sh
uv run silisocs
```

For a local smoke test without model API calls:

```sh
uv run silisocs sim.llm.provider=scripted
```

Override scale or model settings with Hydra dot notation:

```sh
uv run silisocs num_agents=10 num_steps=5 sim.llm.name=gpt-4o
```

Run a bundled external scenario:

```sh
uv run silisocs --config-path scenarios/election/conf
```

Run the packaged resource-market preset:

```sh
uv run silisocs scenario=resource_market agents=resource_market env=resource_market
```

Run the packaged virtual-space preset:

```sh
uv run silisocs scenario=virtual_space agents=virtual_space env=virtual_space
```

The same backends also have curated external examples under `scenarios/`:

```sh
uv run silisocs --config-path scenarios/resource_market/conf scenario=resource_market agents=resource_market env=resource_market
uv run silisocs --config-path scenarios/virtual_space/conf scenario=virtual_space agents=virtual_space env=virtual_space
```

Outputs are written under `outputs/<scenario_name>/<jobname>/` and include
`action_events.jsonl`, `probe_events.jsonl`, `prompts_and_responses.jsonl`,
`sim_metrics.json`, a resolved Hydra config snapshot, and a local
SQLite backend database for local platforms.

## Architecture

The canonical runtime entry point is `src/silisocs/runtime/runner.py`. It
composes Hydra configuration, builds agents, initializes memory, constructs the
environment backend and game master, runs the simulation engine, and writes
artifacts.

```text
silisocs/
├── src/silisocs/
│   ├── agents/              # Native and bridge-compatible runtime agents
│   ├── conf/                # Packaged Hydra defaults
│   ├── dashboard/           # Optional Streamlit scenario launcher
│   ├── environments/        # Game masters and environment backends
│   ├── evaluations/         # Probes, telemetry, and optional analysis tools
│   ├── runtime/             # Runner, config projection, and orchestration
│   └── simulation_engines/  # Engine loop, step, and turn policies
├── scenarios/               # Scenario configs and curated inputs
├── experiments/             # Study orchestration and generated study outputs
├── docs/                    # ProperDocs documentation
└── tests/                   # Unit and integration tests
```

## Optional Concordia Bridge

SiliSoCS runs on native runtime contracts by default. The optional Concordia
bridge is for porting Concordia-designed agents or components without
making Concordia part of the default install:

- All runtime agents satisfy `silisocs.agents.base_agent.Agent`.
- Native runtime classes and GM components are the primary extension API.
- Legacy Concordia-shaped components are isolated behind
  `silisocs.adapters.concordia`.
- Scenario YAML selects builders, backends, policies, probes, and prompts so
  most experiment design does not require Python edits.

See [docs/concordia_bridge.md](docs/concordia_bridge.md) and
[docs/building_agents.md](docs/building_agents.md) for the extension contracts.

## Development

Common commands:

```sh
uv run pytest
uv run poe lint
uv build --sdist --wheel
uv run properdocs build --strict
```
