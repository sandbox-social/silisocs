# Silisocs Studio

Studio is the supported visual product for scenario design, launch, live
monitoring, platform inspection, analysis, study comparison, and report export:

```sh
pip install "silisocs[studio]"
silisocs-studio --output-root outputs --port 8765
```

The API is documented at `http://127.0.0.1:8765/api/docs`. Localhost mutation
is zero-configuration. Binding beyond localhost requires
`STUDIO_AUTH_TOKEN`; clients send it as a Bearer token.

Studio is backend-neutral. Generic surfaces operate on run/study artifacts,
backend action catalogs, job records, and declarative form schemas. A backend
may optionally declare a `VisualizerSpec` and register event semantics for
specialized platform/feed/network views. Custom backends and pages are loaded
through the existing `class_path`, registry, and entry-point mechanisms.

## Extending Studio without core edits

A custom backend is immediately launchable from the scenario composer when its
fully qualified `BackendApp` class path is selected. Studio obtains the action
picker from `BackendApp.declared_action_catalog()`, which introspects
`@app_action` declarations without constructing the backend. It does not contain
a backend-name-to-action switch.

The following metadata is optional and adds richer visual capabilities:

```python
class MyWorld(BackendApp):
    visualizer = VisualizerSpec(
        env_var="MY_WORLD_DB",
        module="my_world.viewer",
        default_port=8100,
        port_env="MY_WORLD_PORT",
    )
    event_semantics = {
        "roles": {"content.root": ("publish",)},
        "fields": {
            "content.id": ("object.id",),
            "content.text": ("object.body",),
        },
    }
```

The runtime copies this portable metadata into `run_manifest.json`. Studio can
therefore discover the viewer and render compatible specialized panels later
without importing the backend implementation. Backends that model markets,
physical spaces, organizations, or another domain can omit social semantic
roles and ship their own artifact-backed panel/view instead.

Composer extensions use the same declarative language. Register a dynamic
choice source with `register_choice_provider(name, callable, deferred=True)` when
resolution may import a backend or contact another capability, and reference it
from `Field(..., choices_from=name, choices_depend_on=(...))`. Deferred choices
load after the form shell and rerun only when a declared dependency changes. For a genuinely custom control, use
`widget="class_path:mypkg.MyWidget"`; the widget implements
`render(field, value, files)`. The resulting YAML remains the source of truth,
including keys unknown to the form schema.

Installed packages can add a complete navigation page through a
`silisocs.studio_pages` entry point returning `StudioPage(name, label, href,
router)`. Analysis panels use the separate `silisocs.panels` entry-point group.
Settings lists all discovered pages, panels, views, form schemas, and dynamic
choice providers.

## Legacy transition

Two legacy commands remain for one deprecation release:

- `silisocs-dashboard` — Streamlit launcher for scenario editing and simulation
  execution (needs the `dashboard` extra)
- `silisocs-analysis-dashboard` — Dash analytics app for post-run
  interaction/probe analysis (needs the `analysis` extra)

Both print a Studio migration pointer. The Streamlit Run button submits to
Studio's `/api/launch` control plane when Studio is available and falls back to
its old direct subprocess path only during this transition.

## Streamlit Launcher

```sh
uv run silisocs-dashboard
# equivalent to: uv run streamlit run src/silisocs/dashboard/launch_app.py
```

The launcher opens in your browser with seven tabs.

---

### What It Is Good For

- Creating and editing scenario YAML files
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
- **Sim role name**: Role key used by sim-level participation params, such as the per-role activity rates in `sim.engine.participation.params.activity_transition_rates`
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
- Next-acting choice: `all_agents`, `fixed_order` (activity models are sim-level: `sim.engine.participation`)
- Observe choice: `timeline_every_turn`, `app_observation`, `episode_only`
- Resolve component: **derived** from the Tool-calling mode + Action mode (shown read-only), so it is always valid — `tool_calling` when tool-calling is `single`/`multi`, else `parsed_action` (custom prompts) or `generic_action` (generic prompts). Set a custom resolve class path to override.
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
- The excluded-action deny-list removes actions from the exposed action surface.
- Tool-calling schemas are generated only for actions that pass both filters.
- Fixed-action agents are also constrained by these filters.
- Unknown action names, or actions matched by both filters, fail before launch.

### 5. Probes

Evaluation probe configuration:

- **Deployment schedule**: Start step, frequency, include/exclude roles
- **Fire-at anchor**: `pre_step`, `post_step`, or `run_end` (the terminal
  measurement, taken once after the run) — maps to `eval.probes.deployment.at`
- **Sampling cap**: probe at most K agents, or a fraction, per due step
  (`sample_k` / `sample_fraction`, applied after include/exclude filters)
- **Query definitions**: Add probe questions with types, each with an optional
  per-probe anchor/sample override (`eval.probes.probes.<name>.deployment`) that
  overlays the global block

### 6. Launch

- **Validation warnings**: Missing modules, data sources, or misconfigured settings
- **Auto-save**: Config is saved before launch
- **CLI preview**: Shows the exact command that will be run
- **Raw generated config (YAML)**: Expander showing the exact world config and
  sim/env/eval overrides the UI composes — the same YAML you could write by
  hand under `scenarios/<name>/conf/`
