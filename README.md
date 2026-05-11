[![CI](https://github.com/social-sandbox/silisocs/actions/workflows/test.yml/badge.svg)](https://github.com/social-sandbox/silisocs/actions/workflows/test.yml)
[![Docs](https://github.com/social-sandbox/silisocs/actions/workflows/docs.yml/badge.svg)](https://social-sandbox.github.io/silisocs)

# Silisocs

Silisocs is a Concordia-based social simulation framework for configurable
social-media experiments. It adds a YAML-first scenario layer, social-media
game masters, local platform backends, evaluation probes, runtime telemetry,
and study tooling on top of Concordia's agent/runtime abstractions.

- 2024 NeurIPS Workshop Paper: [arXiv:2410.13915](http://arxiv.org/abs/2410.13915)
- 2025 IJCAI Demo Paper: [IJCAI 2025](https://www.ijcai.org/proceedings/2025/1271)
- Documentation: [social-sandbox.github.io/silisocs](https://social-sandbox.github.io/silisocs)

## Install

The default package is intentionally lean and supports local simulations without
dashboard, Mastodon, Hugging Face, or analysis dependencies:

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
```

For contributor work from a checkout:

```sh
git clone https://github.com/social-sandbox/silisocs.git
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
uv run silisocs sim.llm.disabled=true
```

Override scale or model settings with Hydra dot notation:

```sh
uv run silisocs num_agents=10 num_steps=5 sim.llm.name=gpt-4o
```

Run a bundled external scenario:

```sh
uv run silisocs --config-path scenarios/election/conf
```

Outputs are written under `outputs/<scenario_name>/<jobname>/` and include
`action_events.jsonl`, `probe_events.jsonl`, `prompts_and_responses.jsonl`,
`sim_metrics.json`, `logs.html`, a resolved Hydra config snapshot, and a local
SQLite backend database for local platforms.

## Architecture

The canonical runtime entry point is `src/silisocs/runtime/runner.py`. It
composes Hydra configuration, builds agents, initializes memory, constructs the
social-media backend and game master, runs the simulation engine, and writes
artifacts.

```text
silisocs/
├── src/silisocs/
│   ├── agents/              # Concordia-compatible and custom agent builders
│   ├── conf/                # Packaged Hydra defaults
│   ├── dashboard/           # Optional Streamlit scenario launcher
│   ├── environments/        # Game masters and social-media backends
│   ├── evaluations/         # Probes, telemetry, and optional analysis tools
│   ├── runtime/             # Runner, config projection, and orchestration
│   └── simulation_engines/  # Action-loop and probe scheduling policies
├── scenarios/               # Scenario configs and curated inputs
├── experiments/             # Study orchestration and generated study outputs
├── docs/                    # MkDocs documentation
└── tests/                   # Unit and integration tests
```

## Concordia Bridge

Silisocs does not replace Concordia. It uses Concordia as the agent and game
runtime substrate, then adds social-simulation conventions around it:

- Concordia entity agents are wrapped behind the common
  `silisocs.agents.base_agent.Agent` interface.
- Silisocs prefabs build either Concordia-compatible agents or simpler custom
  agents that implement `name`, `observe(...)`, and `act(...)`.
- Game-master components translate social-media observations and actions into
  Concordia-compatible action specs.
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
uv run mkdocs build --strict
```

Do not commit `.env` files, credentials, live-service tokens, or generated
secrets. Mastodon credentials should be provided only through local environment
variables when using the optional `mastodon` extra.
