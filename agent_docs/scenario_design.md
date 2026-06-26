# Scenario Design Guide

This guide is for LLM agents helping design and create new scenarios using
configuration (YAML) — without modifying code.

## 1) Scenario Design Workflow

A complete scenario consists of:

1. **Persona Pipeline** — Define agent populations (classes, counts, data sources, personas)
2. **GM component behavior** — Define followership graph setup, activity rates, observation, and update settings
3. **Probes** — Define evaluation questions to ask agents during/after simulation
4. **Setting & Event** — Provide narrative context for the simulation
5. **Optional sim/backend overrides** — Customize sim parameters or backend type for this scenario

### Creating a New Scenario

**Directory Structure:**
```
scenarios/{scenario_name}/conf/
├── world/
│   └── default.yaml                (Required: @package _global_)
├── agents/
│   └── default.yaml                (Required: @package agents)
├── sim.yaml                        (Optional: partial sim overrides)
└── env.yaml                        (Optional: partial env overrides)
```

### File 1: world/default.yaml

**Required header:**
```yaml
# @package _global_
```

**Required fields:**
```yaml
scenario_name: election
jobname_format: "N${num_agents}_T${num_steps}_${experiment_name}_${run_name}"
num_agents: 500
num_steps: 200
seed: 1
run_name: election

setting:
  name: "Storhampton"
  background:
    - "Small town with ~2500 residents"
    - "Economic tensions between communities"

event:
  name: "Mayoral Election"
  context: "Heated campaign on social media between two candidates"

data: {}
```

**Complete example:**

See `scenarios/election/conf/world/default.yaml` for a production world with all sections.

### File 2: Persona Pipeline (`agents/default.yaml`)

Defines how to construct agents from data sources:

```yaml
# @package agents
persona_pipeline:
  defaults:                         # Applied to all classes
    params:
      world_context: ${event.context}
      bio: ""
      style: ""
      goal: null
    shared_memories:
      - "You are a resident of Storhampton"
      - ${event.context}

  classes:
    voter:
      count: 497
      class_path: silisocs.agents.native.NativeAgent
      sim_role_name: voter           # For activity rates
      model: null                     # null = use sim.llm.name, or override per-class
      data:
        source: hf_dataset
        dataset: nvidia/Nemotron-Personas-USA
        split: train
      field_map:
        context: persona
        # Name is derived for nvidia/Nemotron-Personas-USA by the default builder.
        # Other HF datasets should map a real name field or set
        # derive_name_from_context: true intentionally.
      params:
        goal: "Have a good day and vote in the election"
      shared_memories:
        - "Voters care about the town's economy"
      flow_tag: default              # Optional: for multi-flow routing

    candidate:
      count: 2
      class_path: silisocs.agents.native.NativeAgent
      sim_role_name: candidate
      data:
        source: config_path           # Reference another config section
        path: candidates              # Dot-path in this YAML
        expand_values: true
      field_map:
        name: name
        context: persona
        style: style
        goal: goal
      flow_tag: active

    news_account:
      count: 1
      class_path: silisocs.agents.native.NativeAgent
      sim_role_name: news_account
      data:
        source: config_path
        path: news_account
        expand_values: true
      field_map:
        name: name
        bio: bio
        seed_post: seed_post
```

**Data Sources:**

| Source | Config | Example |
|--------|--------|---------|
| HuggingFace Dataset | `source: hf_dataset`<br>`dataset: nvidia/Nemotron-Personas-USA` | Pulls personas from public dataset |
| Local JSON | `source: local_json`<br>`path: agents.json` | File with array of objects |
| Inline YAML | `source: inline`<br>`records: [{name: Alice, bio: "..."}]` | Defined directly in the scenario |
| Config Reference | `source: config_path`<br>`path: candidates` | Dot-path into this YAML file |

### File 3: GM Component Behavior (`env.yaml`)

Controls followership graph setup and activity selection through the GM's
initialize and next-acting component params:

