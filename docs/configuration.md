# Configuration Reference

Complete reference for all YAML configuration options.

## Config Groups

Configuration is split across named groups, each with a base preset in
`src/mastodon_sim/conf/`:

| Group | Base file | Controls |
|---|---|---|
| *(root)* | `experiment.yaml` | Run parameters, Hydra output paths |
| `agent_situation` | `agent_situation/base.yaml` | Persona pipeline, setting, event, shared memories |
| `llm` | `llm/base.yaml` | Model name, API endpoint, temperature |
| `simulator` | `simulator/base.yaml` | Engine, tool-calling, memory backend, checkpoint |
| `env` | `env/twitter_like.yaml` | Platform backend, GM components, social network |
| `evals` | `evals/base.yaml` | Probes, HTML log writing |

---

## Top-Level Config (`experiment.yaml`)

```yaml
defaults:
  - agent_situation: base
  - llm: base
  - simulator: base
  - env: twitter_like
  - evals: base
  - _self_

hydra:
  job:
    name: ${scenario_name}_${now:%Y-%m-%d_%H-%M-%S}
  run:
    dir: scenarios/${scenario_name}/outputs/${jobname_format}
  output_subdir: configs/${jobname_format}

experiment_name: independent

# Run parameters — overridable per-scenario via run.yaml or CLI
num_agents: 100
num_steps: 50
run_name: run1
seed: 1
output_rootname: ""
scenario_name: default
agent_situation_name: default
jobname_format: "N${num_agents}_T${num_steps}_${experiment_name}_${run_name}"
```

Override from the CLI:

```sh
uv run python -m mastodon_sim.runtime.runner env=reddit_like num_agents=500
```

---

## Run Parameters (root-level)

| Parameter | Default | Description |
|-----------|---------|-------------|
| `num_agents` | `100` | Number of agents to create |
| `num_steps` | `50` | Simulation steps to run |
| `run_name` | `run1` | Run identifier (used in output path) |
| `seed` | `1` | Random seed |
| `scenario_name` | `default` | Scenario identifier (used in output path) |
| `agent_situation_name` | `default` | Selects `agent_situation/{name}.yaml` from scenario conf dir |
| `jobname_format` | *(template)* | Output directory name template |
| `experiment_name` | `independent` | Experiment label used in `jobname_format` |

---

## LLM Parameters (`llm/base.yaml`)

| Parameter | Default | Description |
|-----------|---------|-------------|
| `llm.name` | `gpt-4o-mini` | LLM model name (passed to Concordia model factory) |
| `llm.api_base` | `null` | Custom API base URL (for OpenAI-compatible endpoints) |
| `llm.api_key` | `null` | API key (or set via environment variable) |
| `llm.temperature` | `0.5` | Sampling temperature |
| `llm.disabled` | `false` | Use a no-op model (for testing without API calls) |

---

## Simulator Parameters (`simulator/base.yaml`)

| Parameter | Default | Description |
|-----------|---------|-------------|
| `simulator.max_concurrent_actions` | `1000` | Max parallel LLM calls per step |
| `simulator.sentence_encoder` | `sentence-transformers/all-MiniLM-L6-v2` | Embedding model for associative memory |
| `simulator.memory_backend` | `list` | Memory type: `list` (fast) or `associative` (embedding-based) |
| `simulator.action_mode` | `custom` | Prompt style: `custom` (scenario prompt) or `generic` (backend-generated) |
| `simulator.tool_calling.mode` | `single` | Tool dispatch mode: `none`, `single`, or `multi` |
| `simulator.prompt_additions.add_action_count_guidance` | `false` | Add `[ActNum]` marker and action count guidance to prompt |
| `simulator.checkpoint.every_n_steps` | `null` | Save checkpoints every N steps when set |
| `simulator.checkpoint.explicit_steps` | `[]` | Additional explicit checkpoint steps |
| `simulator.checkpoint.resume_file` | `null` | Path to checkpoint JSON to resume a prior run |
| `simulator.checkpoint.resume_step` | `null` | Step override when resuming |
| `simulator.engine.preset` | `base` | Engine preset: `base` or `flow` |
| `simulator.roleplaying_instructions` | *(template)* | System prompt injected into every agent. Use `{name}` placeholder. |

---

## Running and Creating Scenarios

Scenarios live in `scenarios/{name}/conf/` and selectively override package
defaults. The run command:

```bash
uv run python -m mastodon_sim.runtime.runner --config-path scenarios/misinformation/conf
```

### Directory Structure

