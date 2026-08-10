# Usage Overview

This guide covers the complete workflow for running SiliSocS simulations:
from configuration to output analysis.

The terms it leans on — **run**, **step**, **scenario**, **flow**, **game
master** — are each defined once in the [Glossary](glossary.md).

## How It Works

The simulation runs in four phases:

```mermaid
sequenceDiagram
    participant Config as Hydra Config
    participant Runner as Runner
    participant Builder as Agent Builder
    participant Init as Initializers
    participant GM as Game Master
    participant Backend as Environment Backend

    Config->>Runner: Load & compose YAML configs
    Runner->>Builder: Build agent configs
    Runner->>Backend: Construct backend
    Runner->>GM: Construct Game Master + components
    Runner->>Init: Agents, Game Masters, simulation setup
    Init->>GM: Initialize backend state and seed content
    Runner->>GM: Start Engine loop
    loop Each step
        GM->>Backend: Observe environment state
        GM->>Backend: Execute @app_action
        GM->>Runner: Log events + deploy probes
    end
```

**Phase 1, Config composition**: Hydra merges the base simulation config,
environment config, and world config into a single resolved config tree.

**Phase 2, Agent construction**: The agent builder reads the persona pipeline
(or custom builder logic) and creates agent configs with personas, memories,
and goals. Runtime construction then creates live agents.

**Phase 3, Runtime initialization**: The Engine runs agent initialization,
Game Master initialization, then simulation initialization before the main loop.

**Phase 4, Simulation loop**: Each step, agents observe environment state,
decide on an action, and the game master executes it against the configured
backend. Each backend provides its own observations through the GM component
slots; `app_observation` delegates directly to `BackendApp.observe(...)`.
Probes are deployed on schedule.

---

## Running a Simulation

For multi-condition research orchestration (hypothesis trees, seed sweeps, and
built-in evaluators), start with the [Study Guide](study_guide.md).

### CLI (Recommended)

The primary entry point is the `silisocs` CLI command:

```sh
# Check your environment first (Python, optional extras, API keys, writability)
uv run silisocs doctor

# First time? Run a guided deterministic demo (no API key needed) — it runs a
# small scripted simulation, tours the artifacts it produced, and prints the
# next commands to try
uv run silisocs tutorial

# Run with defaults
uv run silisocs

# Override parameters via Hydra
uv run silisocs num_agents=10 num_steps=5 sim.llm.name=gpt-4o

# Use a different backend
uv run silisocs env=reddit_like

# Run the packaged resource-market preset
uv run silisocs world=resource_market agents=resource_market env=resource_market

# Run the packaged virtual-space preset
uv run silisocs world=virtual_space agents=virtual_space env=virtual_space

# Run curated external backend examples
uv run silisocs --config-path scenarios/resource_market/conf world=resource_market agents=resource_market env=resource_market
uv run silisocs --config-path scenarios/virtual_space/conf world=virtual_space agents=virtual_space env=virtual_space

# Run an external scenario
uv run silisocs --config-path scenarios/election/conf
```

### Hydra CLI Overrides

Any config value can be overridden from the command line using dot notation:

```sh
uv run silisocs \
  num_agents=100 \
  num_steps=50 \
  seed=42 \
  env.gm.components.initialize.params.graph.network_type=random

# Switch GM resolve mode to tool-calling
uv run silisocs \
  env.gm.components.resolve.built_in=tool_calling \
  sim.tool_calling.mode=single

# Override only who can act next
uv run silisocs env.gm.components.next_acting.built_in=all_agents
```

### Studio

For a visual interface:

```sh
uv run silisocs-studio --output-root outputs --port 8765
```

Install the `silisocs[studio]` extra when using a lean package install. Studio
provides scenario and study composers, launch/watch controls, platform viewers,
artifact analysis, and report export in one product.

See [Studio](studio.md) for details.

---

## Validate Before You Run

`silisocs-config-dry-run` builds the *real* runtime from a config and then runs
**zero steps**. It is the cheap way to find out that a scenario is wired
correctly before you spend an hour of API budget on it — `silisocs doctor`
points at it for exactly this reason.

```sh
# Validate ONE scenario you are authoring
uv run silisocs-config-dry-run --config-path scenarios/misinformation

# Validate every scenario and replication in a checkout
uv run silisocs-config-dry-run --project-root .
```

| Flag | Default | What it checks |
|------|---------|----------------|
| `--config-path <dir>` | unset | Exactly one scenario config directory. Accepts either the config dir (`scenarios/election/conf`) or the scenario root that contains it (`scenarios/election`) |
| `--project-root <dir>` | `.` | Every config discovered under the checkout: the packaged defaults in `src/silisocs/conf/`, plus `scenarios/**/conf/` and `replications/**/conf/` |

Each config directory expands into one target per `world/*.yaml` variant plus
one per `env/*.yaml` variant, so `--config-path scenarios/misinformation`
(one world, three env variants) checks four combinations. A matching
`agents/<variant>.yaml` or `env/<variant>.yaml` is selected automatically.

Every target is forced onto the `scripted` provider (`sim.llm.provider`,
`sim.llm.name`, and `sim.llm.extra_kwargs` are overridden), so **no API key is
needed and no model is called**. Output goes to a temporary directory that is
deleted afterwards.

