# Dashboard

The project ships two dashboard experiences:

- Streamlit launcher for world editing and simulation execution
- Dash analytics app for post-run interaction/probe analysis

Together they cover end-to-end usage, but they are separate applications.

## Streamlit Launcher

```sh
uv run streamlit run src/silisocs/dashboard/launch_app.py
```

The launcher opens in your browser with six tabs.

---

### What It Is Good For

- Creating and editing world YAML files
- Validating class/source settings before launch
- Previewing Hydra CLI overrides
- Running simulations with live stdout capture

Simple-first UX:

- Advanced controls are hidden by default.
- Turn on **Enable advanced configuration** in Environment to edit flow tags,
  shared-flow GM presets, and orchestration YAML.

### Tabs

### 1. Simulation

Core simulation parameters:

- **LLM Provider/Model**: Provider (`openai`, `openai_compatible`, `scripted`, or `disabled`) plus model name
  (for example, `gpt-4o-mini` for OpenAI or an OpenAI-compatible model served
  behind `sim.llm.api_base`)
- **Number of agents / steps**: Scale parameters
- **Random seed**: Reproducibility
- **Agent initialization**: choose configured memory initialization or no-op startup
- **Action mode**: `custom` or `generic`
- **Tool-calling mode**: `none`, `single`, or `multi`

Simulation tab scope:

- Run-level controls (agents, steps, seed, model, concurrency)
- Advanced runtime toggles (memory backend, action mode, observation limits)

### 2. Scenario

Setting and event configuration:

- **Scenario name**: Used for output directory/config naming
- **Setting name / background**: Social world framing
- **Event name / context**: Current event shaping agent behavior
- **Memory processing mode**: `raw` or `formative`
- **Shared memories**: Injected into all agents at initialization

### 3. Agent Classes

Configure the persona pipeline:

- **Add/remove classes**: Dynamic list of agent classes
- **Agent class**: Dropdown auto-populated by scanning the package for agent classes
- **Data source**: Choose from inline records, local JSON, config path, or Hugging Face datasets
- **File path verification**: Local JSON paths are validated as you type
- **Field map**: YAML editor for mapping data fields to agent parameters
- **Per-class LLM model**: Optional model override per agent class
- **Count**: Number of agents in each class
- **Sim role name**: Role key used by GM component params such as activity rates
- **Flow tag** (advanced only): Class-level action flow used by multi-GM orchestration
- **Fixed-action agent mode** (optional):
- Enable fixed actions for a class
- Select referenced action set id
- Choose policy (`round_robin`, `weighted_random`, `scripted_sequence`)
- Choose exhaustion behavior (`loop`, `stop`, `fallback_to_llm`)
- **Fixed Action Set Registry**:
- Optional file path for action sets
- Inline YAML editor for reusable action sets (`set_id -> actions[]`)

### 4. Environment

Runtime environment controls:

- **Backend**: Twitter-like, Reddit-like, Mastodon, resource market, or virtual space
- **Enabled backend actions**: Optional multi-select whitelist. Empty means all backend actions are available.
- **Enable advanced configuration** (toggle): reveals orchestration controls.

Environment levers in expanders:

- **GM Components**
- Next-acting choice: `activity_probability`, `activity_markov`, `all_agents`, `fixed_order`
- Observe choice: `timeline_every_turn`, `app_observation`, `episode_only`
- Resolve choice: `parsed_action`, `generic_action`, `tool_calling`
- Optional custom class path override field for each GM slot

- **Initialization**
- Agent memory choice: `raw_memory`, `formative_memory`, `none`
- Game Master initialize choice: `social_media`, `app_initialize`, `none`
- Seed-post choice for social backends: `agent`, `csv`, `json`, `fallback`, `none`