```
scenarios/
└── my_scenario/
    └── conf/
        ├── run.yaml                     # Run parameters merged to config root
        ├── env.yaml                     # Platform/GM overrides (optional)
        ├── evals.yaml                   # Probe config overrides (optional)
        ├── llm.yaml                     # LLM overrides (optional)
        ├── simulator.yaml               # Engine overrides (optional)
        └── agent_situation/
            ├── default.yaml             # Default agent situation (setting, event, personas)
            └── thin.yaml                # Alternate lightweight variant (optional)
```

### How Config Overrides Work

Two mechanisms layer on top of the package defaults:

**Layer 1 — Hydra searchpath** (`--config-path`): the scenario conf dir is
prepended to Hydra's searchpath, so any file matching a group name
(`simulator.yaml`, `evals.yaml`, `llm.yaml`) silently replaces the
corresponding package default at compose time.

**Layer 2 — Manual merge** (runs inside `main()` after Hydra composes): handles
files that don't fit Hydra's group model:

- `run.yaml` → merged flat into the config root (`scenario_name`, `num_steps`, `seed`, …)
- `env.yaml`, `evals.yaml`, `llm.yaml`, `simulator.yaml` → merged into their named groups
- `agent_situation/{agent_situation_name}.yaml` → merged into the `agent_situation` group (variant selected by the `agent_situation_name` root key)

**Priority order** (highest → lowest):

1. CLI overrides (`num_steps=1 llm.disabled=true`)
2. Scenario `run.yaml` / group yamls (layer 2 merge)
3. Scenario files in Hydra searchpath (layer 1)
4. Package defaults in `src/mastodon_sim/conf/`

CLI overrides are re-applied at the end of the merge so they always win over
scenario defaults.

### Running a Scenario

```bash
# Run with scenario defaults
uv run python -m mastodon_sim.runtime.runner --config-path scenarios/election/conf

# Override specific parameters
uv run python -m mastodon_sim.runtime.runner --config-path scenarios/election/conf \
    num_agents=500 num_steps=100

# Use alternate agent situation variant
uv run python -m mastodon_sim.runtime.runner --config-path scenarios/ai_conference/conf \
    agent_situation_name=thin

# Dry-run with no LLM calls (for testing)
uv run python -m mastodon_sim.runtime.runner --config-path scenarios/misinformation/conf \
    num_steps=1 llm.disabled=true

# View merged config before running
uv run python -m mastodon_sim.runtime.runner --config-path scenarios/election/conf --cfg job
```

### Creating a New Scenario

**Option 1: Via Dashboard**

1. Start the dashboard: `streamlit run src/mastodon_sim/dashboard/launch_app.py`
2. Modify all settings (agents, network, probes, etc.)
3. Enter a new scenario name in the "Scenario Name" field
4. Click "Save Scenario" — creates files under `scenarios/{name}/conf/`
5. Click "Run Simulation"

**Option 2: Manual**

```bash
mkdir -p scenarios/my_scenario/conf/agent_situation
```

**`scenarios/my_scenario/conf/run.yaml`** — run parameters:
```yaml
scenario_name: my_scenario
agent_situation_name: default
jobname_format: "N${num_agents}_T${num_steps}_${run_name}"
num_agents: 50
num_steps: 20
seed: 42
run_name: my_scenario
```

**`scenarios/my_scenario/conf/agent_situation/default.yaml`** — personas and narrative:
```yaml
setting:
  name: My Setting
  background:
    - Background detail 1

event:
  name: My Event
  context: |
    Event description used in agent memories.

data: {}

persona_pipeline:
  processing_mode: raw
  defaults:
    params:
      scenario_context: ${agent_situation.event.context}
    shared_memories:
      - ${agent_situation.event.context}
  classes:
    user:
      count: ${num_agents}
      prefab_module: mastodon_sim.agents.entity
      sim_role_name: user
      data:
        source: hf_dataset
        dataset: nvidia/Nemotron-Personas-USA
        split: train
      field_map:
        context: persona

shared_memories:
  - ${agent_situation.event.context}

initial_observations:
  - "{name} opens their social media feed."
```

**`scenarios/my_scenario/conf/env.yaml`** — optional platform overrides:
```yaml
social_network:
  base_followership_probability: 0.3
  network_type: barabasi_albert
  barabasi_albert_m: 10
  activity_transition_rates:
    user:
      inactive_to_active: 0.5
      active_to_inactive: 0.2
```

### Output Structure

Simulation outputs go to: `scenarios/{scenario_name}/outputs/{jobname_format}/`