```yaml
gm:
  components:
    initialize:
      params:
        graph:
          network_type: barabasi_albert
          barabasi_albert_m: 30
          base_followership_probability: 0.4
          fully_connected_targets:
            - candidate
            - news_account
    next_acting:
      params:
        activity_transition_rates:
          voter:
            inactive_to_active: 0.1
            active_to_inactive: 0.2
          candidate:
            inactive_to_active: 0.8
            active_to_inactive: 0.1
```

### File 4: Shared Memories

Context injected into all agents at initialization:

```yaml
shared_memories:
  - "You are a long-time resident of Storhampton"
  - ${event.context}
  - ${setting.background}
```

### File 5: Initial Observations

Templates for starting observations (one per agent):

```yaml
initial_observations:
  - "{name} is at home and has just woken up"
  - "{name} remembers they want to check their social media feed"
  - "{name} notices the upcoming election"
```

### File 6: Probes (Evaluation)

Define questions to ask agents during/after simulation:

```yaml
probes:
  deployment:
    enabled: true
    start_step: 1
    every_n_steps: 1
    include_agents: []
    exclude_agents: []

  probes:
    vote_pref:
      probe_name: vote_pref
      probe_type: ChoiceProbe
      probe_data:
        name: VotePref
        question: "In one word, name the candidate you want to vote for"
        choices:
          - Bill Fredrickson
          - Bradley Carter

    favorability_bill:
      probe_name: favorability_bill
      probe_type: NumericRatingProbe
      probe_data:
        name: FavorabilityBill
        question: "Rate Bill Fredrickson on a scale of {lo} to {hi}"
        lo: 1
        hi: 10

    will_vote:
      probe_name: will_vote
      probe_type: BinaryProbe
      probe_data:
        name: WillVote
        question: "Will you cast a vote?"
```

### File 7: Scenario-Specific Agent Data (Optional)

Define special agents referenced in classes:

```yaml
news_account:
  name: "Storhampton Gazette"
  username: storhampton_gazette
  bio: "Local news for Storhampton"
  context: "Small-town newspaper"
  seed_post: "Good morning, Storhampton!"

candidates:
  conservative:
    name: "Bill Fredrickson"
    partisan_type: conservative
    persona: "45-year-old local businessman"
    style: "Direct, focuses on economics"
    goal: "Win the election"
  progressive:
    name: "Bradley Carter"
    partisan_type: progressive
    persona: "35-year-old high school teacher"
    style: "Inviting, focuses on environment"
    goal: "Win the election"
```

---

## 2) Optional: Scenario-Specific sim.yaml Overrides

Create `scenarios/{name}/conf/sim.yaml` to customize sim parameters (LLM, engine, tool-calling).
Run parameters (`num_agents`, `num_steps`, `seed`) belong in `world/default.yaml`, not here.

```yaml
# No @package header needed — merged into sim group automatically

# Only list fields that differ from base defaults
action_mode: generic
tool_calling:
  mode: multi

llm:
  name: gpt-4o                     # Override model for this scenario

engine:
  turn_policy:
    built_in: open_ended
    params:
      max_actions: 10
```

**Important:** Missing fields automatically fall back to `src/silisocs/conf/sim/base.yaml` defaults. This keeps scenario files minimal.

### File 8: Optional env.yaml

Create if you want a different backend or GM component settings:

```yaml
# No @package header needed — merged into env group automatically

gm:
  backend:
    type: reddit_like       # twitter_like | reddit_like | mastodon
    class_path: null
    params: {}
    enabled_actions: null   # null = all backend actions; list names to restrict
    excluded_actions: null  # optional deny-list after enabled_actions
```

Default action surfaces:

- `twitter_like`: all Twitter-like `@app_action` tools are available by default,
  including posting, replying, liking/unliking, reposting, following,
  muting/unmuting, search/trends, reporting, profile actions, `do_nothing`, and
  `FINISHED`.