**Output and exit codes.** Whenever it finds targets, it prints a
`Dry-run summary: N passed, N skipped, N failed (total=N)` line, then the
command, exit code, and error tail of each failure.

| Exit code | Meaning |
|-----------|---------|
| `0` | Every target built, or only failed on a missing optional dependency (`datasets`/`hf`, `concordia`), which is reported as *skipped* rather than failed |
| `1` | At least one target failed, or no targets were discovered (nothing under `--project-root`, or a `--config-path` with no `world/*.yaml`), or `--config-path` is not a directory |

**What it catches**: config composition and Hydra group errors, unresolvable
interpolations, [config validation](configuration.md) errors, bad `class_path`
values and unknown [slot](configuration.md#slots) `params`, agent-builder and
persona-pipeline failures (including missing data files), and backend/GM/engine
construction plus runtime initialization — everything up to the first step.

**What it does not catch**: anything that only happens once agents act — action
prompt/parse behavior, [probe](probes.md) deployment and parsing, per-step
recsys refreshes, checkpointing, and of course the quality of real model output.
For that, run the scenario for a couple of steps with
`sim.llm.provider=scripted` (see the caveat in the
[Quick Start](quickstart.md#1-run-the-default-scenario)).

---

## Configuration System

The project uses [Hydra](https://hydra.cc/) for hierarchical YAML configuration
with composition. The top-level package config is
`src/silisocs/conf/experiment.yaml` and composes these groups:

```yaml
# experiment.yaml
defaults:
  - world: default         # Root run params, setting, event, and world data
  - agents: default        # Agent construction and personas
  - sim: base              # Simulation parameters
  - env: twitter_like      # Backend and GM wiring
  - eval: base            # Probes and evaluation config
```

### Config Hierarchy

```
src/silisocs/conf/
├── experiment.yaml          # Top-level composition
├── agents/
│   └── default.yaml         # Persona pipeline defaults
├── world/
│   └── default.yaml         # Root run params and default world
├── sim/
│   └── base.yaml            # LLM, engine, tool-calling, memory, checkpoints
├── env/
│   ├── twitter_like.yaml    # Local Twitter-like backend
│   ├── reddit_like.yaml     # Local Reddit-like backend
│   └── mastodon.yaml        # Remote Mastodon server
└── eval/
    └── base.yaml            # Probe and logging defaults
```

See [Configuration Reference](configuration.md) for all options. Every
pluggable piece — engines, policies, GM components, backends, checkpoint
strategies — shares one `{built_in | class_path, params}` shape, described once
under [Slots](configuration.md#slots).

For environment-level customization (Engine + GM components + backends), see
[Environment Layer](environment_layer.md).

### External Scenarios

Scenarios can live outside the package in `scenarios/<name>/conf/`:

```
scenarios/election/
├── conf/
│   ├── world/default.yaml    # @package _global_
│   ├── agents/default.yaml   # @package agents
│   ├── sim.yaml              # Optional partial sim override
│   ├── env.yaml              # Optional partial env override
│   └── eval.yaml             # Optional partial eval override
├── input/                    # Optional scenario assets (personas, news data)
├── builders.py               # Optional importable custom agent builder
└── README.md                 # Brief description for discoverability
```

Run with:

```sh
uv run silisocs --config-path scenarios/election/conf
```

The runner reads `scenario_name` from the world config automatically, so you
usually do not need a manual `world=` override. Run output does **not** land
inside the scenario directory; it goes to the Hydra-managed path under
`outputs/` described in [Output](#output).

Authoring one of these from scratch is covered step by step in the
[Scenario Guide](scenario_guide.md); the election scenario is read back key by
key in the [Election Walkthrough](tutorials/election.md).

---

## Agent Pipeline

Agents are defined in the `persona_pipeline` section of your scenario config.
There are two methods:

### Method 1: YAML Pipeline (Declarative)

Define agent classes with data sources and field mappings:

```yaml
builder:
  class_path: null
  params: {}

persona_pipeline:
  classes:
    user:
      count: 100
      class_path: silisocs.agents.native.NativeAgent
      data:
        source: inline
        records:
          - name: Alex
            persona: Alex is a local organizer who posts about public services.
      field_map:
        name: name
        context: persona
```

Hugging Face datasets are available with the `hf` extra:

```sh
pip install "silisocs[hf]"
```

```yaml
data:
  source: hf_dataset
  dataset: nvidia/Nemotron-Personas-USA
  split: train
```

### Method 2: Custom Builder (Programmatic)

Set `agents.builder.class_path` when you need programmatic agent-spec logic:

```python
from silisocs.runtime.construction.agent_builders import AgentBuilder
from silisocs.runtime.construction.specs import AgentConfig

class MyScenarioAgentBuilder(AgentBuilder):
    def build_agent_configs(self):
        return [AgentConfig(...) for _ in range(3)]
```

See [Building Agents](building_agents.md) for the full guide.

---

## Per-Agent LLM Models

You can assign different LLM models at three levels:

**Global default**, in `sim/base.yaml`:
```yaml
llm:
  provider: openai
  name: gpt-4o
```

**Per-class**, in the persona pipeline:
```yaml
classes:
  voter:
    count: 100
    model: gpt-4o-mini      # Cheaper model for voters
  candidate:
    count: 2
    model: gpt-4o            # Better model for key agents
```

`model` may also be a full LLM block that overrides `sim.llm` per-field (unset
fields fall back to global); `extra_kwargs` replaces rather than merges:
```yaml
classes:
  candidate:
    count: 2
    model:
      name: gpt-4o
      temperature: 0.2
      provider: openai
      # api_base, api_key, extra_kwargs, disabled also accepted
```

**Per-agent**, via field mapping:
```yaml
classes:
  user:
    count: 50
    data:
      source: local_json
      path: agents.json       # Must have a "model" field per record
    field_map:
      name: name
      context: persona
      model: model_name       # Maps to per-agent model assignment
```

Priority: per-agent field_map > per-class config > global default. This priority
applies to the model `name`; for a per-class block, each other field overrides
the matching global `sim.llm` field and falls back to global when unset.

---

## Social Graph And Activity

Graph fields feed the GM initialize component. Activity rates feed the GM
`next_acting` slot.

```yaml
gm:
  components:
    initialize:
      params:
        graph:
          network_type: barabasi_albert
          barabasi_albert_m: 10
          base_followership_probability: 0.3
          fully_connected_targets:
            - news_account
```

Activity selection is sim-level participation config (`conf/sim.yaml`):

```yaml
engine:
  participation:
    built_in: activity_probability
    params:
      activity_transition_rates:
        user:
          inactive_to_active: 0.3
          active_to_inactive: 0.3
```

Only active agents take actions. Two built-in activity models read these rates:

- `activity_probability` (shown above) draws each agent independently every
  step — `inactive_to_active` is the per-step probability of being active, so
  the example keeps each agent active ~30% of steps.
- `activity_markov` runs a two-state Markov chain per agent: each step an
  active agent goes inactive with `active_to_inactive`, an inactive one
  activates with `inactive_to_active` (long-run active share
  `on / (on + off)` — 50% in the example).

A rates entry may declare either key; the missing one mirrors the declared one.

---

## Fixed-Action Agents

Use `silisocs.agents.fixed.FixedAgent` when you want deterministic,
episode-aware behavior without LLM action generation.

Minimal pattern:

```yaml
persona_pipeline:
  classes:
    broadcaster:
      count: 1
      class_path: silisocs.agents.fixed.FixedAgent
      sim_role_name: broadcaster
      data:
        source: inline
        records:
          - context: Official broadcaster account
            name: Town Bulletin
      field_map:
        name: name
        context: context
      params:
        flow_tag: fixed_pre
        fixed_action_plan:
          0:
            - action_type: POST
              target_id: ""
              content: "Daily bulletin from {name}: please stay informed."
              reasoning: "Scheduled bulletin at simulation start."
          5:
            - action_type: POST
              target_id: ""
              content: "Emergency update from {name}: check local advisories."
              reasoning: "Scheduled follow-up bulletin."

sim:
  engine:
    step:
      built_in: flow
      params:
        flow_order: [fixed_pre, default]
  gm:
    components:
      observe:
        built_in: timeline_every_turn
        params:
          episode_observation_flows: [fixed_pre]
```

Behavior notes:

- Fixed agents parse episode index from observation text (for example `EPISODE: 12`).
- If no episode number is parseable, the fixed agent increments its internal counter by 1.
- The action text emitted by fixed agents is compatible with existing resolve components.
- `fixed_action_plan` is strict dict-only (`episode -> list[action]`), not list-based.
- You can load the same structure from a file using `params.fixed_action_plan_file` (`.json/.yaml/.yml`).

Compatibility notes:

- Fixed-action items use backend action names (or selectable aliases).
- `env.gm.backend.enabled_actions` and `env.gm.backend.excluded_actions` apply
  globally and can restrict fixed-action items.

---

## Memory Initialization

Before the simulation loop starts, the Engine runs agent initialization and
populates each agent's memory:

1. **Shared memories** (from config) are broadcast to all agents
2. **Generated memories** (from `generate_memories()`) are per-agent
3. **Specific memories** (per-agent, from config) are injected last

Two built-in modes:

| Mode | Config | Behavior |
|------|--------|----------|
| **Raw** | `sim.initialization.agents.built_in: raw_memory` | No LLM calls, only config memories |
| **Formative** | `sim.initialization.agents.built_in: formative_memory` | LLM-generated multi-episode backstories |

See [Memory Initialization](memory_initialization.md) for custom initializers.

---

## Evaluation Probes

Probes are periodic surveys deployed to agents during the simulation:

```yaml
probes:
  deployment:
    enabled: true
    start_step: 1
    every_n_steps: 5
  probes:
    satisfaction:
      probe_name: satisfaction
      probe_type: NumericRatingProbe
      probe_data:
        name: Satisfaction
        question: "On a scale of {lo} to {hi}, how satisfied are you?"
        lo: 1
        hi: 10
```

Built-in probe types: `NumericRatingProbe`, `BinaryProbe`, `ChoiceProbe`,
`FreeTextProbe`.

See [Evaluation Probes](probes.md) for details.

---

## Run from Python

Everything the CLI does is available in-process — compose a config, run it,
and load the artifacts back, with no subprocess and no Hydra invocation:

```python
from silisocs import compose_config, run_simulation, load_run

cfg = compose_config(
    scenario="misinformation",  # optional; like --config-path
    overrides=["num_steps=2", "sim.llm.provider=scripted"],
)
run_dir = run_simulation(cfg, output_dir="outputs/notebook_run")
artifact = load_run(run_dir)
print(artifact.status, sum(1 for _ in artifact.iter_actions()))
```

Outside a Hydra invocation, pass the `output_dir=` argument explicitly (or set
the config key of the same name, `output_dir`, in your `world` group).
`load_run`/`load_study` return the same typed artifacts Studio and the analysis
panels consume.

---

## Output

Each simulation run produces output under the Hydra-managed directory:

```
outputs/<scenario_name>/<jobname_format>/<scenario_name>_<timestamp>/
```

For the default scenario that is
`outputs/default/N10_T5_independent_run1/default_2026-05-01_12-30-00/`. The
middle level is the `jobname_format` template from the world config
(`N${num_agents}_T${num_steps}_${experiment_name}_${run_name}`); the leaf is
Hydra's job name, which carries the timestamp, so **re-running the same
parameters creates a new directory rather than overwriting the previous run**.
Hydra's own composed-config snapshot is written next to the run directory, not
inside it — `<jobname_format>/configs/<jobname_format>/` (there is no `.hydra/`
directory; `hydra.output_subdir` is redirected).

Setting a non-empty `output_dir` replaces this whole layout with the literal
path you give — see
[Output Configuration](configuration.md#output-configuration).

### Output Files

| File | Format | Description |
|------|--------|-------------|
| `run_manifest.json` | JSON | Self-describing run index: status, run params, GM/backend layout, health counters, LLM usage, artifact paths (including per-GM event logs), and provenance (git commit, package version, `uv.lock` hash). Load a run through this instead of guessing the file layout |
| `run_events.jsonl` | JSONL | The runner's live feed: versioned rows for status transitions and step boundaries (`step_started`/`step_finished`), appended as the run progresses. Tail this to observe a run — Studio's watch view does |
| `action_events.jsonl` | JSONL | Every backend action that COMMITTED a state change (or performed a deliberate logged read), with episode index, Game Master name, backend type, source user, and action data. Rejected, failed, and idempotent calls are not recorded |
| `exposure_events.jsonl` | JSONL | What each agent SAW per turn: post ids + per-post source (`follower`/`recsys:<type>`). On by default; disable via `env.gm.components.observe.params.log_exposures: false` |
| `probe_events.jsonl` | JSONL | Probe/survey responses per agent per deployment step. Written only when probes are configured and deployed |
| `prompts_and_responses.jsonl` | JSONL | Every LLM call: prompt, response, episode index, and agent name |
| `run_stats.log` | Text | Per-episode timing, worker counts, retry telemetry, and startup phase durations |
| `sim_metrics.json` | JSON | Structured metrics summary: system info, per-episode durations, worker limits, resource snapshots (CPU/memory), and aggregate statistics |
| `<platform>.db` | SQLite | Full social media state (users, posts, replies, likes, follows). Use with the [built-in visualizers](backends.md#built-in-visualizers) to browse |
| `effective_config.yaml` | YAML | The runtime-resolved config, with every `api_key` masked |

Two more files land in Hydra's own run directory,
`outputs/<scenario_name>/<jobname_format>/`, which is the **parent** of the run
directory above:

| Path | Description |
|------|-------------|
| `configs/<jobname_format>/` | Hydra's snapshot: `config.yaml` (composed config), `hydra.yaml`, `overrides.yaml` (the CLI overrides for this run), and a copy of `effective_config.yaml` |
| `<scenario_name>_<timestamp>.log` | Hydra's per-job log — the runner's Python logging output for this job, named after the Hydra job name |

Both stay outside `output_dir`, so overriding `output_dir` moves the artifact
table above but leaves these two where Hydra put them.

`effective_config.yaml` (both copies) is written with every
`api_key` masked as `**redacted**`, so a run directory stays shareable even when a
key was set in config rather than the environment.

### Run Manifest

`run_manifest.json` is the run's self-describing index — the file `load_run`
reads, and the one your own scripts should read instead of re-deriving the
layout:

| Field | Description |
|-------|-------------|
| `schema_version` | Manifest schema version (currently `1`) |
| `status` | `"success"`, `"failed"`, or `"running"` while the run is in flight. **Not** a quality signal — see [Run Health](#run-health) |
| `error` | Failure message when `status` is `"failed"`, else `null` |
| `scenario`, `seed`, `num_agents`, `num_steps` | The resolved run parameters |
| `llm_name` | The configured `sim.llm.name` — what the scenario *declared* |
| `llm_provider` | The provider the run actually **used**. `sim.llm.disabled=true` reports `"disabled"`; `sim.llm.provider=scripted` reports `"scripted"` |
| `game_masters` | One entry per GM: name, backend type and class path, database file, declared viewer capability, and event semantics |
| `llm_usage` | Token/call totals, per model and per phase (`probe`/`action`/`other`) |
| `health` | The run-health block — see [Run Health](#run-health) |
| `artifacts` | Relative paths to every artifact this run wrote, including per-GM event logs and checkpoints |
| `provenance` | Git commit/branch/dirty flag, Python version, platform, package version, and `uv.lock` hash |

Read `llm_provider` before trusting `llm_name` for provenance: an offline
`sim.llm.provider=scripted` run keeps whatever `sim.llm.name` the scenario
declared (e.g. `gpt-4o-mini`), so `llm_name` alone reads as though a real model
had been called. `llm_provider` is also mirrored into `sim_metrics.json` under
`meta`.

### Run Health

A run records what it *survived*. Each counter below is incremented during the
run, printed as a `⚠ DEGRADED RUN` line at the end, written to
`run_manifest.json` under `health`, and rendered by Studio's **Run health** panel
(and `RunArtifact.health`). Anything a run cannot legitimately continue past
raises and fails the run instead of being counted.

| Counter | Meaning |
|---------|---------|
| `agent_turn_failures` | Agent turns that raised an exception (isolated: the step continued) |
| `action_parse_failures` | Agent actions dropped as unparseable |
| `action_argument_coercion_failures` | Action arguments that did not match their declared parameter type and were passed through as raw strings (the action still ran) |
| `action_invalid_targets` | Agent actions that referenced invalid target ids |
| `backend_action_errors` | Backend actions that raised unexpectedly |
| `exposure_log_failures` | Exposure rows that could not be written to `exposure_events.jsonl` |
| `harness_tool_failures` | Harness tool calls that failed inside a harness turn |
| `recsys_update_failures` | Scheduled recommendation refreshes that failed (the run continued on the previous rows) |
| `routing_fallbacks` | Branch routing calls that fell back to a default choice |

Those nine are the whole registry
(`silisocs.evaluations.vocabulary.HEALTH_COUNTERS`), so a new counter reaches the
warning, the manifest, and the artifact loader together. Zero on every counter is
the only clean run; a non-zero count means results are partial in a specific,
named way.

The `health` block in `run_manifest.json` carries one extra key that is **not** a
counter: `silent_backends` is a *list of names* — the game masters whose backend
committed no structured action events at all, meaning analysis of that backend
will show nothing it did. It gets its own `⚠ NO ACTION EVENTS` warning line at
the end of the run, and `RunArtifact.health` synthesizes it into a count (the
length of that list) so it sits alongside the counters when you read a run back.

!!! warning "A degraded run still exits 0"
    Health counters never change the process exit code and never change
    `status`. A run in which **every** agent turn failed still finishes with
    `status: "success"` in `run_manifest.json` and exit code `0` — `status`
    only becomes `"failed"` when the run itself raised. The degraded-run
    warning on stderr and the `health` block are the signals.

    So CI and orchestration scripts must inspect manifest health, not `$?`:

    ```python
    from silisocs.evaluations.run_artifact import load_run

    run = load_run(run_dir)
    if any(run.health.values()):
        raise SystemExit(f"degraded run: {run.health}")
    ```

Note the split for recommendations: a recsys type you configured that cannot be
*initialized* (unsupported name, missing embedding dependency) is a config error
and **fails the run** — the alternative was running the whole scenario with no
recommender at all. Only a failed per-step *refresh* is survivable, and that is
what `recsys_update_failures` counts.

### Action Events Format

Each line in `action_events.jsonl` is a JSON object:

```json
{
  "episode": 3,
  "event_type": "action",
  "event_index": 42,
  "gm_name": "social_gm",
  "backend_type": "twitter_like",
  "source_user": "Alice Smith",
  "label": "post",
  "data": {
    "content": "Beautiful morning in the neighborhood!",
    "post_id": 127
  }
}
```

### Exposure Events Format

Each line in `exposure_events.jsonl` records what one agent SAW on one turn:

```json
{
  "agent": "Alex",
  "timeline_mode": "hybrid_recsys_follower",
  "recsys_type": "",
  "posts": [
    {"id": 127, "author": "blair", "source": "follower", "rank": 0},
    {"id": 131, "author": "casey", "source": "recsys:tfidf", "rank": 1}
  ],
  "gm_name": "twitter_like_gm",
  "backend_type": "twitter_like",
  "episode": 0,
  "event_type": "exposure",
  "event_index": 0
}
```

`posts` is empty when the agent's timeline had nothing to show — normal on the
first step, before anyone has posted.

### Run Events Format

`run_events.jsonl` is the live progress feed, appended as the run executes.
Every row carries a schema version `v`, a wall-clock `ts`, and a `kind`:

```json
{"v": 1, "ts": 1786320088.844, "kind": "status", "status": "running"}
{"v": 1, "ts": 1786320088.904, "kind": "step_started", "step": 0}
{"v": 1, "ts": 1786320089.029, "kind": "step_finished", "step": 0}
{"v": 1, "ts": 1786320089.159, "kind": "status", "status": "success", "error": null}
```

### Probe Events Format

Each line in `probe_events.jsonl` shares the flat event envelope
(`source_user`/`label`/`data`/`episode`/`event_type`/`event_index`) with
`action_events.jsonl`, plus a probe-specific `anchor`:

```json
{
  "source_user": "Alice",
  "label": "believed_claim",
  "data": {
    "probe_type": "BelievedClaim",
    "raw_response": "The claim about the water supply is unverified.",
    "probe_return": "no",
    "probe_mode": "single_structured_lines"
  },
  "anchor": "pre_step",
  "episode": 1,
  "event_type": "probe",
  "event_index": 0
}
```

| Field | Meaning |
|-------|---------|
| `source_user` | The probed agent's name |
| `label` | The probe's name — the same key you filter and aggregate on |
| `data.probe_type` | The probe class that produced the row |
| `data.raw_response` | The model text the probe parsed |
| `data.probe_return` | The parsed answer, or `null` when the response could not be parsed into one |
| `data.probe_mode` | How the answer was obtained: `single_structured_lines` (one batched multi-probe call), `single_probe` (one call per probe), or `single_probe_fallback` (batched parse failed, retried individually) |
| `anchor` | The loop anchor the probe fired at: `pre_step`, `post_step`, or `run_end` |
| `episode` | Step index the probe fired at (`run_end` rows carry the final step) |
| `event_index` | Monotonic index within `probe_events.jsonl` |

A `null` `probe_return` with a non-empty `raw_response` means the model answered
but the probe could not parse it — count those before drawing conclusions from a
probe series. See [Probes](probes.md) for the probe catalog and scheduling.

### Simulation Metrics

`sim_metrics.json` provides structured data for analysis. It has exactly seven
top-level keys — `system`, `meta`, `counters`, `total_sim_duration_s`,
`phase_timings`, `episode_metrics`, and `resource_snapshots`:

```json
{
  "system": {
    "platform": "Linux-5.14.0-x86_64-with-glibc2.34",
    "python": "3.12.13",
    "cpu_count_logical": 64,
    "cpu_count_physical": 64,
    "total_ram_mb": 257379.0
  },
  "meta": {
    "num_agents": 10,
    "num_game_masters": 1,
    "num_steps": 2,
    "seed": 1,
    "scenario": "default",
    "llm_name": "gpt-4o-mini",
    "llm_provider": "scripted",
    "output_dir": "/abs/path/to/run",
    "agent_names": ["Alex", "Blair", "..."],
    "llm_usage": {
      "per_model": [
        {"model": "gpt-4o-mini", "prompt_tokens": 0, "completion_tokens": 0,
         "total_tokens": 0, "calls_with_usage": 0, "calls_without_usage": 0}
      ],
      "totals": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0,
                 "calls_with_usage": 0, "calls_without_usage": 0},
      "by_phase": {"probe": {"...": 0}, "action": {"...": 0}, "other": {"...": 0}},
      "pricing_applied": false
    }
  },
  "counters": {},
  "total_sim_duration_s": 0.488,
  "phase_timings": [
    {"phase": "config_validation", "duration_s": 0.0152},
    {"phase": "build_game_masters", "duration_s": 0.0039},
    {"phase": "build_agents", "duration_s": 0.0043},
    {"phase": "model_creation", "duration_s": 0.0003},
    {"phase": "runtime_construction", "duration_s": 0.0177},
    {"phase": "engine_initialize", "duration_s": 0.0075},
    {"phase": "engine_run_loop", "duration_s": 0.2525}
  ],
  "episode_metrics": [
    {
      "episode": 0,
      "duration_s": 0.124,
      "total_agents": 10,
      "active_agents": 10,
      "skipped": false,
      "degraded": false,
      "failed_turns": [],
      "game_master": "twitter_like_gm",
      "worker_limit": 10,
      "requested_workers": 10,
      "configured_worker_cap": 1000,
      "phase_timings": {"step_action": 0.0898},
      "retry_telemetry": {
        "model_count": 1, "retries_total": 0, "calls_total": 0,
        "failed_calls_total": 0, "failure_ratio": 0.0,
        "per_model": ["..."], "usage": {"...": 0}
      },
      "probe_phase": {"deployed": false, "total_agents": 10, "selected_agents": 0,
                      "duration_s": 0.0, "worker_limit": 0, "retry": {"...": 0}},
      "action_phase": {"active_agents": 10, "duration_s": 0.0898, "failed_turns": 0,
                       "retry": {"...": 0}}
    }
  ],
  "resource_snapshots": [
    {
      "label": "sim_start",
      "timestamp": 1786320088.669,
      "cpu_percent_process": 0.0,
      "cpu_percent_system": 7.3,
      "memory_rss_mb": 69.99,
      "memory_vms_mb": 314.73,
      "memory_percent": 0.027,
      "system_memory_percent": 59.9,
      "open_file_descriptors": 1,
      "thread_count": 1,
      "gpus": []
    }
  ]
}
```

`counters` holds the run-health counters ([Run Health](#run-health)) and is
empty on a clean run. `resource_snapshots` is one row per labelled sample
(`sim_start`, `episode_<n>_end`, ...), and `episode_metrics` is one row per
step per game master.

### Visualizing Output

Open a run's backend state in the unified Studio workspace:

```sh
uv sync --extra studio
uv run silisocs-studio
```

Select **Runs**, open a run, then select **Platform**. Studio discovers viewer
capabilities declared by each backend and starts the matching read-only view;
custom backends can participate without Studio-specific code.

See [Environment Backends](backends.md#built-in-visualizers) for full details.

### Loading Runs Programmatically

Tools and notebooks should load runs through the Run Artifact Module instead of
re-discovering the file layout:

```python
from silisocs.evaluations.run_artifact import load_run, load_study

run = load_run("outputs/my_world/run_dir")
run.status, run.scenario, run.seed        # from run_manifest.json, which the
run.health, run.llm_usage, run.provenance # loader requires (see Run Health)
for event in run.iter_actions():          # streams flat or per-GM event logs
    ...

study = load_study("experiments/studies/my_study")
study.plan, study.summary, study.provenance
```

Studio loads runs through this same interface, so a run that loads here renders
in the visual analysis views.

---

## End-to-End Workflow

Here is the complete workflow for creating and running a custom world. The
config-authoring steps are compressed here — the [Scenario Guide](scenario_guide.md)
is the canonical step-by-step walkthrough, with worked files and common patterns.

### 1. Create the Scenario Directory

```sh
mkdir -p scenarios/my_world/conf/world scenarios/my_world/conf/agents
```

### 2. Write the Scenario Configs

Two files are required — run parameters plus narrative, and the agent
population. This is the shape; see the
[Scenario Guide](scenario_guide.md) for the annotated version and
[Configuration](configuration.md) for every key.

```yaml
# scenarios/my_world/conf/world/default.yaml
# @package _global_
scenario_name: my_world
jobname_format: "N${num_agents}_T${num_steps}_${experiment_name}_${run_name}"
num_agents: 3
num_steps: 10
seed: 42
run_name: my_world
output_dir: ""

setting:
  name: My Community
  background:
    - A small online community focused on technology discussions.

event:
  name: Product launch
  context: |
    A new product has been announced and community members
    are discussing its merits and drawbacks.
```

```yaml
# scenarios/my_world/conf/agents/default.yaml
# @package agents
persona_pipeline:
  defaults:
    params:
      world_context: ${event.context}
    shared_memories:
      - ${event.context}
  classes:
    user:
      count: ${num_agents}
      class_path: silisocs.agents.native.NativeAgent
      sim_role_name: user
      data:
        source: inline
        records:
          - name: Alex
            persona: Alex follows product launches and asks practical questions.
          - name: Blair
            persona: Blair studies developer tools and compares alternatives.
          - name: Casey
            persona: Casey moderates the forum and keeps discussions on topic.
      field_map:
        name: name
        context: persona

initial_observations:
  - "{name} is browsing the forum."
```

The optional `conf/env.yaml` (social graph, backend, GM components) and
`conf/sim.yaml` (per-role participation rates, LLM, engine) are shown with
worked values under
[Scenario Guide → Common patterns](scenario_guide.md#common-patterns).

### 3. Run It

```sh
uv run silisocs --config-path scenarios/my_world/conf num_agents=20 num_steps=10
```

### 4. Analyze Output

Output appears under `outputs/my_world/<jobname_format>/my_world_<timestamp>/`;
the CLI prints the exact path at startup. See [Output](#output).

### 5. (Optional) Add a Custom Builder

If you need programmatic control over agent construction:

```python
# scenarios/my_world/builders.py
from silisocs.runtime.construction.agent_builders import AgentBuilder

class MyScenarioAgentBuilder(AgentBuilder):
    def build_agent_configs(self):
        # Custom logic here
        ...
```

### 6. (Optional) Use Studio

```sh
uv run silisocs-studio --output-root outputs --port 8765
```

Create the world visually, preflight it, launch, watch artifacts grow, and inspect
the completed run without changing tools.

To replay simulation state from a previous run, use `sim.checkpoint.source_run`
in the launch overrides.

### 7. Replay from a Checkpoint

Enable checkpointing during a run:

```sh
uv run silisocs \
  --config-path scenarios/my_world/conf \
  num_steps=200 \
  sim.checkpoint.every_n_steps=10
```

Then restore from the previous output directory:

```sh
uv run silisocs \
  --config-path scenarios/my_world/conf \
  num_steps=200 \
  sim.checkpoint.source_run=outputs/my_world/run1 \
  sim.checkpoint.restore.built_in=social_action_event_replay
```

Current checkpoints store default agent state, game-master component state, and
the state of built-in local backends. For SQLite-backed backends such as
`twitter_like` and `reddit_like`, the checkpoint stores a database snapshot. The
restore strategy is only used when checkpointed backend state is absent and a
domain-specific replay is needed.

For multi-GM source runs, replay is metadata-driven and strict. The runtime uses
the restored Agent flow and the configured flow chain, then requires exactly one
GM in that chain to expose the replayed backend action.

---

## Developer Customization Guide

This section maps key runtime tasks to concrete extension points for developer
users.

### Engine Responsibilities

The runtime now includes direct engine step strategies:

- `sim.engine.step.built_in: base` (default): simple execution path, one GM active per episode.
- `sim.engine.step.built_in: sequential`: one GM, selected agents executed one by one.
- `sim.engine.step.built_in: flow`: flow-aware execution.
- `sim.engine.step.built_in: multi_gm` / `multi_gm_serial` / `multi_gm_staged`:
  flow-aware execution with multi-GM routing; the three variants select the
  flow-chain traversal mode (see below).

For the `multi_gm*` strategies, all configured Game Masters update once at the
start of each episode step, before flow routing and actor selection. Flow chains
then route selected agent turns through the configured GMs; `gm.update(...)` is
not called again inside each flow chain.

Chain traversal is selected by `sim.engine.step.built_in` (the former
`sim.engine.step.params.chain_execution` knob has been removed; a config that
still sets it raises a `ValueError` with a migration hint):

- `multi_gm` (default, concurrent): flows run as independent pipelines through
  their GM chains. Distinct flows advance concurrently and a flow's next hop
  starts as soon as its own previous hop completes — turns serialize only when
  two flows touch the same GM at the same time (the engine's existing per-GM
  lock). Flows listed in `flow_order` run first as a strict serial prefix,
  preserving declared precedence such as seed-then-act (`fixed_pre` before
  `default`); every other flow runs as the concurrent group. A single agent's own
  chain hops always stay serial, since each hop observes the prior hop's
  resolution. This is the unchanged default behavior.
- `multi_gm_serial`: legacy row-major — each flow runs its full GM chain to
  completion before the next flow, one batch at a time, in a deterministic
  flow-by-flow order.
- `multi_gm_staged`: column-major with a global per-stage barrier — `flow_order`
  flows run first as a serial prefix, then every remaining flow advances one stage
  at a time, with all flows' stage-N hops running concurrently and stage N+1
  blocked until all of stage N finishes. A `null` chain entry (empty slot) idles a
  flow at that stage so differently-shaped chains stay stage-aligned.

Checkpoint replay is per-agent-flow-chain regardless of the traversal mode.

The engine is responsible for:

- Episode loop orchestration (`run_loop`)
- Probe scheduling and deployment timing
- Selecting acting agents and action specs for each episode
- Running agent actions concurrently and resolving them through the GM (under
  the default `multi_gm` strategy, this also covers cross-flow/cross-chain
  concurrency, gated by shared-GM overlap)
- Worker throttling based on retry telemetry

Key implementation: `src/silisocs/simulation_engines/base_engines.py`.

#### Current Action Semantics

Action semantics are policy-driven through `sim.engine.turn_policy`.

- `single_action`: one resolved action per acting agent per episode.
- `fixed_count`: a fixed number of resolved actions per acting agent.
- `open_ended`: continue until stop token or max action budget.

Built-in turn policies also accept `observe_before_act: first | always | never`.
The default `first` preserves existing behavior by observing before the first
action in a repeated-action turn.

Turn scheduling is orthogonal to the policy: `sim.engine.executor` selects
`threads` (default) or `asyncio` (turns run as coroutines on one event loop —
thousands of LLM calls in flight on a few threads). Behavior is identical across
executors; see the configuration reference.

Probe timing is policy-driven through `eval.probes.schedule`.

#### Defining New Agent Behavior Flows

To introduce new class-level behavior phases without engine/resolve bloat:

1. Set `flow_tag` per class in `persona_pipeline.classes`.
2. Define phase order in `sim.engine.step.params.flow_order`.
3. Optionally add per-agent overrides with `sim.engine.step.params.agent_to_flow`.
4. Add any flow names that require episode-style observations to
  `env.gm.components.observe.params.episode_observation_flows`.

`agent_to_flow` keys must match final Agent names. Overrides are applied during
runtime construction and passed to the Engine and every Game Master as the
final flow map.

This pattern is how fixed agents run before default LLM-driven agents today,
and it generalizes to any future specialized class.

### Game Master Responsibilities

The environment GM (`GameMaster` + native components) is responsible for:

- Initializing the active backend app through its initialize component
- Building generic or timeline observations for each acting agent
- Parsing and dispatching actions (`custom`, `generic`) and tool calls (`none`, `single`, `multi`)
- Applying action effects through the backend app contract
- Managing activity-state-based actor gating

Key implementations:

- `src/silisocs/environments/gm/game_master.py`
- `src/silisocs/environments/gm/components/`

### What Developers Commonly Customize

#### Engine-side tasks

- Multi-action-per-episode turn policy (`sim.engine.turn_policy`)
- Alternative actor scheduling policies
- Probe timing policy (`eval.probes.schedule`)
- Concurrency and retry throttling strategy

#### GM-side tasks

- Action grammar and parsing through resolve components
- Action dispatch strategy by resolve mode (`parsed_action`, `generic_action`, `tool_calling`)
- Observation shaping (`app_observation`, `timeline_every_turn`, or custom component)
- Activity transition behavior through next-acting components
- Seed posts through simulation initialization, then normal `resolve_action`

### Backend Contract Tasks

For platform extensions, backend classes implement `BackendApp` and are
selected by `env.gm.backend.type` or `env.gm.backend.class_path` through the backend factory.
Typical developer tasks include adding new `@app_action` methods, observations,
optional timeline semantics, and storage/query behavior.

---

## Further Reading

- [Configuration Reference](configuration.md): Every config option explained
- [Building Agents](building_agents.md): YAML pipeline and custom builders
- [Memory Initialization](memory_initialization.md): Raw, formative, and custom modes
- [Environment Backends](backends.md): Generic apps, Twitter-like, Reddit-like, Mastodon
- [Evaluation Probes](probes.md): Probe types and deployment
- [Study Guide](study_guide.md): Multi-condition studies, seed grids, evaluators
- [Studio](studio.md): unified visual workflow and extension guide
- [Election Walkthrough](tutorials/election.md): Complex real-world example
