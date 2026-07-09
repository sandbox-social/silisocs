# Configuration Reference

Complete reference for all YAML configuration options.

## Config Groups

Configuration is split across named groups, each with a base preset in
`src/silisocs/conf/`:

| Group | Base file | Controls |
|---|---|---|
| *(root)* | `experiment.yaml` | Hydra output paths, experiment label |
| `world` | `world/default.yaml` | Run parameters, setting, event, data |
| `agents` | `agents/default.yaml` | Persona pipeline, shared memories, initial observations |
| `sim` | `sim/base.yaml` | LLM (model, API, temperature), engine, tool-calling, checkpoint |
| `env` | `env/twitter_like.yaml` | Backend construction and GM component wiring |
| `eval` | `eval/base.yaml` | Probes and evaluation timing |

---

## Top-Level Config (`experiment.yaml`)

```yaml
defaults:
  - world: default
  - agents: default
  - sim: base
  - env: twitter_like
  - eval: base
  - _self_

hydra:
  job:
    name: ${scenario_name}_${now:%Y-%m-%d_%H-%M-%S}
  run:
    dir: outputs/${scenario_name}/${jobname_format}
  output_subdir: configs/${jobname_format}

experiment_name: independent
```

Override from the CLI:

```sh
uv run silisocs env=reddit_like num_agents=500
```

---

## Run Parameters (`world/default.yaml`)

Run parameters live in the `world` config group (placed at config root via
`@package _global_`):

| Parameter | Default | Description |
|-----------|---------|-------------|
| `num_agents` | `10` | Number of agents to create (any value works; personas recycle past the bundled 100, see below) |
| `num_steps` | `5` | Simulation steps to run |
| `run_name` | `run1` | Run identifier (used in output path) |
| `seed` | `1` | Random seed |
| `scenario_name` | `default` | Scenario identifier (used in output path) |
| `jobname_format` | *(template)* | Output directory name template |
| `experiment_name` | `independent` | Experiment label used in `jobname_format` |

---

## Sim Parameters (`sim/base.yaml`)

### LLM