- `reddit_like`: all Reddit-like `@app_action` tools are available by default,
  including posting, commenting, voting, feed/comment inspection, muting,
  search/trends, reporting, profile actions, `do_nothing`, and `FINISHED`.

Use `env.gm.backend.enabled_actions` to narrow a scenario to the action set you
want, and `env.gm.backend.excluded_actions` to remove specific actions while
otherwise keeping the wider surface.
Entries may be canonical method names or selectable aliases.

```yaml
gm:
  backend:
    enabled_actions:
      - create_tweet
      - reply_to_tweet
      - like_tweet
      - repost_tweet
      - FINISHED
    excluded_actions:
      - report_post
```

---

## 3) Common Scenario Patterns

### Pattern 1: Active vs Inactive Agents

**Goal:** Some agents post frequently, others are lurkers.

```yaml
persona_pipeline:
  classes:
    active_users:
      count: 100
      flow_tag: active
      sim_role_name: active
      ...

    lurkers:
      count: 400
      flow_tag: inactive
      sim_role_name: inactive
      ...

gm:
  class_path: silisocs.environments.gm.game_master.MultiFlowGameMaster
  components:
    next_acting:
      params:
        activity_transition_rates:
          active:
            inactive_to_active: 0.9
            active_to_inactive: 0.1
          inactive:
            inactive_to_active: 0.05
            active_to_inactive: 0.5
```

### Pattern 2: Different Recommendation Algorithms per Agent Class

Configure in the scenario persona pipeline:

```yaml
persona_pipeline:
  classes:
    tech_savvy:
      count: 100
      data: ...
      params:
        recsys_type: twitter      # TF-IDF: content-based personalization

    general_users:
      count: 400
      data: ...
      params:
        recsys_type: reddit       # Engagement-based hot score
```

The backend automatically initializes all unique types and updates them.

### Pattern 3: Fixed-Action Agents (News Bot, Announcer)

For agents that perform deterministic actions:

```yaml
persona_pipeline:
  classes:
    news_bot:
      count: 1
      class_path: silisocs.agents.fixed.FixedAgent
      data:
        source: config_path
        path: news_bot
      fixed_action:
        enabled: true
        action_set_ref: news_bot_actions
        selection_policy: round_robin
        on_exhaustion: loop

fixed_action_sets:
  inline:
    news_bot_actions:
      actions:
        - action: create_tweet
          args:
            status: "Breaking: {topic}"
          weight: 1.0
        - action: create_tweet
          args:
            status: "Update on {topic}"
          weight: 1.0
```

---

## 4) Running Your Scenario

**From CLI:**
```bash
uv run silisocs --config-path scenarios/my_world/conf
```

**With overrides:**
```bash
uv run silisocs --config-path scenarios/my_world/conf \
    num_agents=1000 num_steps=100
```

**From Dashboard:**
1. Open `uv run streamlit run src/silisocs/dashboard/launch_app.py`
2. Select scenario from the dropdown
3. Modify settings as needed
4. Click "Save Scenario" to persist
5. Click "Run Simulation"

---

## 5) Understanding Multi-Flow (Advanced)

If you want different agent populations to receive different observations or components, use the flow-routed GM class:

```yaml
gm:
  class_path: silisocs.environments.gm.game_master.MultiFlowGameMaster
  components:
    observe:
      # Create two separate Observe components
      timeline:
        built_in: timeline_every_turn
        params: {}
      episode:
        built_in: episode_only
        params: {}
```

Then route agents by `flow_tag`:

```yaml
persona_pipeline:
  classes:
    active:
      flow_tag: active              # Will receive timeline observations
    lurkers:
      flow_tag: lurkers             # Will receive episode observations
```

The game master automatically routes each agent to the correct component instance based on their flow.

---

## 6) Config Composition & Fallback

**How Hydra merges configs (in order):**