- **Run Simulation button**: Launches the simulation as a subprocess
- **Open live platform view during run** (social backends): as soon as the run
  creates its backend database, the read-only platform visualizer is started
  against it (needs the `viz` extra) — its feed auto-refreshes as agents act
- **Live actions panel**: while the run streams output, a compact summary of
  committed actions (totals, per-label counts, most recent events) updates from
  the run's `action_events.jsonl`

### 7. Results

- **Run History**: Recent runs (newest first) with status, scenario, health
  issues, token totals, and estimated cost — read from each run's
  `run_manifest.json`; runs that predate the manifest show what
  `sim_metrics.json` can recover
- **Companion viewers**: one-click buttons per selected run — **Open platform
  view** (the backend's read-only web UI on the run's database; multi-GM runs
  get a database picker) and **Open analysis dashboard** (the Dash analytics
  app on the run's output directory), plus **Stop viewer servers**. Missing
  extras surface as the exact `pip install "silisocs[...]"` hint
- **Run inspector**: Pick a run (discovered or pasted path) to see overview
  metrics, degraded-run health counters, action activity charts, and probe
  responses. Multi-GM runs load merged per-GM event logs via the Run Artifact
  Module (`silisocs.evaluations.run_artifact.load_run`)

---

### Creating A New Scenario

1. In the sidebar, go to **Create New Scenario**
2. Enter a scenario name
3. The dashboard creates grouped config files under `scenarios/<name>/conf/` (`world/default.yaml`, `agents/default.yaml`, `sim.yaml`, `env.yaml`, `eval.yaml`)
4. Configure the scenario across all tabs
5. Click **Run Simulation** in Launch tab: the dashboard auto-saves and runs with `--config-path`

Scenarios created via the dashboard are immediately available for CLI use:

```sh
uv run silisocs --config-path scenarios/my_world/conf
```

---

### Loading Existing Scenarios

The sidebar uses a two-step loader:

1. **Load scenario**: scenario names discovered from `scenarios/*/conf/world/default.yaml`
2. **Start from**: choose one of:
	- **Scenario definition** (the base world YAML)
	- A prior run snapshot from `outputs/<scenario>/<run>/configs/*/config.yaml`

This allows you to start from the latest saved run config while keeping scenario selection clean.

Checkpoint replay note:

- `Start from` loads configuration snapshots, not runtime state checkpoints.
- To restore runtime state, launch with `sim.checkpoint.source_run=<prior_output_dir>`.

Notes:

- If no external scenarios are found, the launcher falls back to package `default`.
- The top banner shows **Loaded from** so you can see whether you are editing a base scenario or a run snapshot.

---

## Dash Analytics App

The analytics app visualizes run outputs after simulation completes.

```sh
uv run silisocs-analysis-dashboard \
	--output_dir outputs/<scenario>/<run_dir>
```

### Required Inputs

- `action_events.jsonl`

Optional inputs:

- `probe_events.jsonl`
- `prompts_and_responses.jsonl`

When launched with `--output_dir`, discovery goes through the Run Artifact
Module (`silisocs.evaluations.run_artifact.load_run`): manifest-first, with
per-GM event logs from multi-GM runs merged automatically.

The app renders action trends and probe trends across both microblog
(`post`/`like`/`repost`/`reply`) and forum (`post`/`comment`/`upvote`/`downvote`)
vocabularies. Every actor becomes a node in the interaction graph even without
follow edges, so post-only and Reddit-like runs render the network and
post-level action details rather than staying on the upload screen; follow edges
are added on top when present.

### What It Answers Well

- Which actions increased/decreased over time
- Which users were most active in each episode
- How follow relationships evolved in social runs
- How probe responses changed by step
- What prompts and outputs were logged when prompt logs are available

### Current Limits

- It does not replace deep custom analysis scripts
- Non-social backends (e.g. resource market) have no interaction graph, but their
  actors still appear as nodes and their action/probe/prompt summaries render
- Launcher and analytics are separate apps, but the launcher's Results tab
  starts the analytics app (and the platform visualizer) with one click

---

## Shared Visual Tokens

`silisocs.visual_tokens` is the single source of truth for the brand accent and
the per-action-type plot colors, shared by the Dash analytics app and any future
UI. The Streamlit theme lives in `.streamlit/config.toml` (Slate & Teal, matching
the docs site); its `primaryColor` mirrors `visual_tokens.ACCENT` — keep the two
in sync when changing the brand.

---

## Recommended End-To-End User Journey

1. Use Streamlit launcher to create or edit a scenario and run simulation —
   with **Open live platform view during run** enabled, the platform UI opens
   automatically and updates while agents act.
2. Inspect generated output folder (`action_events.jsonl`, optional `probe_events.jsonl`, prompt logs, DB).
3. From the Results tab, open the Dash analytics app on that run with one click.
4. From the Results tab, open the backend visualizer (Twitter-like or
   Reddit-like) for detailed platform state inspection.