| Parameter | Default | Description |
|-----------|---------|-------------|
| `sim.llm.provider` | `openai` | Model provider: `openai`, `openai_compatible`, `scripted`, `disabled`, a built-in preset (see below), a registered name, or a class path |
| `sim.llm.name` | `gpt-4o-mini` | LLM model name (passed to the model factory) |
| `sim.llm.api_base` | `null` | Required base URL when `provider: openai_compatible`; also overrides the base URL of a built-in preset |
| `sim.llm.api_key` | `null` | API key (or set via the provider's environment variable) |
| `sim.llm.temperature` | `0.5` | Sampling temperature |
| `sim.llm.disabled` | `false` | Use a no-op model (for testing without API calls) |
| `sim.llm.extra_kwargs` | `{}` | Provider request kwargs such as OpenAI-compatible `extra_body` settings |
| `sim.llm.pricing` | `null` | Optional `{input_per_1m, output_per_1m}` USD rate for cost reporting |

**Token usage & cost.** OpenAI-compatible providers record `response.usage`
per call, split by phase — `probe` (evaluation spend; the loop brackets probe
deployment the same way scheduling brackets action turns), `action` (the
simulation itself), and `other` (initialization and out-of-band calls). Per-model
token totals ride into `sim_metrics.json` under
`episode_metrics[].retry_telemetry` (per-episode cumulative) and a run-level
`meta.llm_usage` summary with `per_model`, `totals`, and a `by_phase` split, so
instrumentation cost is separable from experiment cost. Providers that
omit `usage` are counted under `calls_without_usage` rather than guessed. Setting
`sim.llm.pricing` adds `estimated_cost_usd` per model, per phase, and overall — a
single rate applied to every model, so for a mixed-model run with differing real
prices read the per-model token counts and price them yourself. `pricing` is telemetry-only:
it never affects model construction or the effective-config dedup.

**Built-in provider presets.** Common providers that expose an OpenAI-compatible
API are available as named presets. Set `sim.llm.provider` to the name and supply
the key via the listed environment variable (or `sim.llm.api_key`); `sim.llm.name`
selects the model.

| Preset | Endpoint | API key env var |
|--------|----------|-----------------|
| `anthropic` | `https://api.anthropic.com/v1/` | `ANTHROPIC_API_KEY` |
| `gemini` | `https://generativelanguage.googleapis.com/v1beta/openai/` | `GEMINI_API_KEY` |
| `openrouter` | `https://openrouter.ai/api/v1` | `OPENROUTER_API_KEY` |
| `groq` | `https://api.groq.com/openai/v1` | `GROQ_API_KEY` |
| `together` | `https://api.together.xyz/v1` | `TOGETHER_API_KEY` |
| `deepseek` | `https://api.deepseek.com` | `DEEPSEEK_API_KEY` |
| `mistral` | `https://api.mistral.ai/v1` | `MISTRAL_API_KEY` |
| `fireworks` | `https://api.fireworks.ai/inference/v1` | `FIREWORKS_API_KEY` |
| `xai` | `https://api.x.ai/v1` | `XAI_API_KEY` |
| `ollama` | `http://localhost:11434/v1` | none (local) |

These presets route through the OpenAI-compatible client, so they inherit the same
retry, backoff, and telemetry support. Anthropic and Gemini are reached through
their OpenAI-compatible endpoints. For a provider not listed here, use
`provider: openai_compatible` with an explicit `sim.llm.api_base`, or register a
custom provider (see [Building Agents](building_agents.md)).

### Engine and Runtime

| Parameter | Default | Description |
|-----------|---------|-------------|
| `sim.max_concurrent_actions` | `1000` | Max parallel LLM calls per step |
| `sim.action_mode` | `custom` | Prompt style: `custom` (world prompt) or `generic` (backend-generated) |
| `sim.tool_calling.mode` | `single` | Tool dispatch mode: `none`, `single`, or `multi` |
| `sim.prompt_additions.action_count_guidance` | `true` | Add `[ActNum]` marker and action count guidance to prompt |
| `sim.checkpoint.every_n_steps` | `null` | Save checkpoints every N steps when set |
| `sim.checkpoint.explicit_steps` | `[]` | Additional explicit checkpoint steps |
| `sim.checkpoint.source_run` | `null` | Previous output directory to restore from (explicit resume) |
| `sim.checkpoint.auto_resume` | `true` | Resume from this run's own output directory if it already contains checkpoints; ignored when `source_run` is set |
| `sim.checkpoint.save.built_in` | `monolithic_json` | On-disk checkpoint layout: `monolithic_json` (one JSON per step, the long-standing format) or `sharded` (a `step_N_checkpoint.json` manifest + NDJSON object shards + raw SQLite sidecar `.db` files with sha256; `params.objects_per_shard`, default 500). Restore reads both layouts transparently; `class_path` accepts a custom `CheckpointSaveStrategy` |
| `sim.checkpoint.restore.built_in` | `social_action_event_replay` | Checkpoint restore strategy when `source_run` is set |
| `sim.telemetry.record_active_agent_names` | `false` | Retain each episode's active-agent *name list* in `sim_metrics.json` (kept in memory for the whole run — O(active × steps)). Counts (`active_agents`) are always recorded |
| `sim.engine.step.built_in` | `base` | Engine step policy: `base`, `sequential`, `flow`, `multi_gm`, `multi_gm_serial`, or `multi_gm_staged`. The three `multi_gm*` strategies select the flow-chain traversal mode: `multi_gm` (concurrent, default — flows advance as independent pipelines, serializing only when two flows touch the same GM), `multi_gm_serial` (legacy row-major — each flow runs its full GM chain to completion before the next), `multi_gm_staged` (column-major with a global per-stage barrier — all flows advance one stage at a time) |
| `sim.engine.step.params.gm_turn_policies` | `{}` | Per-GM turn policy overrides keyed by GM name, each value using the same slot shape as `sim.engine.turn_policy` (`{built_in\|class_path, params}`); applies under any step mode and is resolved per batch by GM name |
| `sim.engine.step.params.gm_concurrency_caps` | `{}` | Per-GM concurrency caps (`{gm_name: int}`); caps how many of that GM's agent turns run at once via a per-GM semaphore. Effective per-GM limit = `min(cap, sim.max_concurrent_actions)`; empty map = the global cap governs every GM. Applies under any step mode |
| `sim.engine.turn_policy.built_in` | `single_action` | Global turn policy — how many actions an agent takes per step: `single_action`, `fixed_count`, or `open_ended` |
| `sim.engine.executor` | `threads` | Turn executor: `threads` (one pool worker per in-flight turn) or `asyncio` (turns run as coroutines on one background event loop — thousands of LLM calls in flight on a handful of threads). Agents/models that provide `act_async` / `sample_*_async` (the shipped `NativeAgent`, `FixedAgent`, and OpenAI-compatible providers do) run loop-native; sync-only custom agents, models, and turn policies automatically run on helper threads, and both kinds mix freely in one step. `sim.max_concurrent_actions` keeps its meaning — max in-flight turns — under either executor; scheduling semantics (flow chains, barriers, per-GM caps and locks) are identical |
| `sim.engine.participation.built_in` | `activity_probability` (base.yaml) | Sim-level roster filter applied before scheduling, every GM `next_acting`, **and per-step GM `update`s** (so per-step backend work like the recsys refresh is O(active), not O(population); update components that need the full population declare `requires_full_roster = True`): `all` (pass-through), `activity_probability`, `activity_markov`, or a `class_path`. Effective acting = participation ∩ `next_acting`. Set `all` for deterministic/turn-based runs. A missing slot defaults to `all` in code; `sim/base.yaml` ships `activity_probability` |
| `sim.engine.class_path` | `null` | Whole-engine swap seam: a fully-qualified path to a custom `RuntimeEngine` subclass. When set, the engine is that class, built with the standard kwargs (`config`, `loop_strategy`, `step_strategy`, `turn_policy`, `gm_turn_policies`, `gm_concurrency_caps`, `participation`, `seed`, `executor`); unset uses the built-in `RuntimeEngine`. Prefer the policy seams (loop/step/turn_policy/participation `class_path`) unless you must replace the engine lifecycle itself |
| `sim.roleplaying_instructions` | *(template)* | System prompt injected into every agent. Use `{name}` placeholder. |

### Per-GM action_mode and tool_calling overrides

`sim.action_mode` and `sim.tool_calling.mode` set the global defaults. A single
default GM (`env.gm`) and each orchestrated GM (`env.gm_orchestration.gms[*]`)
may additionally set optional per-GM overrides:

```yaml
env:
  gm_orchestration:
    gms:
      - name: social_gm
        action_mode: generic
        tool_calling: single        # scalar: none | single | multi
        # ... backend + components ...
```

Each unset key falls back to the global `sim.action_mode` / `sim.tool_calling.mode`
(today's behavior). The per-GM override is a **scalar** `tool_calling: <mode>`
(a sibling to the scalar `action_mode`). The retired `tool_calling_mode` key and the
`tool_calling: {mode: ...}` block form each raise a migration error pointing at the
scalar spelling — the block form survives only at the global `sim.tool_calling.mode`.

Resolve compatibility is validated **per GM** against each GM's *effective*
tool-calling mode: a GM whose effective mode is `single`/`multi` must pair its
`components.resolve.built_in` with `tool_calling`, and a GM whose effective mode is
`none` must not use the `tool_calling` resolve component.

---

## Running and Creating Scenarios

Scenarios live in `scenarios/{name}/conf/` and override package defaults.
The run command:

```bash
uv run silisocs --config-path scenarios/misinformation/conf
```

### Directory Structure

```
scenarios/
└── my_world/
    └── conf/
        ├── world/
        │   └── default.yaml             # Run parameters + setting/event/data
        ├── agents/
        │   ├── default.yaml             # Persona pipeline, shared memories
        │   └── thin.yaml                # Lightweight variant (optional)
        ├── env.yaml                     # Platform/GM overrides (optional)
        ├── eval.yaml                   # Probe config overrides (optional)
        └── sim.yaml                     # LLM + engine overrides (optional)
```

### How Config Overrides Work

Two mechanisms layer on top of the package defaults:

**Layer 1, Hydra SearchPath Plugin**: a registered `SearchPathPlugin` prepends
the scenario conf dir to Hydra's search path before composition. This gives
`world/default.yaml` and `agents/default.yaml` from the scenario conf dir
higher priority than the package defaults, so they replace the package
`world/default.yaml` and `agents/default.yaml` entirely.

**Layer 2, Manual merge** (runs inside `main()` after Hydra composes): handles
partial-override flat files that don't replace their group wholesale:

- `env.yaml`, `eval.yaml`, `sim.yaml` → merged into their named groups

**Priority order** (highest → lowest):

1. CLI overrides (`num_steps=1 sim.llm.provider=scripted`)
2. Scenario flat files merged in Layer 2 (`env.yaml`, `sim.yaml`, …)
3. Scenario `world/default.yaml` and `agents/default.yaml` (via plugin searchpath)
4. Package defaults in `src/silisocs/conf/`

CLI overrides are re-applied after the merge so they always win over
scenario defaults.

### Running a Scenario

```bash
# Run with scenario defaults
uv run silisocs --config-path scenarios/election/conf

# Override specific parameters
uv run silisocs --config-path scenarios/election/conf \
    num_agents=500 num_steps=100

# Use alternate agents variant
uv run silisocs --config-path scenarios/ai_conference/conf \
    agents=thin

# Dry-run with no LLM calls (for testing)
uv run silisocs --config-path scenarios/misinformation/conf \
    num_steps=1 sim.llm.provider=scripted

# View merged config before running
uv run silisocs --config-path scenarios/election/conf --cfg job
```

### Creating a New Scenario

**Option 1: Via Dashboard**

1. Start the dashboard: `streamlit run src/silisocs/dashboard/launch_app.py`
2. Modify all settings (agents, network, probes, etc.)
3. Enter a new scenario name in the "Scenario name" field
4. Click "Save Scenario": creates files under `scenarios/{name}/conf/`
5. Click "Run Simulation"

**Option 2: Manual**

```bash
mkdir -p scenarios/my_world/conf/world scenarios/my_world/conf/agents
```

**`scenarios/my_world/conf/world/default.yaml`**, run parameters and narrative:
```yaml
# @package _global_
scenario_name: my_world
jobname_format: "N${num_agents}_T${num_steps}_${run_name}"
num_agents: 50
num_steps: 20
seed: 42
run_name: my_world

setting:
  name: My Setting
  background:
    - Background detail 1

event:
  name: My Event
  context: |
    Event description used in agent memories.

data: {}
```

**`scenarios/my_world/conf/agents/default.yaml`**, personas:
```yaml
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
            persona: Alex follows local policy and posts practical updates.
          - name: Blair
            persona: Blair follows technology news and likes concise debates.
      field_map:
        name: name
        context: persona

shared_memories:
  - ${event.context}

initial_observations:
  - "{name} opens their social media feed."
```

Every final agent spec must have a unique `name`. For most persona-pipeline
sources, map it with `field_map.name`. The default builder can derive names for
the known `nvidia/Nemotron-Personas-USA` persona dataset, and custom builders can
derive names as part of their own config-to-spec logic. Runtime construction
still rejects unnamed or duplicate specs before the simulation starts. Agent
names are the runtime identities used by GMs, backends, flows, probes, logs, and
checkpoints.

**`scenarios/my_world/conf/env.yaml`**, optional backend/GM overrides:
```yaml
gm:
  components:
    initialize:
      params:
        graph:
          base_followership_probability: 0.3
          network_type: barabasi_albert
          barabasi_albert_m: 10
```

**`scenarios/my_world/conf/sim.yaml`**, optional sim overrides — activity rates
are sim-level participation config (see "Participation" below):
```yaml
engine:
  participation:
    built_in: activity_probability
    params:
      activity_transition_rates:
        user:
          inactive_to_active: 0.5
          active_to_inactive: 0.2
```

### Output Structure

Simulation outputs go to: `outputs/{scenario_name}/{jobname_format}/`

```
outputs/
└── my_world/
    └── N50_T20_my_world/
        ├── my_world_2026-01-01_12-00-00/
        │   ├── effective_config.yaml      # Full resolved config
        │   ├── sim_metrics.json           # Timing and run stats
        │   ├── action_events.jsonl        # Per-step action log
        │   ├── probe_events.jsonl         # Probe outputs
        │   └── checkpoints/               # Step checkpoints (if enabled)
        └── configs/N50_T20_my_world/
            ├── config.yaml                # Hydra-composed config snapshot
            └── effective_config.yaml      # Runtime-resolved config
```

---

## World Config (`world/default.yaml`)

Defines run parameters and the narrative context. Uses `@package _global_` so
all keys are placed at the config root. Scenario-specific content lives in
`scenarios/*/conf/world/default.yaml`.

```yaml
# @package _global_
scenario_name: my_world
num_agents: 50
num_steps: 20
seed: 42
run_name: my_world
jobname_format: "N${num_agents}_T${num_steps}_${run_name}"

setting:
  name: My Community
  background:
    - A social media community with distinct user groups.

event:
  name: The Event
  context: |
    Full narrative context injected into agent memories.

data: {}   # Scenario-specific structured data (e.g. news_file)
```

`${event.context}` and `${setting.background}` are available as interpolation
targets in `agents/default.yaml` and other config files.

---

## Agents Config (`agents/default.yaml`)

Defines the persona pipeline, shared memories, and initial observations. Uses
`@package agents` so all keys are nested under `agents.*`. Scenario-specific
content lives in `scenarios/*/conf/agents/default.yaml`.

### Persona Pipeline

```yaml
# @package agents
persona_pipeline:

  defaults:                     # Applied to all classes
    params:
      world_context: ${event.context}
      seed_post: ""
      bio: ""
      style: ""
      goal: null
    shared_memories:
      - "A shared memory for all agents."
    field_map:
      name: name
      context: persona

  classes:
    <class_name>:
      count: ${num_agents}              # Number of agents in this class
      class_path: silisocs.agents.native.NativeAgent
      sim_role_name: user               # Role name for activity rates
      flow_tag: default                 # Optional class-level flow tag
      model: null                       # Per-class LLM override: scalar name OR
                                        # a full {name, temperature, provider,
                                        # api_base, api_key, extra_kwargs,
                                        # disabled} block overriding sim.llm
                                        # per-field (unset fields fall back to
                                        # global). Models are deduped by effective
                                        # config, so a no-override run still shares
                                        # one model while classes differing in any
                                        # field get distinct model objects.
      data:
        source: inline                 # inline | config_path | local_json | hf_dataset
        records:
          - name: Alex
            persona: Alex follows local policy and posts practical updates.
      field_map:
        name: name
        context: persona
      params:
        goal: "Have a productive discussion."
      shared_memories:
        - "Class-specific memory."

shared_memories:
  - ${event.context}

initial_observations:
  - "{name} is at home checking their social media feed."
```

### Data Sources

| Source | Required Keys | Description |
|--------|--------------|-------------|
| `hf_dataset` | `dataset`, `split` | HuggingFace Datasets (cached after first download) |
| `inline` | `records` | Records defined directly in YAML |
| `config_path` | `path` | Dot-path reference into another config section (e.g. `candidates`) |

### Count vs. available records (persona recycling)

A class's `count` (commonly `${num_agents}`) sets how many agents the class
builds; the data source supplies the persona records. The builder reconciles the
two automatically:

- **`count` ≤ records**: the record list is truncated to `count`.
- **`count` > records**: the records are **recycled** to reach `count`, and each
  extra pass gets a numbered suffix so agent names stay unique
  (`Alex`, `Alex 2`, `Alex 3`, …). A single `WARNING` is logged naming the class
  and the shortfall.

This means any `num_agents` works out-of-the-box. There is no silent cap at the
record count. The bundled default agents config ships **100 distinct starter
personas**, so the default scenario scales to large agent counts before any
recycling happens. For fully distinct personas at larger scale, point the class
at a bigger data source (`csv`, `jsonl`, or `hf_dataset`) instead of relying on
recycling.

### `num_agents` vs. per-class `count`

There are two related knobs, and it is important to understand which one is
authoritative:

| Field | Scope | Role |
|-------|-------|------|
| `num_agents` | run param (config root) | **Declared total.** Convenience value used in the job name and run metadata, and commonly referenced as `count: ${num_agents}`. |
| `count` | per persona-pipeline class | **Authoritative.** How many agents that class builds. |

The **actual** number of agents is the **sum of every class's `count`**: it is
neither capped nor padded to `num_agents`. In the default config one class uses
`count: ${num_agents}` and the others use `count: 0`, so the totals agree. If you
add classes with explicit counts, make sure they **sum to `num_agents`** (set
unused classes to `count: 0`).

If the built total diverges from `num_agents`, a `WARNING` is logged at build
time (and the dashboard's Launch tab shows the same mismatch), since this usually
indicates the class counts were not kept in sync with the declared total.

### Alternate Agents Variants

Create additional files alongside `default.yaml` for lightweight or experimental
variants:

```
agents/
├── default.yaml    # Full persona set
└── thin.yaml       # Minimal personas for fast testing
```

Select at runtime using the Hydra config group override syntax:
```bash
uv run silisocs --config-path scenarios/ai_conference/conf \
    agents=thin
```

---

## Env Config (`env/twitter_like.yaml`)

### Backends

**Twitter-like (default)**
```yaml
gm:
  backend:
    type: twitter_like
    class_path: null
    params: {}
```

**Reddit-like**
```yaml
gm:
  backend:
    type: reddit_like
    class_path: null
    params: {}
```

**Mastodon (remote)**
```yaml
gm:
  backend:
    type: mastodon
    class_path: null
    params:
      perform_operations: false
      reset_server_on_setup: false
```

Dry-run is the packaged default. Live mutation requires `silisocs[mastodon]`,
server URL/API credentials, and an explicit
`env.gm.backend.params.perform_operations=true` override. Server clearing is
separately gated by `env.gm.backend.params.reset_server_on_setup=true`.
See [Installation](installation.md) for `.env` setup.

**Resource market**
```yaml
gm:
  backend:
    type: resource_market
    class_path: null
    params:
      initial_cash: 20
      initial_inventory:
        food: 1
        wood: 0
        ore: 0
      production_capabilities:
        farmer: {food: 2}
        woodworker: {wood: 2}
        miner: {ore: 2}
      role_needs:
        farmer: {wood: 1}
        woodworker: {food: 1}
        miner: {food: 1}
      upkeep_interval: 2
```

**Virtual space**
```yaml
gm:
  backend:
    type: virtual_space
    class_path: null
    params:
      rooms: [atrium, garden, workshop]
      starting_room: atrium
      room_descriptions:
        atrium: A bright central hall with paths to every other room.
        garden: A quiet garden for private conversations.
        workshop: A practical room filled with tools and shared projects.
      connections: null          # null = fully connected; or a map of room -> [reachable rooms]
      room_tasks:
        - task_id: welcome_board
          room: atrium
          description: Prepare a shared welcome board.
          required_effort: 2
          completion_message: The welcome board summarizes the group's first impressions.
```

Custom backend apps can be loaded without editing the factory:

```yaml
gm:
  backend:
    type: custom
    class_path: my_pkg.apps.MyBackendApp
    params:
      custom_setting: value
```

`gm.backend.params` are strict constructor arguments. Unknown keys fail before the
simulation starts unless the app constructor accepts `**kwargs`.

### Enabled Actions

By default agents can use all backend actions. Restrict to a subset:

```yaml
env:
  gm:
    backend:
      enabled_actions:
        - create_tweet
        - reply_to_tweet
        - like_tweet
        - FINISHED
      excluded_actions:
        - report_post
```

Action names may be canonical decorated backend function names or selectable
aliases such as `FINISHED`. Unknown names fail during backend construction. If
an action is matched by both `enabled_actions` and `excluded_actions`, the run
fails loudly instead of guessing which list wins.

### Action Aliases (agent-facing renaming)

Give backend actions simpler/different agent-facing names without editing backend
code, to simplify the action vocabulary agents see and emit:

```yaml
env:
  gm:
    backend:
      action_aliases:
        create_tweet: post            # rename: agents see + call "post"
        like_tweet: [like, fav]       # "like" is displayed; "fav" also accepted
```

- The key is an existing action (its canonical method name or current selectable
  name); the value is a single new name (rename) or a list (the first is shown to
  agents, all are accepted by the parser).
- The renamed name appears in the auto-generated action catalog/prompt, and the
  canonical name plus every alias all resolve to the same action.
- Works across resolve modes: `generic`/`tool_calling` dispatch aliases directly;
  the custom `parsed_action` parser receives the token normalized to the
  canonical method name.
- Unknown actions, empty names, or a name that collides with another action fail
  loudly at backend construction. Aliases are applied before
  `enabled_actions`/`excluded_actions`, so filters may reference either vocabulary.

#### Per-flow action filters

`env.gm.backend.enabled_actions`/`excluded_actions` apply to every agent on that
backend. To restrict the action surface *per flow* (e.g. a `lurker` flow that
may only `like`/`repost` while a `poster` flow may publish), add a
`flow_action_filters` map on the resolve component. It is enforced at resolve
time (the disallowed action is rejected before the backend runs) and only ever
*further-restricts* the backend-wide filter:

```yaml
env:
  gm:
    components:
      resolve:
        built_in: parsed_action        # parsed_action | generic_action | tool_calling
        params:
          flow_action_filters:
            default:                    # fallback for any unlisted flow
              enabled_actions: null     # null => all backend actions
            lurker:
              enabled_actions: [like, repost]
            poster:
              excluded_actions: [follow_user]
```

Keys are flow tags (from `persona_pipeline.classes.<class>.flow_tag` or
`sim.engine.step.params.agent_to_flow`); values reuse the same
`enabled_actions`/`excluded_actions` vocabulary as the backend filter and match
both canonical and selectable names. This works on the default
`ComponentGameMaster` (no `MultiFlowGameMaster` needed). The terminal `FINISHED`
signal is never blocked, so open-ended flows can always terminate. Omitting the
key preserves current behavior. In `custom` (`parsed_action`) mode, specify
filters using the agent-facing verbs the parser emits (`post`, `like`, `reply`,
`repost`); in `generic`/`tool_calling` mode, use backend action names. Names that
match no backend action are matched literally and logged as a warning to catch
typos. Per-flow filtering enforces; to also *hide* actions from a flow's prompt,
give that flow its own `action_prompt` instance via `MultiFlowGameMaster`.

| Backend | Common actions |
|---------|----------------|
| `twitter_like` | `create_tweet`, `reply_to_tweet`, `like_tweet`, `unlike_tweet`, `repost_tweet`, `quote_repost_tweet`, `follow_user`, `unfollow_user`, `mute_user`, `unmute_user`, `search_posts`, `get_trending_posts`, `report_post`, `update_profile`, `view_profile`, `do_nothing`, `FINISHED` |
| `reddit_like` | `create_reddit_post`, `create_comment`, `upvote`, `downvote`, `unlike_post`, `dislike_post`, `undo_dislike_post`, `get_home_feed`, `get_post_comments`, `search_subreddits`, `get_trending_posts`, `report_post`, `mute_user`, `unmute_user`, `update_profile`, `view_profile`, `do_nothing`, `FINISHED` |
| `mastodon` | `post_toot`, `reply_to_toot`, `like_toot`, `boost_toot`, `follow_user`, `unfollow_user` |

### Seed Posts

Initialize agent feeds with background posts before the simulation starts:

| Type | Description |
|------|-------------|
| `agent` | Ask agents for starting posts through their normal act path |
| `csv` | Pre-written posts from a CSV file (`agent_name,post_text`) |
| `json` | Pre-written posts from a JSON file (`{"agent_name": "post_text"}`) |
| `none` | Disable seed posts (organic growth only) |
| `fallback` | File values first, agent-generated posts for missing agents |

```yaml
sim:
  initialization:
    simulation:
      built_in: seed_posts
      class_path: null
      params:
        type: agent
        params:
          file_path: null   # Path to CSV/JSON file when type is csv/json/fallback
```

Agent and Game Master initialization are configured separately:

```yaml
sim:
  initialization:
    agents:
      built_in: raw_memory
      class_path: null
      params: {}
    game_masters:
      built_in: default
      class_path: null
      params: {}
```

Each native Game Master has an initialize component slot:

```yaml
env:
  gm:
    components:
      initialize:
        built_in: social_media   # social_media | app_initialize | none
        class_path: null
        params: {}
```

### Agent Memory (`sim.memory`)

`sim.memory` governs how a NativeAgent *records* observations and *renders* the
"Memory" section of its prompt at runtime — distinct from
`sim.initialization.agents`, which only SEEDS memories at step 0.

```yaml
sim:
  memory:
    built_in: window            # window | retrieval | summarizing
    class_path: null
    params: {}
```

| Built-in | Behavior | Params |
|----------|----------|--------|
| `window` (default) | Keep the last N memories, render the last `render_count`. Byte-identical to the pre-slot behavior. | `render_count` (10); store cap defaults to the agent's `memory_history` |
| `retrieval` | A recency window PLUS relevance recall: always render the last `window_count` memories verbatim, and prepend the `retrieved_count` OLDER memories most relevant to the current observation by deterministic lexical overlap (recency tiebreak) — replay-stable, no embedding API. `window_count: 0` recovers pure retrieval; `retrieved_count: 0` is a plain window. | `window_count` (40), `retrieved_count` (10) |
| `summarizing` | Three tiers: all rolling summaries, then the `retrieved_count` most relevant OLDER memories, then the recent `render_count` window. When memory exceeds `max_memories`, the oldest `chunk_size` are compressed into one summary via a model call. | `max_memories` (200), `chunk_size` (50), `max_summaries` (20), `render_count` (40), `retrieved_count` (10), `prompt` |

A custom policy is `class_path` to a `MemoryPolicy` subclass (built with
`params`). Unknown `params` keys fail loudly before the run starts (a typo'd
param must not silently run with defaults); the built-in framework kwargs
(`model`, `memory_history`) are the only silently-filtered names. Determinism:
`window`/`retrieval` are deterministic; `summarizing` is only as reproducible as
the model it calls (summarization runs at record/observe time, so its tokens are
counted under the caller's phase in [token usage](#llm): `action` inside a turn,
`other` for observes outside one — agent initialization,
`broadcast_observation`). Memory rides inside the agent's
checkpoint state, so it resumes without extra plumbing; `summarizing` persists
its summaries so a resumed run never re-summarizes. Applies to NativeAgent —
Concordia-compat agents manage memory through their own components.

### GM Components

```yaml
env:
  gm:
    components:
      next_acting:
        built_in: all_agents            # all_agents | fixed_order (activity models moved to sim.engine.participation)
      observe:
        built_in: timeline_every_turn   # app_observation | timeline_every_turn | episode_only
        params:
          episode_observation_flow: fixed_pre
      resolve:
        built_in: tool_calling          # parsed_action | generic_action | tool_calling
      update:
        built_in: app_update            # app_update | social_recommendation | disabled | none
```

Component `params` are strict constructor arguments. Unknown keys fail before
the simulation starts unless the target component accepts `**kwargs`. Observe
components that explicitly accept `observation_params` can use `params` as
forwarded observation settings.

### Social Setup and Participation

Graph fields are owned by the GM initialize component (environment layer).
Activity selection is owned by the sim-level participation policy
(`sim.engine.participation`): it filters which agents are in each step's roster
before any scheduling and before every GM's `next_acting` component runs
(effective acting = participation filter ∩ next_acting output).

```yaml
env:
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
sim:
  engine:
    participation:
      built_in: activity_probability  # all | activity_probability | activity_markov
      class_path: null                # or a custom ParticipationPolicy class
      params:
        active_probability: null      # global override; null = per-role rates below
        min_active_agents: 1          # top up a too-small draw (deterministic)
        activity_transition_rates:
          <role_name>:
            inactive_to_active: 0.3
            active_to_inactive: 0.3
```

Participation policies are pure functions of `(agent_names, step_index, seed)` —
stateless, so runs replay and resume identically with nothing to checkpoint.
`activity_markov` re-derives its per-agent activity chain from step 0 on each
call. A custom policy subclasses
`silisocs.simulation_engines.policies.participation.ParticipationPolicy` and is
referenced via `class_path`; declare a `sim_roles` constructor param to receive
the agent→role mapping. Set `built_in: all` (pass-through) for deterministic or
turn-based runs (e.g. `fixed_order` environments), where every agent should stay
in the roster.

Two defaults to keep straight: `sim/base.yaml` ships `built_in:
activity_probability`, so any scenario inheriting base without overriding the
slot gets probability filtering (a new turn-based scenario must set `built_in:
all` explicitly). When the `participation` slot is *absent entirely* — e.g. a
programmatic `build_engine` call — the code fallback is `all` (pass-through),
not `activity_probability`; the active default is opted into by base.yaml, not
by the engine.

### Timeline Observation

```yaml
env:
  gm:
    components:
      observe:
        params:
          timeline_mode: follower_chronological
```

| Mode | Backends | Description |
|----------|-----------|-------------|
| `follower_chronological` | All | Recent posts from followed users, no algorithm |
| `pure_recsys` | Twitter, Reddit | Algorithm-selected posts only |
| `hybrid_recsys_follower` | Twitter, Reddit | Blend of recommendations + followed posts |
| `curated_global` | Twitter only | Trending posts + personalized recommendations |

#### Exposure logging

The timeline observe component records what each agent SAW — the post ids +
per-post `source` (`follower` / `recsys:<type>`) shown each turn — to
`exposure_events.jsonl` (mirroring `action_events.jsonl`, per-GM directories and
all). Exposure→action is the unit of analysis for recommender/platform studies;
`silisocs.evaluations.exposure.exposure_action_join(run_dir)` computes per-agent
engagement of shown posts. On by default; disable with:

```yaml
env: {gm: {components: {observe: {params: {log_exposures: false}}}}}
```

It logs ids/source only (not content — recoverable by id), so the payload stays
small, and it no-ops for backends without a SQLite timeline (e.g. Mastodon).

---

## Evals Config (`eval/base.yaml`)

```yaml
probes: {}              # See Probes section below
```

### Probes

```yaml
probes:
  probe_lib_module: null   # Optional custom probe type module

  deployment:
    enabled: true
    start_step: 1
    every_n_steps: 1
    include_agents: []   # Empty = all agents
    exclude_agents: []
    include_classes: []  # Filter by persona class / sim role
    exclude_classes: []
    include_flows: []    # Filter by flow tag; empty = all flows
    exclude_flows: []
    sample_k: null       # After the filters: probe at most K agents per due step
    sample_fraction: null  # ...or ceil(fraction * filtered), in (0, 1]; not both

  probes:
    favorability:
      probe_name: favorability
      probe_type: NumericRatingProbe
      probe_data:
        name: Favorability
        question: "Return a single rating from {lo} to {hi}."
        lo: 1
        hi: 10
```

Deployment filters select which agents receive probes and are applied as a
sequential `AND`: `include_classes` → `exclude_classes` → `include_agents` →
`exclude_agents` → `include_flows` → `exclude_flows`. `include_flows`/
`exclude_flows` target by flow tag: e.g. `include_flows: [treatment]` deploys
probes only to agents whose materialized flow is `treatment`, ideal for measuring
a treatment cohort. Flow tags come from the same source as scheduling
(`persona_pipeline.classes.<class>.flow_tag` + `sim.engine.step.params.agent_to_flow`)
and are resolved from the game master's authoritative `agent_flow_tags`, so they
apply uniformly to native and fixed agents. Empty/omitted flow lists preserve
current behavior (deploy to all selected agents).

`sample_k` / `sample_fraction` cap how many of the *filtered* agents are probed
each due step (mutually exclusive; unset probes them all). Selection is a
deterministic hash ranking per `(seed, step, agent)` — independent of roster
order and stable across replay/resume — so each due step probes a fresh but
reproducible subset. Use this to keep probe cost bounded as populations grow
(e.g. `sample_k: 100` at 10k agents).

---

## Action Prompt Configuration

### Prompt Additions

| Flag | Default | Effect |
|------|---------|--------|
| `sim.prompt_additions.action_count_guidance` | `true` | Add `[ActNum]` marker and action count guidance |

### How Action Prompts Are Constructed

1. **Runner startup**: `build_action_prompt_with_app_instance()` compiles the base prompt from the world config or backend action catalog (`action_mode: custom` vs `generic`)
2. **GM (`GameMaster.action_prompt`)**: returns a typed `ActionSpec` and includes tool schemas in `extra_args` when `tool_calling.mode != none`
3. **Agent**: calls the LLM in tool-calling or free-text mode from typed `ActionSpec.output_type` and `extra_args`

Tool-calling output style is automatically stripped from the base prompt when
`sim.tool_calling.mode` is not `none`.

---

## Engine Turn Policies

| Policy | Option | Behavior |
|--------|--------|----------|
| Single action | `single_action` | Each agent acts once per episode |
| Fixed count | `fixed_count` | Each agent gets N action turns per episode |
| Open-ended | `open_ended` | Agent acts until outputting a done token |

```yaml
sim:
  engine:
    step:
      built_in: base
      params:
        flow_order: [fixed_pre, default]
        agent_to_flow: {}
    turn_policy:
      built_in: fixed_count
      params:
        count: 3
        observe_before_act: first   # first | always | never
```

Policy `params` are strict constructor arguments. Unknown keys fail before the
simulation starts unless the target policy accepts `**kwargs`.
`observe_before_act` controls whether repeated-action policies refresh the GM
observation only before the first action, before every action, or never.
Omit it to preserve the default `first` behavior.

Flow scheduling (requires `engine.step.built_in: flow`):

```yaml
sim:
  engine:
    step:
      built_in: flow
      params:
        flow_order: [fixed_pre, default]
        agent_to_flow: {}
        # Optional per-flow turn policy overrides. Each value mirrors the
        # sim.engine.turn_policy slot shape ({built_in|class_path, params}).
        # Flows not listed here use the global sim.engine.turn_policy below.
        flow_turn_policies:
          fixed_pre:
            built_in: single_action
          default:
            built_in: open_ended
            params: {max_actions: 3}
    turn_policy:                 # global default; applies to unlisted flows
      built_in: single_action
```

`sim.engine.turn_policy` is the global default applied to every agent. With
`flow` or `multi_gm` scheduling you may additionally override the policy per
flow via `sim.engine.step.params.flow_turn_policies`, keyed by flow tag, each
value uses the same slot shape as `turn_policy`. Flows absent from the map fall
back to the global policy, so omitting the key reproduces current behavior
exactly. Per-flow overrides are ignored under `base`/`sequential` scheduling
(which do not group agents by flow). For a multi-GM flow chain the same per-flow
policy applies at every GM hop. Hop scheduling itself follows the chosen
`multi_gm*` step strategy — under the default `multi_gm` (concurrent), a flow's
next hop starts as soon as its own previous hop resolves, serializing only when
two flows touch the same GM; select `multi_gm_serial` or `multi_gm_staged` via
`sim.engine.step.built_in` for the other traversal modes (see
[Advanced: Multi-GM Orchestration](#advanced-multi-gm-orchestration)).

You may also override the turn policy per GM via
`sim.engine.step.params.gm_turn_policies`, a `{gm_name: turn_policy_slot}` map
where each value uses the same slot shape as `turn_policy` and
`flow_turn_policies` (`{built_in|class_path, params}`). This lets a GM/backend
set its own per-step action cadence — e.g. `single_action` in a "world" GM but
`open_ended` in a social GM, or a different cadence at each hop of a multi-GM
flow chain. The per-GM key disambiguates hops that share one flow, which
`flow_turn_policies` cannot (it is keyed by flow, so the same per-flow policy
applies at every GM hop). The policy for a batch is resolved by most-specific
wins: per-flow (`flow_turn_policies[flow]`) > per-GM
(`gm_turn_policies[gm_name]`) > global (`sim.engine.turn_policy`). Unset (an
empty map) means the global turn policy applies everywhere, unchanged. Unlike
`flow_turn_policies` (which only takes effect under `flow`/`multi_gm`
scheduling), `gm_turn_policies` applies under any step mode because it is
resolved per batch by GM name.

A sibling key, `sim.engine.step.params.gm_concurrency_caps`
(a `{gm_name: int}` map), caps how many of *that* GM's agent turns run
concurrently via a per-GM semaphore. It is orthogonal to `gm_turn_policies`:
the turn policy controls how *many* actions a single turn takes, while the cap
controls how many turns run *at once*. The global `sim.max_concurrent_actions`
remains the overall ceiling and the default for every GM, so the effective per-GM
limit is `min(cap, sim.max_concurrent_actions)`; an empty map means the global
cap governs everything (unchanged). This lets you throttle a rate-limited backend
(e.g. a live Mastodon server) below the global limit while other GMs keep running
concurrently. Like `gm_turn_policies`, it applies under any step mode.

Keep a cap well below `sim.max_concurrent_actions`: a capped GM's turns block on
its permit *while holding a worker thread*, so a heavily-capped GM with many
queued turns can occupy up to `worker_limit` threads waiting on its own permits
and starve other GMs of throughput (a slowdown, not a deadlock).

Sequential scheduling:

```yaml
sim:
  engine:
    step:
      built_in: sequential
```

`sequential` uses the same GM actor selection as `base`, but executes each
selected agent in its own batch so turns are strictly ordered.

---

## Checkpoint Restore

```bash
uv run silisocs \
  --config-path scenarios/my_world/conf \
  num_steps=200 \
  sim.checkpoint.every_n_steps=10 \
  sim.checkpoint.source_run=outputs/my_world/run1 \
  sim.checkpoint.restore.built_in=social_action_event_replay
```

Checkpoints are written to `.../outputs/.../checkpoints/step_<N>_checkpoint.json`.
Restore selects the latest checkpoint in the source run, initializes the runtime
object scaffolding, and then applies checkpointed agent, game-master, component,
and backend state. Built-in local backends restore their world state directly
from the checkpoint. `sim.checkpoint.restore` is still required for source runs
that need a restore strategy, such as older social runs that must rebuild backend
state from `action_events.jsonl`.
Checkpoint runtime metadata records artifact ownership for every Game Master
rather than relying on one representative GM for the whole run.

### Backend checkpoint capability

A backend supports either (or both) of two restore paths (see
`src/silisocs/environments/backends/base.py`):

- `provides_checkpoint_state` (class flag): the backend round-trips authoritative
  state via `get_state`/`set_state`, so restore is a direct snapshot apply through
  the default checkpoint loader. **True for every shipped backend.**
- Action-event replay — a restore *mechanism* owned by the `sim.checkpoint.restore`
  strategy, **not** a backend method. The built-in `social_action_event_replay`
  strategy keeps its per-backend event→action mappings in a registry keyed by
  `backend_type` (`runtime/checkpointing/replay_mappers.py`); a backend "supports
  replay" exactly when a mapper is registered for its `backend_type`. Any backend
  routed to replay whose `backend_type` has no mapper fails loudly. The registry
  ships `twitter_like` → `microblog_event_to_replay_action`; `reddit_like` has none
  (no valid microblog mapping) and relies on its snapshot instead.

Every shipped backend self-restores via `set_state`. The SQL backends snapshot
their database; **`mastodon`** can't snapshot its external live server, so its
checkpoint state *is* its action history: `get_state` embeds the logged actions
and `set_state` rebuilds the server by re-running them through a private mapper (as
their original users, in order). It therefore restores through the same default
`set_state` path, no special strategy required:

- **Server reset**: the server must be wiped first (`reset_server_on_setup=true`),
  otherwise replay duplicates the original run's content. Replay logs a warning
  when reset is not configured.
- **Toot-id remapping**: re-creating a post yields a *new* server toot id, so
  `like`/`boost`/`reply` events are remapped from their logged (pre-resume) id to
  the new one; an unmapped reference is skipped.
- **Caveat**: replay re-posts to the live server and reproduces a *similar*, not
  byte-identical, state (timestamps, ordering, federation differ). Set
  `perform_operations=true` for the actions to actually reach the server.

A **custom** non-snapshot backend (`provides_checkpoint_state=False`) can either
do the same (implement `get_state`/`set_state`) or call
`register_replay_mapper(backend_type, mapper)` (before the resume runs) to let the
built-in strategy replay its logged events. A backend that supports neither fails
loudly; supply a custom restore strategy:

```yaml
sim:
  checkpoint:
    restore:
      class_path: my_pkg.MyRestore   # subclass of CheckpointRestoreStrategy
      params: {}
```

`class_path` takes precedence over `built_in`. The class must subclass
`silisocs.runtime.checkpointing.restore.CheckpointRestoreStrategy`.

### Restore robustness

- **Identity reconciliation**: restoring a checkpoint object onto a runtime
  object of a different `class_path`/`compat` is rejected, and an object that
  saved non-empty state but only inherits the no-op `set_state()` raises rather
  than silently dropping that state. Objects present in the runtime but absent
  from the checkpoint are left freshly initialized and logged as a warning.
- **Recsys self-heal**: after restore the in-memory recsys engine is rebuilt
  empty; the recommendation-update component reconciles configured types against
  the backend's live `recsys_active_types()` and lazily re-initializes them on
  the first post-resume update, so algorithmic feeds resume automatically.
- **Flow scheduling**: the agent->flow tag assignment is re-materialized from the
  resume-time config (not the checkpoint); a divergence from the checkpointed
  agent->flow fingerprint is logged as a warning, since it can mis-route replay.
  The flow->GM routing topology (flow chains) is engine config, re-materialized
  from config and not part of the GM checkpoint at all.

### Multi-GM layout

When more than one Game Master is configured, each GM's backend database and
`action_events.jsonl` are isolated under a per-GM subdirectory
(`<output>/<gm_name>/...`) so same-type GMs cannot clobber one another on
checkpoint restore. Single-GM runs keep the flat layout. Two GMs that would
resolve to the same backend database path are rejected at build time.

**Per-GM restore**: the authoritative-vs-replay decision is made per game
master, not all-or-nothing: each GM that carries a backend snapshot restores from
it directly, and only the remaining (non-authoritative, e.g. Mastodon) GMs are
handed to the restore strategy. A mixed run (e.g. a `twitter_like` GM and a
`mastodon` GM) restores the snapshot GM from disk while replaying the Mastodon GM.

For multi-GM replay, restore discovers **every** per-GM `action_events.jsonl`
(the same flat-or-per-GM lookup eval uses), so multi-GM resumes locate their
logs. Each event is routed back to the GM that logged it (its `gm_name`) and
mapped to a backend action by that GM's own backend; events owned by an
already-restored (snapshot) GM are skipped.

**Per-GM restore override**: a GM may override the global `sim.checkpoint.restore`
with its own strategy (same schema), for a backend that needs custom loading
logic rather than the default replay/snapshot:

```yaml
env:
  gm_orchestration:
    gms:
      - gm_name: mastodon_gm
        backend: { type: mastodon }      # plus components: { ... }
        restore:
          class_path: my_pkg.MyMastodonRestore   # subclass of CheckpointRestoreStrategy
          params: {}
```

GMs without a `restore` block use the global default. The key is additive: omit
it and multi-GM restore behaves exactly as before. Authoritative (snapshot) GMs
ignore their `restore` override because `set_state` already restored them.

**Evaluation/analysis** read every per-GM `action_events.jsonl` (via
`silisocs.evaluations.action_events.resolve_action_event_files`), so the default
evaluators, activity summary, and dashboard cover all game masters, not just a
flat root log.

---

## Mid-Run Interventions

An optional top-level `interventions` schedule fires actions at step boundaries
(after probes measure the pre-intervention world, before the step runs), turning
a "controlled experiment" — swap the recommender at the midpoint, ban an agent,
inject a breaking-news post — into config instead of a manual
checkpoint / edit / resume cycle. Absent = no interventions (default).

```yaml
# top-level (world config root, @package _global_)
interventions:
  - at_step: 5
    actions:
      - kind: set_participation          # persistent
        slot: {built_in: activity_probability, params: {active_probability: 0.1}}
      - kind: ban_agents                 # persistent
        agents: [Alice, Bob]
      - kind: set_recsys                 # persistent (sugar for set_component_params)
        recsys_type: twitter_tfidf
        gm: null                         # null = the single/default GM; a name for multi-GM
      - kind: set_component_params       # persistent (the generic form)
        params: {update_every_n_steps: 3, max_posts: 5}
        gm: null
      - kind: set_turn_policy            # persistent
        slot: {built_in: fixed_count, params: {count: 2}}
        flow: burst_posters              # scope: at most one of flow / gm; neither = global
      - kind: set_router                 # persistent (re-point a flow's branch router)
        flow: choose_platform            # the flow whose branch node to re-point
        slot: {built_in: random, params: {weights: {gm_a: 3, gm_b: 1}}}
      - kind: swap_component             # persistent (stateless components only)
        role: observe                    # observe | next_acting | update
        slot: {built_in: episode_only}
        gm: null
  - at_step: 8
    actions:
      - kind: inject_post                # one-shot (sugar for inject_action)
        author: NewsBot                  # an existing agent / backend user
        text: "BREAKING: ..."
      - kind: inject_action              # one-shot (the generic form)
        agent: Moderator
        action: follow_user              # any backend catalog action name
        args: {target_username: NewsBot}
      - kind: broadcast_observation      # one-shot
        text: "You hear a rumor that ..."
        agents: []                       # empty = every agent
      - kind: unban_agents               # persistent
        agents: [Alice]
```

**Action kinds.** Each is either *persistent* (changes live engine/component
state) or *one-shot* (a single event):

| kind | class | effect |
|------|-------|--------|
| `set_participation` | persistent | rebuild the participation policy from `slot` (keeps any active ban) |
| `ban_agents` / `unban_agents` | persistent | exclude/re-include `agents` from every step's active roster (a *soft* ban — a banned agent still exists in the world, can be mentioned/followed, and still receives `requires_full_roster` updates) |
| `set_component_params` | persistent | retune declared component parameters on a GM (`params` mapping, applied to every component that declares the name; raises if a name lands nowhere) |
| `set_recsys` | persistent | sugar for `set_component_params` with `params: {recsys_type: ...}` — swaps the recommender on a GM's observe + update components (the new type initializes on the next recsys refresh) |
| `set_turn_policy` | persistent | rebuild the turn policy (how many actions a turn takes) from `slot`; scope is `flow` (needs a flow-aware step strategy), `gm`, or neither = the global default. Batch-time precedence is unchanged: per-flow > per-GM > global |
| `set_router` | persistent | re-point the router at `flow`'s branch node with `slot` (a stateless plain callable, rebuilt via `build_router`); needs a `multi_gm*` step strategy whose chain for `flow` contains a branch |
| `swap_component` | persistent | hot-swap a **stateless** GM component — `role` ∈ `observe` / `next_acting` / `update` — with `slot` (rebuilt with the GM's live wiring); refused when the outgoing OR incoming component has non-empty `get_state()` (retune stateful components with `set_component_params` instead) |
| `inject_action` | one-shot | invoke any backend catalog action as `agent` (a typed tool call — `action` name + `args` — resolved through the GM's resolve component, which validates against the catalog and injects the runtime actor) |
| `inject_post` | one-shot | sugar for `inject_action`: post `text` as `author` via the backend's canonical post action (the same per-backend mapping the seed-post initializer uses; `action_mapping` extends it for custom backends, `subreddit` targets reddit-likes) |
| `broadcast_observation` | one-shot | deliver `text` to targeted agents' memory (`agents: []` = all) |
| `custom` | declared by the class | `class_path` to an `InterventionHandler` subclass, built with `params` |

**Component tunables.** `set_component_params` is the extension seam for
mid-run retuning: a component opts a parameter in by listing it in its
class-level `runtime_tunable` frozenset (`BaseComponent.set_params` then routes
each name through a `set_<name>()` setter when the component defines one,
otherwise assigns the same-named attribute). The shipped social-media
components declare `recsys_type` (observe + update) and
`update_every_n_steps` / `lazy` / `max_posts` (update); a custom component —
recsys or otherwise — declares its own names and is immediately addressable
from config, no new intervention kind required. Only declare parameters that
are safe to reassign at a step boundary.

**Turn policies, routers, and stateless components.** `set_turn_policy` rebuilds
a turn policy from its `slot` and re-points the map the scheduler reads each step
(global, per-`gm`, or per-`flow`); turn policies carry no checkpoint state, so
the swap is replay-safe (per-`flow` scope needs a flow-aware step strategy —
`flow`/`multi_gm*`). `set_router` similarly re-points a flow's branch `router` —
also a stateless plain callable rebuilt from a slot — in the step strategy's flow
chain (needs a `multi_gm*` strategy with a branch node in that flow's chain).
`flow` targets on both are preflight-validated against the flows statically
declared in config (class `flow_tag`s, `flow_order`, `agent_to_flow`,
`flow_to_gms`), so a typo'd flow fails at config validation rather than mid-run;
when no flows are declared (e.g. a custom step strategy) the check defers to
fire time, mirroring the `gm` target rule.
`swap_component` replaces a whole GM component (`observe` /
`next_acting` / `update`) from its `slot`, but ONLY when both the outgoing and
the freshly built incoming component are stateless (empty `get_state()`) — a
stateful component (e.g. the recsys updater, or a `fixed_order` next-acting
cursor) is retuned with `set_component_params`, never replaced. The GM's
`rebuild_component` seam reuses the same per-role factory and live wiring as
first construction, and a checkpoint records each stateful component's class so a
resume after a swap skips (rather than blindly applies) state saved for a
different class. `resolve` / `action_prompt` (a pair coupled to the GM's
tool-calling mode) and `initialize` (meaningless mid-run) are out of scope.

**Resume semantics.** Persistent actions with `at_step < start_step` are
*replayed* on resume (their effect isn't in the checkpoint); one-shot events are
never replayed (their effect — a post, an observation — is already in restored
backend/agent state). Fired-ness is a pure function of `(schedule, step)`, so
resuming reproduces the exact intervention state with no checkpoint-schema
change. Interventions are recorded in `sim_metrics.json`
(`meta.interventions` + the `interventions_fired` counter).

**Scope.** Only the whitelisted kinds above are hot-swappable; model/agent
construction, GM topology, backend schema, the executor / step-strategy / loop
policies, and the `resolve`/`action_prompt` pair are not mid-run mutable.
Injections (`inject_action` / `inject_post`) emit typed tool calls, so — like
seed-post initialization, which shares the path — they require the target GM's
resolve component to execute tool calls (effective `tool_calling: single|multi`,
the default); on a `tool_calling: none` GM the text-parsing resolve records a
parse failure instead of executing the injection. Note that
any intervention that changes what agents see also changes downstream LLM output,
so a live-LLM run is only as reproducible as the model it calls (as with the
`agent_choice` router).

---

## Output Configuration

Output paths are controlled by Hydra in `experiment.yaml`:

```yaml
hydra:
  job:
    name: ${scenario_name}_${now:%Y-%m-%d_%H-%M-%S}
  run:
    dir: outputs/${scenario_name}/${jobname_format}
  output_subdir: configs/${jobname_format}
```

The simulation writes artifacts into the directory resolved by `hydra.run.dir` +
`hydra.job.name`. See [Usage Overview: Output](usage.md#output) for the complete
list of output files.

---

## Advanced: Multi-GM Orchestration

See [Multi-GM Architecture](multi_gm_architecture.md) for configuring multiple
game masters, flow-based scheduling, and per-flow component routing.

Use `sim.engine.step.built_in: multi_gm` with `env.gm_orchestration.gms` when
one run needs multiple Game Masters or backends. Every orchestrated GM must
declare its own `backend` and `components` blocks; those nested blocks use the
same strict key surface as `env.gm.backend` and `env.gm.components`.

`env.gm_orchestration.flow_bindings.flow_to_gms` maps flow names to GM chains.
Each chain must reference known GMs, contain at least one GM, avoid duplicate GM
names, and follow increasing GM `sequence` values when more than one GM is in
the chain. Flows without an explicit binding fall back to the earliest-sequence
GM. At runtime, every GM updates once at the start of each step before flow
routing and actor selection.

`sim.engine.step.params.agent_to_flow` is validated against final Agent names
and materialized before runtime. The Engine and Game Masters both read the same
final `agent_flow_tags`, so component routing cannot drift from Engine flow
scheduling.

`sim.engine.step.built_in` selects how flow chains traverse their GMs, via three
`multi_gm*` step strategies:

- `multi_gm` (DEFAULT, concurrent): Flows run as independent pipelines through
  their GM chains. Distinct flows advance concurrently and a flow's next hop
  starts as soon as its own previous hop completes — turns serialize ONLY when
  two flows touch the same GM at the same time (enforced by the engine's existing
  per-GM lock). Flows listed in `flow_order` run first as a strict serial prefix,
  preserving declared precedence such as seed-then-act (`fixed_pre` before
  `default`); every other flow runs as the concurrent group. A single agent's
  own chain hops always stay serial, since each hop observes the prior hop's
  resolution.
- `multi_gm_serial` (legacy row-major): each flow runs its full GM chain to
  completion before the next flow, one batch at a time, in a deterministic
  flow-by-flow order.
- `multi_gm_staged` (column-major with a global per-stage barrier): `flow_order`
  flows run first as a serial prefix (same as `multi_gm`); then every remaining
  flow advances ONE STAGE AT A TIME — all flows' stage-N hops run concurrently,
  and stage N+1 does NOT begin until ALL of stage N's turns finish. A flow's
  chain may contain an empty slot (a `null` entry in `flow_to_gms`) so it idles
  at that stage and resumes at its next non-null hop, letting flows with
  different chain shapes stay stage-aligned (see
  [Advanced: Multi-GM Orchestration](#advanced-multi-gm-orchestration)). The
  barrier can leave the worker pool idle at stage tails (a fast flow waits for
  slow flows); use `multi_gm` when you don't need stage alignment.

`multi_gm` is the default; its behavior is unchanged from prior releases (it was
formerly selected by the now-removed `sim.engine.step.params.chain_execution:
concurrent`, and `multi_gm_serial` was `chain_execution: sequential` — a config
that still sets `chain_execution` now raises a `ValueError` with a migration
hint). Independent of the mode: each GM's `update()` still runs once before any
acting in a step; checkpoint replay is still per-agent-flow-chain; and
`flow_turn_policies` and per-flow component routing still apply at every hop.

#### Branch nodes: routing one flow across alternative GMs

A chain entry may be a **branch node** — `{branch: {router, choices}}` — instead of
a GM name or `null`. At that stage each of the flow's agents is routed by the
configured `router` to exactly one of `choices` (real GM names); agents that pick
the same GM are batched together. The branch is a single chain stage, so the GMs
before and after it still run once on every agent (shared pre/post hops are not
split), and under `multi_gm_staged` the branch occupies one stage column so
alignment is preserved.

```yaml
env:
  gm_orchestration:
    flow_bindings:
      flow_to_gms:
        social_flow:
          - seed_gm
          - branch:
              router: { built_in: random, params: { weights: { twitter_gm: 0.7, reddit_gm: 0.3 } } }
              choices: [twitter_gm, reddit_gm]
          - wrapup_gm        # shared tail — both branches re-converge here
```

The router is a `{built_in | class_path, params}` slot, like a turn policy. Built-ins:

- `random` (`RandomChoiceRouter`): a weighted random pick, deterministic per
  `(seed, flow, step, agent)` — so a run reproduces and replays identically.
- `agent_choice` (`AgentChoiceRouter`): the **agent itself** picks its GM (see below).

**Custom routers — any callable.** A `class_path` router is the "a custom function
chooses" seam. A router is just a callable
`route(agents, gms, ctx) -> {agent name: chosen gm name}` — no base class, no
registration. It receives the flow's agent objects (call `agent.act(...)` freely),
`gms` (`{gm name: game master}`, one per choice, in config order — read `gm.backend`
freely), and `ctx` (`RouteInfo(flow, step, seed)` for a replay-stable decision), and
returns each agent's chosen GM. `class_path` may point at a plain function (config
`params` are bound as keyword arguments) or a class (built with `params`, instances
callable).

**When it runs.** The engine runs the router when the flow's chain reaches the branch
stage — after the flow's earlier hops have drained, so the router sees live backend
state and may involve the agents. This holds in all three `multi_gm*` traversals
(under `multi_gm_staged`, after the prior stage's barrier). The router call runs
unlocked; only the follow-up per-chosen-GM turn selection is serialized under that
GM's lock; and the engine validates the returned assignment (every agent covered,
every GM one of `choices`). An LLM-driven router is only as reproducible as the model
it calls.

`agent_choice` params: `prompt` (a template; placeholders `{choices}`, `{flow}`, `{step}`,
`{agent}`) and `on_invalid` (`random` — the default, a replay-stable fallback;
`first`; or `raise`). It matches the agent's answer with the shared `match_choice`
helper (exact → case-insensitive → contained-once), which custom routers can import.
Example:

```yaml
        social_flow:
          - branch:
              router:
                built_in: agent_choice
                params:
                  prompt: "You can act on {choices} this round. Reply with exactly one."
                  on_invalid: random
              choices: [twitter_gm, reddit_gm]
```

Constraints (validated at config/engine build): at most one branch per chain; at
least two distinct, known choices; the branch's choice sequences must sit strictly
between its chain neighbours; a branch requires a `multi_gm*` step mode; and a branch
may not sit in a `flow_order` (serial-prefix) flow.

---

## Related

- [Usage Overview](usage.md): End-to-end workflow and output format
- [Building Agents](building_agents.md): Persona pipeline details
- [Environment Backends](backends.md): Generic apps, social platforms, and visualizers
- [Evaluation Probes](probes.md): Probe type reference
- [Multi-GM Architecture](multi_gm_architecture.md): Advanced GM orchestration
