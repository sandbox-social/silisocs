[![CI](https://github.com/sandbox-social/silisocs/actions/workflows/test.yml/badge.svg)](https://github.com/sandbox-social/silisocs/actions/workflows/test.yml)
[![Docs](https://github.com/sandbox-social/silisocs/actions/workflows/docs.yml/badge.svg)](https://sandbox-social.github.io/silisocs/)

**NOTE: This repository is currently in alpha development, and we expect to ship further stability updates over the coming weeks**

# SiliSocS

**A configurable, extensible framework for multi-agent social simulation and experimentation.**

SiliSocS (Silicon Society Sandbox) is easy to use and is structured around the EASE decomposition (Environment,
Agents, Simulation engine, and Evaluation), providing a principled, reproducible configuration layer for
simulated worlds. You define each axis in YAML: the environment agents inhabit, the agent population and their
memories, the simulation engine that schedules how and when agents act, and the evaluation that measures them.
Built-in environments span social platforms (a Twitter-like app, a Reddit-like app, and a real Mastodon server),
a resource market, and a virtual space, and you can add your own. SiliSocS offers scenario-driven grounding,
environments mediated by a game master (the referee component that shows each agent its view of the world and
turns its responses into platform actions), local and served backends, evaluation probes, runtime telemetry, and
experimental study tooling. Agents designed for DeepMind's Concordia framework — an inspiration for this project
— can be reused through an optional compatibility bridge; Concordia is not required.

New here? See the [Glossary](https://sandbox-social.github.io/silisocs/glossary/) for the handful of terms the
docs use (scenario, run, step, game master, probe, flow, ...).

- 2026 ICML Position Paper [ICML 2026](https://www.complexdatalab.com/stamina/papers/puelmatouzel_CloseEvalGap.pdf)
- 2026 EASE Configuration: [arXiv:2605.30258](https://arxiv.org/abs/2605.30258)
- Documentation: [sandbox-social.github.io/silisocs](https://sandbox-social.github.io/silisocs)
- Demo videos (CLI quickstart + Studio tour): [End-to-End Demo](https://sandbox-social.github.io/silisocs/tutorials/studio_demo/)

Papers on a previous version centered on a served Mastodon social media network:
- 2024 NeurIPS Workshop Paper: [arXiv:2410.13915](http://arxiv.org/abs/2410.13915)
- 2025 IJCAI Demo Paper: [IJCAI 2025](https://www.ijcai.org/proceedings/2025/1271)

## Install

The default package is intentionally lean and supports local simulations without
Studio, Mastodon, HuggingFace, or analysis dependencies:

```sh
pip install silisocs
```

Optional integrations are exposed as extras:

```sh
pip install "silisocs[studio]"    # unified visual workspace
pip install "silisocs[analysis]"  # notebooks and extended analysis
pip install "silisocs[hf]"        # Hugging Face persona sources
pip install "silisocs[mastodon]"  # real Mastodon backend
pip install "silisocs[all]"       # every extra except aws (includes docs)
```

The full extra-by-extra table (`recsys`, `concordia`, `hpc`, `aws`, `docs`, ...)
is in [docs/installation.md](docs/installation.md).

For contributor work from a checkout:

```sh
git clone https://github.com/sandbox-social/silisocs.git
cd silisocs
uv sync --all-extras --group dev --group docs
```

## Quick Start

Run the built-in package default scenario (`uv run silisocs` from a repo
checkout; plain `silisocs` after `pip install silisocs`):

```sh
uv run silisocs
```

For a local smoke test without model API calls:

```sh
uv run silisocs sim.llm.provider=scripted
```

Two guided commands verify your setup and walk a complete run end to end —
no API key needed:

```sh
uv run silisocs doctor
uv run silisocs tutorial
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
uv run silisocs world=resource_market agents=resource_market env=resource_market
```

Run the packaged virtual-space preset:

```sh
uv run silisocs world=virtual_space agents=virtual_space env=virtual_space
```

The same backends also have curated external examples under `scenarios/`:

```sh
uv run silisocs --config-path scenarios/resource_market/conf world=resource_market agents=resource_market env=resource_market
uv run silisocs --config-path scenarios/virtual_space/conf world=virtual_space agents=virtual_space env=virtual_space
```

Outputs are written under
`outputs/<scenario_name>/<jobname_format>/<scenario_name>_<timestamp>/` (for
example `outputs/default/N10_T5_independent_run1/default_2026-05-01_12-30-00/`)
and include `run_manifest.json`, `action_events.jsonl`, `probe_events.jsonl`,
`prompts_and_responses.jsonl`, `sim_metrics.json`, `effective_config.yaml`, and
a local SQLite backend database for local platforms. The timestamped leaf means
re-running the same parameters never overwrites a previous run. See
[docs/usage.md](docs/usage.md) for the full list.

## Studies and Experiments

Study orchestration ships in the package as the `silisocs.studies` subpackage,
exposed as the `silisocs-study` console command (equivalent: `python -m
silisocs.studies.run_study`). It expands hypotheses, conditions, scenarios, and
seeds into reproducible simulation runs, then executes the configured evaluators
and writes organized artifacts under the study's `generated/` directory.

```sh
uv run silisocs-study --study experiments/studies/study_template_v1 plan
uv run silisocs-study --study experiments/studies/study_template_v1 run --only-hypothesis h1_timeline_mechanism
uv run silisocs-study --study experiments/studies/study_template_v1 summary-append --author analyst --hypothesis h1_timeline_mechanism --note "Initial finding"
```

Custom commands plug in through `conditions.<id>.execution.command`, evaluator
commands through the `evaluations` list, and optional HPC setup through the
study runner's `submitit` or `slurm-array` commands. Start with
[docs/study_guide.md](docs/study_guide.md) to design a study, then
[docs/experiments.md](docs/experiments.md) for the runner reference and
[docs/study_schema.md](docs/study_schema.md) for the file formats.

## Architecture

The canonical runtime entry point is
`src/silisocs/runtime/execution/session.py`. It composes Hydra configuration,
builds agents, initializes memory, constructs the environment backend and game
master, runs the simulation engine, and writes artifacts.
(`src/silisocs/runtime/runner.py` is a thin re-export shim kept so
`python -m silisocs.runtime.runner` keeps working.)

```text
silisocs/
├── src/silisocs/
│   ├── agents/              # Native and bridge-compatible runtime agents
│   ├── conf/                # Packaged Hydra defaults
│   ├── studio/              # Unified visual workspace and control plane
│   ├── environments/        # Game masters and environment backends
│   ├── evaluations/         # Probes, telemetry, and optional analysis tools
│   ├── runtime/             # Session entrypoint, config projection, orchestration
│   └── simulation_engines/  # Engine loop, step, and turn policies
├── scenarios/                  # Scenario configs and curated inputs
├── experiments/             # Study orchestration and generated study outputs
├── docs/                    # ProperDocs documentation
└── tests/                   # Unit and integration tests
```

## Optional Concordia Bridge

SiliSocS runs on native runtime contracts by default. The optional Concordia
bridge is for porting Concordia-designed agents or components without
making Concordia part of the default install:

- All runtime agents satisfy `silisocs.agents.base_agent.Agent`.
- Native runtime classes and GM components are the primary extension API.
- Legacy Concordia-shaped components are isolated behind
  `silisocs.adapters.concordia`.
- Scenario YAML selects builders, backends, policies, probes, and prompts so
  most experiment designs do not require Python edits.

See [docs/concordia_bridge.md](docs/concordia_bridge.md) and
[docs/building_agents.md](docs/building_agents.md) for the extension contracts.

## Development

Common commands:

```sh
uv run pytest
uv run silisocs-config-dry-run --project-root .
uv run --group dev poe lint
uv run --group dev poe docs
uv build --sdist --wheel
uv run --group docs properdocs build --strict
```

- `uv run pytest` runs the test suite in the current environment.
- `uv run silisocs-config-dry-run --project-root .` composes shipped scenario
  and replication configs without running LLM calls.
- `uv run --group dev poe lint` runs the configured formatting, static checks, and type
  checks.
- `uv run --group dev poe docs` runs the configured documentation build task.
- `uv build --sdist --wheel` builds release artifacts in `dist/`.
- `uv run --group docs properdocs build --strict` builds the documentation site
  and fails on broken links or stale navigation.
