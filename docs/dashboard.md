# Dashboard

The project ships two dashboard experiences:

- Streamlit launcher for scenario editing and simulation execution
- Dash analytics app for post-run interaction/probe analysis

Together they cover end-to-end usage, but they are separate applications.

## Streamlit Launcher

```sh
uv run streamlit run src/mastodon_sim/dashboard/launch_app.py
```

The launcher opens in your browser with six tabs.

---

### What It Is Good For

- Creating and editing scenario YAML files
- Validating class/source settings before launch
- Previewing Hydra CLI overrides
- Running simulations with live stdout capture

### Tabs

### 1. Simulation

Core simulation parameters:

- **LLM Model**: Model name (e.g., `gpt-4o`, `qwen3.5-4b`)
- **Number of agents / steps**: Scale parameters
- **Random seed**: Reproducibility
- **Memory backend**: `list` (fast) or `associative` (embedding-based)
- **Action mode**: `custom`, `generic`, or `tool_calling`

Simulation tab scope:

- Run-level controls (agents, steps, seed, model, concurrency)
- Advanced runtime toggles (memory backend, action mode, timeline/observation limits)

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
- **Entity module**: Dropdown auto-populated by scanning the package for Prefab classes
- **Data source**: Choose from HuggingFace dataset, local JSON, config path, or inline
- **File path verification**: Local JSON paths are validated as you type
- **Field map**: YAML editor for mapping data fields to agent parameters
- **Per-class LLM model**: Optional model override per agent class
- **Count**: Number of agents in each class
- **Sim role name**: Role key used by social network/activity config

### 4. Environment

Runtime environment controls:

- **Platform backend**: Twitter-like, Reddit-like, or Mastodon

Environment levers in expanders:

- **GM Components**
- Next-acting choice: `activity_markov`, `all_entities`, `fixed_order`
- Observe choice: `timeline_every_turn`, `chunk_start_only`
- Resolve choice: `parsed_action`, `generic_action`, `tool_calling`
- Initializer choice: `backend_default`
- Optional custom class path override field for each GM slot

- **Engine Policies**
- Action loop choice: `single_action`, `fixed_count`, `open_ended`
- Probe schedule choice: `step_schedule`, `fixed_interval`, `disabled`
- Action-loop params: `count`, `max_actions`, `done_token`
- Probe-schedule params: `start_step`, `every_n_steps`
- Optional custom class path override for each policy slot

Social network controls (in the same Environment tab):

- **Graph type**: Barabasi-Albert, random, LFR benchmark
- **Parameters**: Edges per node, followership probability
- **Activity rates**: Per-role transition probabilities

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
2. Enter a scenario name
3. The dashboard creates `scenarios/<name>/conf/scenario/<name>.yaml`
4. Configure the scenario across all tabs
5. Click **Run Simulation** in Launch tab — the dashboard auto-saves and runs with `--config-path`

Scenarios created via the dashboard are immediately available for CLI use:

```sh
uv run mastodon-sim --config-path scenarios/my_scenario/conf
```

---

### Loading Existing Scenarios

The sidebar uses a two-step loader:

1. **Load scenario**: scenario names discovered from `scenarios/*/conf/scenario/*.yaml`
2. **Start from**: choose one of:
	- **Scenario definition** (the base scenario YAML)
	- A prior run snapshot from `scenarios/<scenario>/outputs/<run>/configs/*/config.yaml`

This allows you to start from the latest saved run config while keeping scenario-level selection clean.

Notes:

- If no external scenarios are found, the launcher falls back to package `default`.
- The top banner shows **Loaded from** so you can see whether you are editing a base scenario or a run snapshot.

---

## Dash Analytics App

The analytics app visualizes run outputs after simulation completes.

```sh
uv run python -m mastodon_sim.evaluations.analysis.dashboard.main \
	--output-dir scenarios/<scenario>/outputs/<run_dir>
```

### Required Inputs

- `action_events.jsonl`
- `probe_events.jsonl`

The app loads both files and renders interaction trends, probe trends, and a
dynamic follow/interactions graph.

### What It Answers Well

- Which actions increased/decreased over time
- Which users were most active in each episode
- How follow relationships evolved
- How probe responses changed by step

### Current Limits

- It does not replace deep custom analysis scripts
- It assumes expected action/probe event labels
- Launcher and analytics are not yet a single integrated UI

---

## Recommended End-To-End User Journey

1. Use Streamlit launcher to create or edit scenario and run simulation.
2. Inspect generated output folder (`action_events.jsonl`, `probe_events.jsonl`, `logs.html`, DB).
3. Open Dash analytics app on that output folder for exploratory analysis.
4. Use backend visualizer (Twitter-like or Reddit-like) for detailed platform state inspection.