- **Engine Policies**
- Turn policy choice: `single_action`, `fixed_count`, `open_ended`
- Probe schedule choice: `step_schedule`, `fixed_interval`, `disabled`
- Turn-policy params: `count`, `max_actions`, `finished_action_signal`, `observe_before_act`
- Probe-schedule params: `start_step`, `every_n_steps`
- Optional custom class path override for each policy slot

- **Advanced GM Orchestration** (advanced mode only)
- GM preset: `base` or `shared_flow`
- YAML editor for `gm_orchestration` (multi-GM flow bindings)

Social backend controls (shown only for social backends):

- **Graph type**: Barabasi-Albert, random, LFR benchmark
- **Parameters**: Edges per node, followership probability
- **Activity rates**: Per-role transition probabilities
- **Timeline strategy**: follower chronological, pure recommendation,
  hybrid recommendation/follower, or curated global where supported

Action filtering behavior:

- The enabled-action whitelist constrains LLM action selection prompts.
- Tool-calling schemas are generated only for enabled actions.
- Fixed-action agents are also constrained by this whitelist.

### 5. Probes

Evaluation probe configuration:

- **Deployment schedule**: Start step, frequency
- **Query definitions**: Add probe questions with types

### 6. Launch

- **Validation warnings**: Missing modules, data sources, or misconfigured settings
- **Auto-save**: Config is saved before launch
- **CLI preview**: Shows the exact command that will be run
- **Run Simulation button**: Launches the simulation as a subprocess

---

### Creating A New Scenario

1. In the sidebar, go to **Create New Scenario**
2. Enter a world name
3. The dashboard creates grouped config files under `worlds/<name>/conf/` (`world/default.yaml`, `agents/default.yaml`, `sim.yaml`, `env.yaml`, `eval.yaml`)
4. Configure the world across all tabs
5. Click **Run Simulation** in Launch tab — the dashboard auto-saves and runs with `--config-path`

Scenarios created via the dashboard are immediately available for CLI use:

```sh
uv run silisocs --config-path worlds/my_world/conf
```

---

### Loading Existing Scenarios

The sidebar uses a two-step loader:

1. **Load world**: world names discovered from `worlds/*/conf/world/default.yaml`
2. **Start from**: choose one of:
	- **World definition** (the base world YAML)
	- A prior run snapshot from `outputs/<world>/<run>/configs/*/config.yaml`

This allows you to start from the latest saved run config while keeping world-level selection clean.

Checkpoint replay note:

- `Start from` loads configuration snapshots, not runtime state checkpoints.
- To restore runtime state, launch with `sim.checkpoint.source_run=<prior_output_dir>`.

Notes:

- If no external worlds are found, the launcher falls back to package `default`.
- The top banner shows **Loaded from** so you can see whether you are editing a base world or a run snapshot.

---

## Dash Analytics App

The analytics app visualizes run outputs after simulation completes.

```sh
uv run python -m silisocs.evaluations.analysis.dashboard.main \
	--output-dir outputs/<world>/<run_dir>
```

### Required Inputs

- `action_events.jsonl`

Optional inputs:

- `probe_events.jsonl`
- `prompts_and_responses.jsonl`

The app renders generic action trends and probe trends. When social follow or
post/reply/like/repost events are present, it also renders a follow/interactions
graph and post-level action details.

### What It Answers Well

- Which actions increased/decreased over time
- Which users were most active in each episode
- How follow relationships evolved in social runs
- How probe responses changed by step
- What prompts and outputs were logged when prompt logs are available

### Current Limits

- It does not replace deep custom analysis scripts
- Social graph panels require social action labels; other backend runs still
  show action/probe/prompt summaries
- Launcher and analytics are not yet a single integrated UI

---

## Recommended End-To-End User Journey

1. Use Streamlit launcher to create or edit world and run simulation.
2. Inspect generated output folder (`action_events.jsonl`, optional `probe_events.jsonl`, prompt logs, DB).
3. Open Dash analytics app on that output folder for exploratory analysis.
4. Use backend visualizer (Twitter-like or Reddit-like) for detailed platform state inspection.
