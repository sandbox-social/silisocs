# EXPERIMENTS.md

This guide is for LLM agents helping design and create new simulation scenarios using configuration (YAML) — without modifying code.

## 1) Scenario Design Workflow

A complete scenario consists of:

1. **Persona Pipeline** — Define agent populations (classes, counts, data sources, personas)
2. **Social Network** — Define followership patterns, activity rates, network topology
3. **Probes** — Define evaluation questions to ask agents during/after simulation
4. **Setting & Event** — Provide narrative context for the simulation
5. **Optional sim/platform overrides** — Customize sim parameters or platform type for this scenario

### Creating a New Scenario

**Directory Structure:**
```
scenarios/{scenario_name}/conf/
├── scenario/
│   └── default.yaml                (Required: @package _global_)
├── agents/
│   └── default.yaml                (Required: @package agents)
├── sim.yaml                        (Optional: partial sim overrides)
└── env.yaml                        (Optional: partial env overrides)
```

### File 1: scenario/default.yaml

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

See `scenarios/election/conf/scenario/default.yaml` for a production scenario with all sections.

### File 2: Persona Pipeline (`agents/default.yaml`)

Defines how to construct agent entities from data sources:

```yaml
# @package agents
persona_pipeline:
  processing_mode: raw              # raw | formative

  defaults:                         # Applied to all classes
    params:
      scenario_context: ${event.context}
      bio: ""
      style: ""
      goal: null
    shared_memories:
      - "You are a resident of Storhampton"
      - ${event.context}

  classes:
    voter:
      count: 497
      prefab_module: silisocs.agents.entity
      sim_role_name: voter           # For activity rates
      model: null                     # null = use sim.llm.name, or override per-class
      data:
        source: hf_dataset
        dataset: nvidia/Nemotron-Personas-USA
        split: train
      field_map:
        context: persona
        name: full_name              # Map HF dataset field to agent field
      params:
        goal: "Have a good day and vote in the election"
      shared_memories:
        - "Voters care about the town's economy"
      flow_tag: default              # Optional: for multi-flow routing

    candidate:
      count: 2
      prefab_module: silisocs.agents.entity
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
      prefab_module: silisocs.agents.entity
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
| Inline YAML | `source: inline`<br>`records: [{name: Alice, bio: "..."}]` | Defined directly in scenario |
| Config Reference | `source: config_path`<br>`path: candidates` | Dot-path into this YAML file |

### File 3: Social Network Topology

Controls how agents follow each other:

```yaml
social_network:
  # Activity state transitions (per sim_role_name)
  activity_transition_rates:
    voter:
      inactive_to_active: 0.1
      active_to_inactive: 0.2
    candidate:
      inactive_to_active: 0.8
      active_to_inactive: 0.1

  # Agents that everyone follows
  fully_connected_targets:
    - candidate
    - news_account

  # Base probability for random followership
  base_followership_probability: 0.4

  # Network topology algorithm
  network_type: barabasi_albert      # barabasi_albert | erdos_renyi
  barabasi_albert_m: 30              # Preferential attachment parameter
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
    include_entities: []
    exclude_entities: []

  queries:
    vote_pref:
      probe_name: vote_pref
      query_type: ChoiceProbe
      query_data:
        name: VotePref
        question: "In one word, name the candidate you want to vote for"
        choices:
          - Bill Fredrickson
          - Bradley Carter

    favorability_bill:
      probe_name: favorability_bill
      query_type: NumericRatingProbe
      query_data:
        name: FavorabilityBill
        question: "Rate Bill Fredrickson on a scale of {lo} to {hi}"
        lo: 1
        hi: 10

    will_vote:
      probe_name: will_vote
      query_type: BinaryProbe
      query_data:
        name: WillVote
        question: "Will you cast a vote?"
```

### File 7: Scenario-Specific Entities (Optional)

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
Run parameters (`num_agents`, `num_steps`, `seed`) belong in `scenario/default.yaml`, not here.

```yaml
# No @package header needed — merged into sim group automatically

# Only list fields that differ from base defaults
action_mode: generic
tool_calling:
  mode: multi

llm:
  name: gpt-4o                     # Override model for this scenario

engine:
  action_loop:
    built_in: open_ended
    params:
      max_actions: 10
      done_token: FINISHED

enabled_actions:                   # Restrict to specific actions (exact function names)
  - create_tweet
  - like_tweet
  - repost_tweet
  - follow_user
```

**Important:** Missing fields automatically fall back to `src/silisocs/conf/sim/base.yaml` defaults. This keeps scenario files minimal.

### File 8: Optional env.yaml

Create if you want a different platform or GM component settings:

```yaml
# No @package header needed — merged into env group automatically

platform_type: reddit_like         # twitter_like | reddit_like | mastodon
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

social_network:
  activity_transition_rates:
    active:
      inactive_to_active: 0.9
      active_to_inactive: 0.1
    inactive:
      inactive_to_active: 0.05
      active_to_inactive: 0.5
```

Then in `sim.yaml` (if needed):

```yaml
enable_gm_multi_flow: true
# This allows different observation/resolution for each flow
# (optional — both flows still work with default components)
```

### Pattern 2: Different Recommendation Algorithms per Agent Class

Configure in scenario persona_pipeline:

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
      prefab_module: silisocs.agents.fixed_entity
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
uv run silisocs --config-path scenarios/my_scenario/conf
```

