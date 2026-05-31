# Social Simulation Sandbox

**Configurable generative agent simulation of social media using Silisocs-native runtime contracts.**

- 2024 NeurIPS Workshop Paper: [arXiv:2410.13915](http://arxiv.org/abs/2410.13915)
- 2025 IJCAI Demo Paper: [IJCAI 2025](https://www.ijcai.org/proceedings/2025/1271)
- Version 2: Structured scenario configuration with an optional Concordia bridge

---

## What is this?

Social Simulation Sandbox lets you spin up large-scale social media simulations
populated by LLM-powered generative agents. Each agent has a unique persona,
memories, and goals, and they interact through a configurable environment
backend such as a Twitter-like app, Reddit-like app, real Mastodon instance,
resource market, or virtual space.

You configure everything in YAML: the scenario setting, agent populations,
backend, Game Master components, evaluation probes, and run defaults. The
framework handles the rest — memory initialization, turn policies, probe
deployment, and structured output logging.

## Key Features

| Feature | Description |
|---------|-------------|
| **Declarative scenarios** | Define agents, settings, and networks in YAML — no Python needed for most use cases |
| **Multiple backends** | Local Twitter-like and Reddit-like backends (SQLite), a real Mastodon server, a resource market, or a virtual space |
| **Scalable** | Tested with 5000+ concurrent agents using adaptive concurrency control |
| **Persona pipeline** | Source agent personas from HuggingFace datasets, local JSON, inline YAML, or config references |
| **Memory initialization** | Raw (config-only) or formative (LLM-generated backstories) modes, with custom initializers |
| **Evaluation probes** | Deploy longitudinal surveys (numeric, binary, choice, free-text) to agents during simulation |
| **Per-agent LLM** | Assign different LLM models per agent class or per individual agent |
| **Streamlit dashboard** | GUI for creating scenarios, configuring agents, and launching simulations |
| **Hydra config** | Full Hydra composition with CLI overrides, sweep support, and structured logging |
| **Rich output** | Action events, probe responses, LLM logs, timing telemetry, and structured metrics (JSON) |
| **Built-in visualizers** | Web UIs for browsing simulated Twitter/Reddit platforms (user profiles, threads, admin stats) |

## For End Users

If your goal is to **design and run scenarios** without writing code:

1. Start with the [Quick Start](quickstart.md) to run the default scenario
2. Read the [Usage Overview](usage.md) for the full workflow
3. Follow the [Scenario Guide](scenario_guide.md) to build a new scenario
4. Follow the [Study Guide](study_guide.md) to design a multi-condition study
5. Use [Experiment Studies](experiments.md) for the study runner reference
6. Use the [Dashboard](dashboard.md) to create scenarios visually
7. See the [Election Walkthrough](tutorials/election.md) for a real-world example
8. Check [Configuration Reference](configuration.md) for all knobs

## For Developers

If you want to **extend the framework** (new backends, agents, probes, initializers):

1. Read the [Usage Overview](usage.md) to understand the pipeline
2. See [Building Agents](building_agents.md) for custom builder classes
3. See [Concordia Bridge](concordia_bridge.md) for optional legacy interoperability details
4. See [Memory Initialization](memory_initialization.md) for custom initializers
5. See [Environment Layer](environment_layer.md) for Engine/GM/component configurability
6. See [Environment Backends](backends.md#adding-a-new-backend-developer-guide) for new backend apps
7. See [Evaluation Probes](probes.md#custom-probe-types) for custom probe types
8. See [Simulation Extensibility API](simulation_extensibility_api.md) for class/method contracts and extension hooks
9. Check [Contributing](contributing.md) for code standards and workflows

## For AI Coding Agents

If you're an **LLM helping with code changes or architecture**:

1. **Code Extension & Architecture**: Read `AGENTS.md` in the repository root — entry points, component system, extensibility patterns
2. **Deep Architectural Dive**: Read `agent_docs/architecture.md` in the repository root — multi-flow routing, component instance management, flow scheduling
3. **Configuration Reference**: Check [configuration.md](configuration.md) — All knobs and their effects
4. **Study Orchestration**: Read `agent_docs/scenario_design.md` in the repository root — `run_study.py` schema and evaluator presets

If you're an **LLM helping design experiments via configuration**:

1. **Scenario Design**: Read `agent_docs/scenario_design.md` in the repository root — How to create `scenarios/{name}/conf/` with persona pipelines, networks, probes
2. **Guided workflows**: Use `/new-scenario` or `/new-study` with the instruction files in `agent_docs/skills/`
3. **Config Reference**: Check [configuration.md](configuration.md) — All config values and defaults

## Quick Links

**For All Users:**
- [Installation](installation.md) — Set up the project
- [Quick Start](quickstart.md) — Run your first simulation in 5 minutes
- [Configuration Reference](configuration.md) — All config options explained

**For End Users:**
- [Usage Overview](usage.md) — End-to-end guide to the system
- [Scenario Guide](scenario_guide.md) — Build a new scenario from scratch
- [Study Guide](study_guide.md) — Design and run a multi-condition study
- [Study Schema Reference](study_schema.md) — Full study.yaml schema and file formats
- [Experiment Studies](experiments.md) — Study runner CLI reference
- [Dashboard Guide](dashboard.md) — GUI for scenario creation
- [Election Walkthrough](tutorials/election.md) — Step-by-step complex scenario tutorial

**For Code Developers:**
- [Environment Layer](environment_layer.md) — Engine/GM/backend extensibility
- [Simulation Extensibility API](simulation_extensibility_api.md) — API-style contracts for extending agents, GMs, engines, and policies
- [Documentation Coverage](documentation_coverage.md) — Coverage matrix and stale-doc register
- [Framework Roadmap](framework_roadmap.md) — Near-term framework priorities
- [Concordia Bridge](concordia_bridge.md) — Optional legacy interoperability layer
- [Building Agents](building_agents.md) — YAML pipeline and custom builders
- [Memory Initialization](memory_initialization.md) — Custom initializers
- [Environment Backends](backends.md) — Backend plugin guide
- [Evaluation Probes](probes.md) — Custom probe types
- [Contributing](contributing.md) — Code standards

**For AI Agents:**
- `AGENTS.md` in the repository root — Code extension points and architecture
- `agent_docs/architecture.md` — Multi-flow routing deep dive
- `agent_docs/scenario_design.md` — Scenario design via configuration
- `agent_docs/skills/` — Guided workflow instruction sets

## Architecture at a Glance

```mermaid
graph TD
    A[Scenario YAML] --> B[Hydra Config Composition]
    B --> C[Runner]
    C --> D[Agent Builder]
    C --> E[Memory Initializer]
    C --> F[Environment Backend]
    D --> G[Agents]
    E --> G
    F --> H[Game Master]
    G --> H
    H --> I[Simulation Loop]
    I --> J[Probe Deployment]
    I --> K[Action Logging]
    K --> L[action_events.jsonl + probe_events.jsonl + sim_metrics.json]
```

## Project Structure

```
silisocs/
├── src/silisocs/
│   ├── agents/              # Agent runtimes
│   ├── conf/                # Hydra YAML config hierarchy
│   ├── dashboard/           # Streamlit GUI
│   ├── environments/        # Environment backends + game masters
│   ├── evaluations/         # Evaluation probes
│   ├── initialization/      # Agent, GM, and simulation initialization
│   ├── runtime/             # Runner, config, simulation orchestration
│   ├── scenario_gen/        # Scenario generation helpers
│   └── simulation_engines/  # Runtime engine and policy implementations
├── scenarios/               # External scenario directories
├── docs/                    # This documentation
└── tests/                   # Test suite
```
