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
| `num_agents` | `100` | Number of agents to create |
| `num_steps` | `50` | Simulation steps to run |
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
| `sim.llm.provider` | `openai` | Model provider: `openai`, `openai_compatible`, `scripted`, or `disabled` |
| `sim.llm.name` | `gpt-4o-mini` | LLM model name (passed to the model factory) |
| `sim.llm.api_base` | `null` | Required base URL when `provider: openai_compatible` |
| `sim.llm.api_key` | `null` | API key (or set via environment variable) |
| `sim.llm.temperature` | `0.5` | Sampling temperature |
| `sim.llm.disabled` | `false` | Use a no-op model (for testing without API calls) |
| `sim.llm.extra_kwargs` | `{}` | Provider request kwargs such as OpenAI-compatible `extra_body` settings |

### Engine and Runtime

| Parameter | Default | Description |
|-----------|---------|-------------|
| `sim.max_concurrent_actions` | `1000` | Max parallel LLM calls per step |
| `sim.action_mode` | `custom` | Prompt style: `custom` (world prompt) or `generic` (backend-generated) |
| `sim.tool_calling.mode` | `single` | Tool dispatch mode: `none`, `single`, or `multi` |
| `sim.prompt_additions.action_count_guidance` | `true` | Add `[ActNum]` marker and action count guidance to prompt |
| `sim.checkpoint.every_n_steps` | `null` | Save checkpoints every N steps when set |
| `sim.checkpoint.explicit_steps` | `[]` | Additional explicit checkpoint steps |
| `sim.checkpoint.source_run` | `null` | Previous output directory to restore from |
| `sim.checkpoint.restore.built_in` | `social_action_event_replay` | Checkpoint restore strategy when `source_run` is set |
| `sim.engine.step.built_in` | `base` | Engine step policy: `base`, `sequential`, `flow`, or `multi_gm` |
| `sim.roleplaying_instructions` | *(template)* | System prompt injected into every agent. Use `{name}` placeholder. |

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

**Layer 1 — Hydra SearchPath Plugin**: a registered `SearchPathPlugin` prepends
the scenario conf dir to Hydra's search path before composition. This gives
`world/default.yaml` and `agents/default.yaml` from the scenario conf dir
higher priority than the package defaults, so they replace the package
`world/default.yaml` and `agents/default.yaml` entirely.

**Layer 2 — Manual merge** (runs inside `main()` after Hydra composes): handles
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
4. Click "Save Scenario" — creates files under `scenarios/{name}/conf/`
5. Click "Run Simulation"

**Option 2: Manual**

```bash
mkdir -p scenarios/my_world/conf/world scenarios/my_world/conf/agents
```

**`scenarios/my_world/conf/world/default.yaml`** — run parameters and narrative:
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

**`scenarios/my_world/conf/agents/default.yaml`** — personas:
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

**`scenarios/my_world/conf/env.yaml`** — optional backend/GM overrides:
```yaml
gm:
  components:
    initialize:
      params:
        graph:
          base_followership_probability: 0.3
          network_type: barabasi_albert
          barabasi_albert_m: 10
    next_acting:
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
      model: null                       # Per-class LLM override
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
      room_tasks:
        - task_id: welcome_board
          room: atrium
          description: Prepare a shared welcome board.
          required_effort: 2
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

| Backend | Common actions |
|---------|----------------|
| `twitter_like` | `create_tweet`, `reply_to_tweet`, `like_tweet`, `unlike_tweet`, `repost_tweet`, `quote_repost_tweet`, `follow_user`, `unfollow_user`, `mute_user`, `unmute_user`, `search_posts`, `get_trending_posts`, `report_post`, `update_profile`, `view_profile`, `do_nothing`, `FINISHED` |
| `reddit_like` | `create_reddit_post`, `create_comment`, `upvote`, `downvote`, `like_post`, `unlike_post`, `dislike_post`, `undo_dislike_post`, `get_home_feed`, `get_post_comments`, `search_subreddits`, `get_trending_posts`, `report_post`, `mute_user`, `unmute_user`, `update_profile`, `view_profile`, `do_nothing`, `FINISHED` |
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

### GM Components

```yaml
env:
  gm:
    components:
      next_acting:
        built_in: activity_probability  # activity_markov | activity_probability | all_agents | fixed_order
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

### Social Setup and Activity Components

Graph fields are owned by the GM initialize component. Activity rates are owned
by the GM next-acting component.

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
      next_acting:
        params:
          activity_transition_rates:
            <role_name>:
              inactive_to_active: 0.3
              active_to_inactive: 0.3
```

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
```

The turn policy is global for the engine. Flow mode only controls which
agent groups act together and in what order.

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
- [Environment Backends](backends.md) — Generic apps, social platforms, and visualizers
- [Evaluation Probes](probes.md) — Probe type reference
- [Multi-GM Architecture](multi_gm_architecture.md) — Advanced GM orchestration