```
scenarios/my_scenario/outputs/
└── N50_T20_my_scenario/
    ├── my_scenario_2026-01-01_12-00-00/
    │   ├── effective_config.yaml      # Full resolved config
    │   ├── sim_metrics.json           # Timing and run stats
    │   ├── action_events.jsonl        # Per-step action log
    │   ├── probe_events.jsonl         # Probe outputs
    │   └── checkpoints/               # Step checkpoints (if enabled)
    └── configs/N50_T20_my_scenario/
        ├── config.yaml                # Hydra-composed config snapshot
        └── effective_config.yaml      # Runtime-resolved config
```

---

## Agent Situation Config (`agent_situation/base.yaml`)

Defines the narrative context and persona pipeline. Scenario-specific content
lives in `scenarios/*/conf/agent_situation/default.yaml`.

### Setting and Event

```yaml
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

`${agent_situation.event.context}` is available as an interpolation target
throughout the agent situation config.

### Persona Pipeline

```yaml
persona_pipeline:
  processing_mode: raw          # raw | formative

  defaults:                     # Applied to all classes
    params:
      scenario_context: ${agent_situation.event.context}
      seed_post: ""
      bio: ""
      style: ""
      goal: null
    shared_memories:
      - "A shared memory for all agents."
    field_map:
      context: persona

  classes:
    <class_name>:
      count: ${num_agents}              # Number of agents in this class
      prefab_module: mastodon_sim.agents.entity
      sim_role_name: user               # Role name for activity rates
      flow_tag: default                 # Optional class-level flow tag
      model: null                       # Per-class LLM override
      data:
        source: hf_dataset             # hf_dataset | inline | config_path
        dataset: nvidia/Nemotron-Personas-USA
        split: train
      field_map:
        context: persona
      params:
        goal: "Have a productive discussion."
      shared_memories:
        - "Class-specific memory."
```

### Data Sources

| Source | Required Keys | Description |
|--------|--------------|-------------|
| `hf_dataset` | `dataset`, `split` | HuggingFace Datasets (cached after first download) |
| `inline` | `records` | Records defined directly in YAML |
| `config_path` | `path` | Dot-path reference into another config section (e.g. `candidates`) |

### Alternate Agent Situation Variants

Create additional files alongside `default.yaml` for lightweight or experimental
variants:

```
agent_situation/
├── default.yaml    # Full persona set
└── thin.yaml       # Minimal personas for fast testing
```

Select at runtime:
```bash
uv run python -m mastodon_sim.runtime.runner --config-path scenarios/ai_conference/conf \
    agent_situation_name=thin
```

---

## Env Config (`env/twitter_like.yaml`)

### Platform Backends

**Twitter-like (default)**
```yaml
platform_type: twitter_like
use_server: false
```

**Reddit-like**
```yaml
platform_type: reddit_like
use_server: false
```

**Mastodon (remote)**
```yaml
platform_type: mastodon
use_server: true   # Requires a running Mastodon server
```

Requires environment variables for server URL and API credentials.
See [Installation](installation.md) for `.env` setup.

### Enabled Actions

By default agents can use all backend actions. Restrict to a subset:

```yaml
enabled_actions:
  - create_tweet
  - reply_to_tweet
  - like_tweet
  - FINISHED
```

Action names must be the **exact decorated backend function names**:

| Platform | Actions |
|----------|---------|
| TwitterLike | `create_tweet`, `reply_to_tweet`, `like_tweet`, `repost_tweet`, `follow_user`, `unfollow_user` |
| RedditLike | `create_reddit_post`, `create_comment`, `upvote_post`, `downvote_post`, `subscribe`, `unsubscribe` |
| Mastodon | `post_toot`, `reply_to_toot`, `like_toot`, `boost_toot`, `follow_user`, `unfollow_user` |

### Seed Posts

Initialize agent feeds with background posts before the simulation starts:

| Type | Description |
|------|-------------|
| `llm` (default) | LLM-generated context-aware posts |
| `csv` | Pre-written posts from a CSV file (`agent_name,post_text`) |
| `json` | Pre-written posts from a JSON file (`{"agent_name": "post_text"}`) |
| `none` | Disable seed posts (organic growth only) |
| `fallback` | CSV when available, LLM for missing agents |

```yaml
seed_posts:
  type: llm
  params:
    file_path: null   # Path to CSV/JSON file when type is csv/json/fallback
```

### GM Components

```yaml
gm:
  preset: base
  components:
    next_acting:
      built_in: activity_probability  # activity_markov | activity_probability | all_entities | fixed_order
    observe:
      built_in: timeline_every_turn   # timeline_every_turn | episode_only
      params:
        episode_observation_flow: fixed_pre
    resolve:
      built_in: tool_calling          # parsed_action | generic_action | tool_calling