1. **Package defaults** (lowest priority)
   - `src/silisocs/conf/world/default.yaml` (`@package _global_`)
   - `src/silisocs/conf/agents/default.yaml` (`@package agents`)
   - `src/silisocs/conf/sim/base.yaml` (`@package sim`)
   - `src/silisocs/conf/env/twitter_like.yaml` (`@package env`)
   - `src/silisocs/conf/eval/base.yaml` (`@package eval`)

2. **Your scenario overrides** (higher priority via SearchPath plugin)
   - `scenarios/my_world/conf/world/default.yaml` (replaces package world group)
   - `scenarios/my_world/conf/agents/default.yaml` (replaces package agents group)
   - `scenarios/my_world/conf/sim.yaml` (merged into sim group, if present)
   - `scenarios/my_world/conf/env.yaml` (merged into env group, if present)

3. **CLI overrides** (highest priority)
   - `num_agents=500`
   - `sim.llm.name=gpt-4o`

**Default behavior:**
- If `sim.yaml` omits `llm.name`, uses `base.yaml` value (`gpt-4o-mini`)
- If a scenario omits a probe, that probe is not deployed

This means your scenario files only need to specify what's **different** from defaults.

---

## 7) Reference: Default Values

**From `src/silisocs/conf/world/default.yaml`** (at config root):
- `num_agents`: 100
- `num_steps`: 50
- `seed`: 1
- `run_name`: run1

**From `src/silisocs/conf/sim/base.yaml`:**
- `sim.llm.name`: gpt-4o-mini
- `sim.llm.provider`: openai
- `sim.action_mode`: custom
- `sim.tool_calling.mode`: single
- `sim.engine.step.built_in`: base
- `sim.engine.turn_policy.built_in`: single_action

**From `src/silisocs/conf/env/twitter_like.yaml`:**
- `env.gm.backend.type`: twitter_like
- Supports: `create_tweet`, `like_tweet`, `repost_tweet`, `reply_to_tweet`, `follow_user`

---

## 8) Troubleshooting

**Problem:** "Scenario file not found"
- **Check**: `scenarios/{name}/conf/world/default.yaml` exists
- **Check**: File has `# @package _global_` as first line

**Problem:** "Unknown field in persona_pipeline"
- **Check**: `docs/configuration.md` Scenario Config section for valid fields
- **Check**: All agent classes have `class_path` and `data.source`

**Problem:** "Agent count exceeds num_agents"
- **Check**: Sum of all `classes[*].count` <= `num_agents`
- **Fix**: Adjust counts or increase `num_agents` in sim.yaml

**Problem:** "Probe type unknown"
- **Check**: Probe types are: ChoiceProbe, NumericRatingProbe, BinaryProbe, FreeTextProbe
- **Check**: `docs/probes.md` for detailed field requirements per type

---

## 9) Next Steps

1. **Study existing example**: review `scenarios/election/conf/world/default.yaml`
2. **Understand defaults**: check `src/silisocs/conf/sim/base.yaml`
3. **Create your scenario**: mkdir and scaffold with the structure above
4. **Test it**: run via CLI with `--config-path`
5. **For complex worlds**: consult [architecture.md](architecture.md) (multi-flow deep dive) and [AGENTS.md](../AGENTS.md) (custom agents)

---

## 10) Structured Study Orchestration (`silisocs.studies.run_study`)

For multi-condition research studies (hypothesis trees, seed sweeps, condition-specific evaluators), use:

```bash
uv run silisocs-study --study experiments/studies/study_template_v1 plan
uv run silisocs-study --study experiments/studies/study_template_v1 generate-bash
uv run silisocs-study --study experiments/studies/study_template_v1 run
```

Code placement:
- Canonical runner implementation is in the package at `silisocs.studies.run_study`.
- Console command: `uv run silisocs-study ...` (equivalent: `uv run python -m silisocs.studies.run_study ...`).

### Why use study orchestration?

- Expand one declarative file into many concrete runs (scenario x condition x seed).
- Keep hypothesis metadata and interpretation fields close to execution metadata.
- Optionally use exact command templates for full manual command control.
- Reuse existing runs in follow-up hypotheses while preserving reproducibility lock records.
- Run multiple evaluators per run (global and condition-local evaluators).