**With overrides:**
```bash
uv run silisocs --config-path scenarios/my_scenario/conf \
    num_agents=1000 num_steps=100
```

**From Dashboard:**
1. Open `uv run streamlit run src/silisocs/dashboard/launch_app.py`
2. Select scenario from dropdown
3. Modify settings as needed
4. Click "Save Scenario" to persist
5. Click "Run Simulation"

---

## 5) Understanding Multi-Flow (Advanced)

If you want different agent populations to receive different observations or components, enable multi-flow:

```yaml
enable_gm_multi_flow: true

gm:
  preset: shared_flow              # Allows multiple component instances
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
   - `src/silisocs/conf/scenario/default.yaml` (`@package _global_`)
   - `src/silisocs/conf/agents/default.yaml` (`@package agents`)
   - `src/silisocs/conf/sim/base.yaml` (`@package sim`)
   - `src/silisocs/conf/env/twitter_like.yaml` (`@package env`)
   - `src/silisocs/conf/evals/base.yaml` (`@package evals`)

2. **Your scenario overrides** (higher priority via SearchPath plugin)
   - `scenarios/my_scenario/conf/scenario/default.yaml` (replaces package scenario group)
   - `scenarios/my_scenario/conf/agents/default.yaml` (replaces package agents group)
   - `scenarios/my_scenario/conf/sim.yaml` (merged into sim group, if present)
   - `scenarios/my_scenario/conf/env.yaml` (merged into env group, if present)

3. **CLI overrides** (highest priority)
   - `num_agents=500`
   - `sim.llm.name=gpt-4o`

**Fallback behavior:**
- If `sim.yaml` omits `llm.name`, uses `base.yaml` value (`gpt-4o-mini`)
- If `env.yaml` omits `use_server`, uses `twitter_like.yaml` value (`false`)
- If scenario omits a probe, that probe is not deployed

This means your scenario files only need to specify what's **different** from defaults.

---

## 7) Reference: Default Values

**From `src/silisocs/conf/scenario/default.yaml`** (at config root):
- `num_agents`: 100
- `num_steps`: 50
- `seed`: 1
- `run_name`: run1

**From `src/silisocs/conf/sim/base.yaml`:**
- `sim.llm.name`: gpt-4o-mini
- `sim.action_mode`: custom
- `sim.tool_calling.mode`: single
- `sim.memory_backend`: list
- `sim.engine.preset`: base
- `sim.engine.action_loop.built_in`: single_action

**From `src/silisocs/conf/env/twitter_like.yaml`:**
- `platform_type`: twitter_like
- Supports: `create_tweet`, `like_tweet`, `repost_tweet`, `reply_to_tweet`, `follow_user`

---

## 8) Troubleshooting

**Problem:** "Scenario file not found"
- **Check**: `scenarios/{name}/conf/scenario/default.yaml` exists
- **Check**: File has `# @package _global_` as first line

**Problem:** "Unknown field in persona_pipeline"
- **Check**: `docs/configuration.md` Scenario Config section for valid fields
- **Check**: All agent classes have `prefab_module` and `data.source`

**Problem:** "Agent count exceeds num_agents"
- **Check**: Sum of all `classes[*].count` <= `num_agents`
- **Fix**: Adjust counts or increase `num_agents` in sim.yaml

**Problem:** "Probe query_type unknown"
- **Check**: Query types are: ChoiceProbe, NumericRatingProbe, BinaryProbe, FreeTextProbe
- **Check**: `docs/probes.md` for detailed field requirements per type

---

## 9) Next Steps

1. **Study existing example**: review `scenarios/election/conf/scenario/election.yaml`
2. **Understand defaults**: check `src/silisocs/conf/sim/base.yaml`
3. **Create your scenario**: mkdir and scaffold with the structure above
4. **Test it**: run via CLI with `--config-path`
5. **For complex scenarios**: consult ARCHITECTURE.md (multi-flow deep dive) and AGENTS.md (custom agents)

---

## 10) Structured Study Orchestration (`experiments/run_study.py`)

For multi-condition research studies (hypothesis trees, seed sweeps, condition-specific evaluators), use:

```bash
uv run python -m experiments.run_study --study experiments/studies/study_template_v1 plan
uv run python -m experiments.run_study --study experiments/studies/study_template_v1 generate-bash
uv run python -m experiments.run_study --study experiments/studies/study_template_v1 run
```

Code placement:
- Canonical runner implementation is in `experiments/run_study.py`.
- Use module entrypoint: `uv run python -m experiments.run_study ...`.

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
          sim.timeline_mode: follower_chronological
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
            - scenario={scenario}
            - sim.seed={seed}
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
| `hypotheses` | mapping | yes | Hypothesis tree with conditions/cases. |

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
- `run_name_template`: Optional placeholder template for `sim.run_name`.
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
uv run python -m experiments.run_study --study experiments/studies/study_template_v1 summary-append \
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
      evals/{eval_id}/...
```
```

Subset execution controls:

```bash
uv run python -m experiments.run_study --study experiments/studies/study_template_v1 run \
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