```

### Social Network

```yaml
social_network:
  network_type: barabasi_albert       # barabasi_albert | random | predefined
  barabasi_albert_m: 10
  base_followership_probability: 0.3
  fully_connected_targets:            # Roles that all agents follow
    - news_account
  activity_transition_rates:
    <role_name>:
      inactive_to_active: 0.3
      active_to_inactive: 0.3
```

### Timeline Mode

```yaml
timeline_mode: follower_chronological  # follower_chronological | pure_recsys | hybrid_recsys_follower | curated_global
```

| Strategy | Platforms | Description |
|----------|-----------|-------------|
| `follower_chronological` | All | Recent posts from followed users, no algorithm |
| `pure_recsys` | Twitter, Reddit | Algorithm-selected posts only |
| `hybrid_recsys_follower` | Twitter, Reddit | Blend of recommendations + followed posts |
| `curated_global` | Twitter only | Trending posts + personalized recommendations |

---

## Evals Config (`evals/base.yaml`)

```yaml
write_html_log: true    # Generate Concordia HTML logs (slow; set false for batch runs)

probes: {}              # See Probes section below
```

### Probes

```yaml
probes:
  query_lib_module: null   # Optional custom probe type module

  deployment:
    enabled: true
    start_step: 1
    every_n_steps: 1
    include_entities: []   # Empty = all agents
    exclude_entities: []

  queries:
    favorability:
      probe_name: favorability
      query_type: NumericRatingProbe
      query_data:
        name: Favorability
        question: "Return a single rating from {lo} to {hi}."
        context: "{agentname} rates favorability toward the current event."
        lo: 1
        hi: 10
```

---

## Action Prompt Configuration

### Prompt Additions

| Flag | Default | Effect |
|------|---------|--------|
| `simulator.prompt_additions.add_action_count_guidance` | `false` | Add `[ActNum]` marker and action count guidance |

### How Action Prompts Are Constructed

1. **Runner startup**: `build_action_prompt_with_app_instance()` compiles the base prompt from the scenario config or backend action catalog (`action_mode: custom` vs `generic`)
2. **GM (SMAct)**: wraps the compiled prompt; if `tool_calling.mode != none`, appends tool schemas
3. **Entity**: calls LLM in tool-calling or free-text mode based on markers

Tool-calling output style is automatically stripped from the base prompt when
`simulator.tool_calling.mode` is not `none`.

---

## Engine Action Loop Policies

| Policy | Option | Behavior |
|--------|--------|----------|
| Single action | `single_action` | Each agent acts once per episode |
| Fixed count | `fixed_count` | Each agent gets N action turns per episode |
| Open-ended | `open_ended` | Agent acts until outputting a done token |

```yaml
simulator:
  engine:
    preset: base
    action_loop:
      built_in: fixed_count
      params:
        count: 3
```

Per-flow override (requires `engine.preset: flow`):

```yaml
simulator:
  engine:
    flow_routing:
      flow_order: [fixed_pre, default]
    flow_policies:
      fixed_pre:
        built_in: fixed_count
        params:
          count: 1
      default:
        built_in: single_action
```

---

## Checkpoint Resume

```bash
uv run python -m mastodon_sim.runtime.runner \
  --config-path scenarios/my_scenario/conf \
  num_steps=200 \
  simulator.checkpoint.every_n_steps=10 \
  simulator.checkpoint.resume_file=scenarios/my_scenario/outputs/.../checkpoints/step_100_checkpoint.json
```

Checkpoints are written to `.../outputs/.../checkpoints/step_<N>_checkpoint.json`.
Set `simulator.checkpoint.resume_step` to force a different starting step.

---

## Output Configuration

Output paths are controlled by Hydra in `experiment.yaml`:

```yaml
hydra:
  job:
    name: ${scenario_name}_${now:%Y-%m-%d_%H-%M-%S}
  run:
    dir: scenarios/${scenario_name}/outputs/${jobname_format}
  output_subdir: configs/${jobname_format}
```

The simulation writes artifacts into the directory resolved by `hydra.run.dir` +
`hydra.job.name`. See [Usage Overview — Output](usage.md#output) for the complete
list of output files.

---

## Advanced: Multi-GM Orchestration

See [Multi-GM Architecture](multi_gm_architecture.md) for configuring multiple
game masters, flow-based scheduling, and per-flow component routing.

---

## Related

- [Usage Overview](usage.md) — End-to-end workflow and output format
- [Building Agents](building_agents.md) — Persona pipeline details
- [Social Media Backends](backends.md) — Platform config and visualizers
- [Evaluation Probes](probes.md) — Probe type reference
- [Multi-GM Architecture](multi_gm_architecture.md) — Advanced GM orchestration