### Study schema (v1) at a glance

```yaml
schema_version: 1

study:
  name: recsys_behavior_sweep
  study_id: recsys_behavior_sweep
  study_version: v1
  question: "Research question"
  scenarios: [election_recsys_engagement]
  parent_studies: []
  derived_from_runs: []
  study_summary_path: experiments/studies/recsys_behavior_sweep/SUMMARY.md
  summary_log_path: experiments/studies/recsys_behavior_sweep/generated/summary_log.jsonl
  run_defaults:
    config_path: scenarios/election_recsys_engagement/conf
    run_name_template: "{study_id}_{hypothesis_id}_{condition_id}_{scenario}_seed{seed}"
    output_root_override: "experiments/studies/{study_id}/runs/{hypothesis_id}/{condition_id}/{scenario}/seed_{seed}/run"
    seed_start: 11
    seed_repeats: 3
    overrides:
      num_agents: 50
      num_steps: 10

evaluations:
  - id: action_metrics
    preset: builtin.action_metrics_detailed
  - id: probe_metrics
    preset: builtin.probe_metrics_detailed

hypotheses:
  h1:
    statement: "Hypothesis statement"
    independent_variable: timeline_case
    prediction: "Expected outcome"
    status: testing
    conditions:
      case1:
        overrides:
          env.gm.components.observe.params.timeline_mode: follower_chronological
      case2:
        execution:
          mode: run
          command:
            - uv
            - run
            - python
            - -m
            - silisocs.runtime.runner
            - --config-path
            - scenarios/election_recsys_engagement/conf
            - seed={seed}
```

### Key fields

| Field | Type | Required | Notes |
|---|---|---|---|
| `schema_version` | int | yes | Must be `1`. |
| `study.name` | string | yes | Used for generated artifact paths. |
| `study.study_id` | string | recommended | Stable identifier used in run/eval directory layout. |
| `study.study_version` | string | optional | Human-managed version tag. |
| `study.question` | string | yes | Human-readable research question. |
| `study.scenarios` | list[string] | recommended | Base scenario loop for all conditions. |
| `study.run_defaults` | mapping | optional | Shared config path, seed settings, and overrides. |
| `evaluations` | list[mapping] | optional | Global evaluators executed for each run. |
| `hypotheses` | mapping | yes | Hypothesis tree with `conditions`. |

#### `study.run_defaults` seed controls

Use one of these patterns:
- Single seed: `seed: 11`
- Explicit list: `seeds: [11, 12, 13]`
- Range expansion: `seed_start: 11` + `seed_repeats: 3` => `11,12,13`

#### Condition execution mode

| Field | Meaning |
|---|---|
| `execution.mode: run` | Execute a fresh simulation run. |
| `execution.mode: reuse_existing` | Reference existing run artifacts instead of re-running simulation. |
| `execution.re_evaluate: true` | In reuse mode, run evaluators again over referenced outputs. |

Condition-level path controls:
- `run_name_template`: Optional placeholder template for top-level `run_name`.
- `output_root_override`: Optional placeholder template for `sim.output_rootname`.
- `sub_experiment`: Optional label to group/run subsets of conditions.
- `config_path`: Optional per-condition override for Hydra `--config-path`.

### Exact command templates (optional)

If `execution.command` is set, the study runner uses that command exactly for each expanded run.

Supported placeholders:
- `{run_id}`
- `{study_name}`
- `{study_id}`
- `{hypothesis_id}`
- `{condition_id}`
- `{scenario}`
- `{seed}`

These placeholders are also available in `run_name_template` and `output_root_override`.

### Evaluators in studies

Evaluator objects support either explicit commands or built-in presets.

Common evaluator fields:

| Field | Type | Notes |
|---|---|---|
| `id` | string | Evaluator identifier used in output paths. |
| `preset` | string | Built-in command preset. |
| `command` | string or list[string] | Explicit evaluator command. |
| `input_mode` | string | `run_dir` or `explicit_paths`. |
| `output_subpath` | string | Output filename/subpath under evaluator directory. |
| `enabled` | bool | Defaults to true. |

Condition-level evaluator behavior:
- `evaluation_mode: append` (default): add condition evaluators to global evaluators.
- `evaluation_mode: replace`: use only condition evaluators.

### Built-in evaluator presets

Legacy light summaries:
- `builtin.activity_summary`
- `builtin.probe_summary`

Detailed default evaluators:
- `builtin.action_metrics_detailed` (per-agent, per-episode, transitions)
- `builtin.probe_metrics_detailed` (all probe events with per-type/per-label metrics)
- `builtin.probe_binary_detailed`
- `builtin.probe_numeric_detailed`
- `builtin.probe_choice_detailed`
- `builtin.probe_freetext_detailed`

Detailed probe evaluators also emit probe-type-specific PNG plots in
`*_plots/` directories colocated with evaluator JSON outputs.

Extensible postprocessor hooks:
- Built-in detailed probe evaluators accept repeated `--postprocessor module:function`.
- Add these via evaluator `static_args` in study YAML.
- Hook signature: `(records_by_type, out_dir, context) -> dict | list | None`.

Example:

```yaml
evaluations:
  - id: probe_metrics
    preset: builtin.probe_metrics_detailed
    static_args:
      - --postprocessor
      - silisocs.evaluations.postprocessors:episode_probe_volume
```

These detailed evaluators read `action_events.jsonl` and use `effective_config.yaml` (when available) to map probe labels to configured probe types.

### Generated artifacts

Each study writes orchestration artifacts under its study directory:

```text
experiments/studies/{study_id}/generated/
  plan.json
  run_study.sh
  repro_lock.jsonl
  repro_lock.json
  study_index.json
  study_enriched.yaml
  logs/{run_id}.log
  eval/{hypothesis}/{condition}/{scenario}/seed_{seed}/{eval_id}/*.json
```

Simulation outputs are organized by study/hypothesis/condition/scenario with seed at the lowest level:

```text
experiments/studies/{study_id}/runs/{hypothesis_id}/{condition_id}/{scenario}/seed_{seed}/run
```

Recommended output policy:
- Study runs: write into `experiments/studies/{study_id}/runs/...` for clean lineage and cross-hypothesis organization.
- Scenario-local `scenarios/<name>/outputs/...`: reserve for ad hoc/manual scenario testing outside study orchestration.

Rolling summary artifacts for humans/LLMs:
- `experiments/studies/{study_id}/SUMMARY.md`
- `experiments/studies/{study_id}/generated/summary_log.jsonl`

Append summary entries from CLI:

```bash
uv run silisocs-study --study experiments/studies/study_template_v1 summary-append \
  --author analyst \
  --hypothesis h1_timeline_mechanism \
  --note "Observed stronger interaction rates in recsys conditions" \
  --evidence experiments/studies/recsys_behavior_sweep/generated/repro_lock.json

The runner also writes an organized analysis tree for notebook use:

```text
experiments/studies/{study_id}/generated/organized/
  study_summary.yaml
  summary.json
  {hypothesis_id}/
    hypothesis.yaml
    runs.json
    {condition_id}/{scenario}/seed_{seed}/
      config.yaml
      run -> <symlink to simulation output>
      eval.json -> <symlink to first evaluator output>
      eval/{eval_id}/...
```
```

Subset execution controls:

```bash
uv run silisocs-study --study experiments/studies/study_template_v1 run \
  --only-hypothesis h1_timeline_mechanism \
  --only-sub-experiment bill_bias
```

### Human/LLM analysis fields

You can keep interpretation text in the same study YAML:
- `study.notes.*`
- `hypotheses.<id>.analysis.*`
- `hypotheses.<id>.conditions.<id>.analysis.*`

Start from:
- `experiments/studies/study_template_v1/study.yaml`
