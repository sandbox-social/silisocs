# Study Schema

A study is a self-contained investigation of a research question using simulation experiments. This document defines the directory layout, file formats, analysis pipeline, and notebook structure that all studies should follow.

## Directory Layout

```
experiments/
  run_study.py                           # Study planner/runner/evaluator orchestrator
  _internal/study_artifacts.py           # Private artifact organization helpers
  studies/
    {study_name}/
      study.yaml                        # Study definition (authored, version-controlled)
      eval.py                           # Study-specific evaluation script (authored per study)
      notebook.ipynb                    # Results notebook (authored, version-controlled)
      SUMMARY.md                        # Human-readable notes and findings
      generated/                        # Reproducibility locks, eval copies, organized views
        repro_lock.jsonl
        repro_lock.json
        study_index.json
        study_enriched.yaml
        eval/                           # Stable evaluator output copies
        organized/
          study_summary.yaml
          summary.json
          {hypothesis_id}/
            hypothesis.yaml             # Hypothesis definition (generated)
            runs.json                   # All eval records for this hypothesis (generated)
            {condition_id}/{scenario}/seed_{seed}/
              config.yaml               # Run configuration (frozen at launch)
              run -> <simulation output directory>
              eval.json -> <primary evaluator output>
              eval/{eval_id}/...
      runs/                             # Optional study-owned simulation output root

outputs/                                # Default simulation output root (gitignored)
```

`study.yaml` is the single source of truth — it defines the scientific hierarchy *and* maps conditions to concrete run/eval paths. It is authored by the user and checked into version control. The study runner writes generated reproducibility and evaluation artifacts under `experiments/studies/{study_id}/generated/`.

Raw simulation outputs usually live under `outputs/` or a study-specific
`output_root_override`. The study runner writes reproducibility locks and stable
evaluation copies under `experiments/studies/{study_id}/generated/`.

### Naming conventions

| Element | Format | Example |
|---------|--------|---------|
| Study name | `snake_case` | `style_diversity` |
| Hypothesis ID | `h{N}_{short_name}` | `h1_model_capacity` |
| Condition directory | `{iv}={value}` | `sim.llm.name=gpt-4o-mini` |
| Run directory | `run_{ISO timestamp}` | `run_2026-02-06T23-50-55` |
| Notebook file | `notebook.ipynb` (inside study dir) | `experiments/studies/style_diversity/notebook.ipynb` |

The `{iv}={value}` convention (inspired by Hive-style partitioning) makes the independent variable and its level readable from the path alone.

## File Formats

### study.yaml

The study definition file. This is the single source of truth: it defines the scientific hierarchy and maps each condition to concrete simulation output and eval paths. It is authored by the user and version-controlled.

```yaml
schema_version: 1

study:
  name: style_diversity
  study_id: style_diversity
  question: >-
    Does increasing LLM capacity reduce repetitive/groupthink behavior
    in multi-agent social media simulations?
  scenarios:
    - ai_conference
    - misinformation
  run_defaults:
    config_path: scenarios/{scenario}/conf
    seeds: [42, 7, 123]
    overrides:
      num_steps: 10

evaluations:
  - id: action_metrics
    preset: builtin.action_metrics_detailed

hypotheses:
  h1_model_capacity:
    statement: >-
      Larger language models produce more diverse agent behavior.
    independent_variable: model
    prediction: >-
      gpt4o outperforms gpt4o-mini on diversity metrics across scenarios.
    status: supported
    conditions:
      gpt4o-mini:
        overrides:
          sim.llm.name: gpt-4o-mini
      gpt4o:
        overrides:
          sim.llm.name: gpt-4o

  h2_temperature_effect:
    follows_from: h1_model_capacity
    motivation: >-
      H1 supported: gpt4o produced higher diversity. H2 asks whether
      sampling temperature drives the effect independently of model size.
    statement: >-
      Higher sampling temperature produces more diverse agent behavior,
      independent of model size.
    independent_variable: temperature
    prediction: >-
      temperature=1.0 outperforms temperature=0.2 on diversity metrics.
    status: testing
    conditions:
      temperature=0.2:
        overrides:
          sim.llm.temperature: 0.2
      temperature=1.0:
        overrides:
          sim.llm.temperature: 1.0

```

**Required top-level keys:** `study`, `hypotheses`.

**`study` fields:**

| Field | Type | Description |
|-------|------|-------------|
| `name` | string | Unique study identifier (matches directory name) |
| `study_id` | string | Stable output identifier; defaults to `name` when omitted |
| `question` | string | The research question in plain language |
| `scenarios` | list[string] | Scenario names used across all hypotheses |
| `run_defaults.config_path` | string | Scenario config directory, often `scenarios/{scenario}/conf`. |
| `run_defaults.overrides` | dict | Hydra overrides shared by all runs. Per-condition overrides are added on top. |
| `run_defaults.checkpoint_every_n_steps` | int \| null | Checkpoint cadence injected into every run as `sim.checkpoint.every_n_steps`. Defaults to `1` (a checkpoint every step) so `eval.py` can read the final checkpoint for action-type metrics. Set another positive integer for a sparser cadence, or `null`/`0`/`false` to skip the injection entirely (the runtime default then applies). An explicit `sim.checkpoint.every_n_steps` in `run_defaults.overrides` or a condition's `overrides` still takes precedence. |

**`hypotheses.{id}.conditions.{name}` fields:**

| Field | Type | Description |
|-------|------|-------------|
| `overrides` | dict | Hydra override map for this condition, for example `sim.llm.name: gpt-4o-mini`. |
| `execution.mode` | string | `run` or `reuse_existing`. |
| `reuse.runs` | list | Existing run records used only when `execution.mode: reuse_existing`. |

**`hypotheses.{id}.conditions.{name}.reuse.runs[]` fields:**

| Field | Type | Description |
|-------|------|-------------|
| `scenario` | string | Scenario name for this run |
| `source` | string | Path to the original simulation output directory |
| `eval` | string | Path to the evaluation JSON file |

### hypothesis.yaml

Generated under `generated/organized/{hypothesis_id}/hypothesis.yaml` from
`study.yaml`. A flat summary of one hypothesis — no run paths.

```yaml
id: h1_model_capacity
statement: >
  Larger language models produce more diverse agent behavior
  (higher lexical diversity, lower self-BLEU, more varied actions).
independent_variable: model
prediction: gpt4o outperforms gpt4o-mini on diversity metrics across scenarios.
status: testing          # testing | supported | refuted | inconclusive
conditions:
  - gpt4o-mini
  - gpt4o
```

| Field | Type | Description |
|-------|------|-------------|
| `id` | string | Matches the directory name |
| `statement` | string | Falsifiable hypothesis in one sentence |
| `independent_variable` | string | The variable being manipulated |
| `prediction` | string | Expected outcome if hypothesis is true |
| `status` | enum | One of: `testing`, `supported`, `refuted`, `inconclusive` |
| `conditions` | list[string] | Condition names (values of the IV) |

Followup hypotheses additionally have `follows_from` and `motivation` (see study.yaml above).

### runs.json

Generated under `generated/organized/{hypothesis_id}/runs.json`. It is a flat
list of all eval records for every condition x scenario × seed under this
hypothesis and is the primary data source for per-hypothesis notebook sections.

```json
[
  {
    "condition": "gpt4o-mini",
    "scenario": "ai_conference",
    "checkpoint": "outputs/ai_conference_experiment/2026-02-06_23-50-55/checkpoints/step_10_checkpoint.json",
    "agents": {
      "Agent Name": { "self_bleu": 0.32, "lexical_diversity": 0.30, ... }
    },
    "aggregated": {
      "self_bleu": 0.45, "lexical_diversity": 0.23, ..., "inter_agent_distinctiveness": 0.56
    },
    "summary": { "total_posts": 96, "agents": 9, "steps": 9, ... }
  },
  {
    "condition": "gpt4o",
    "scenario": "ai_conference",
    ...
  }
]
```

Each entry is the contents of a single `eval.json` plus `condition` and `scenario` keys. One entry per run (multiple entries per condition if replicate runs exist).

Each entry also carries an `aggregated_stats` map (empty when a run has fewer than two evaluator payloads). For every numeric metric that was averaged it reports replicate statistics:

```json
"aggregated_stats": {
  "self_bleu": {
    "n": 3,
    "mean": 0.45,
    "stdev": 0.04,
    "ci95_low": 0.35,
    "ci95_high": 0.55
  }
}
```

| Field | Description |
|-------|-------------|
| `n` | Number of values aggregated |
| `mean` | Sample mean (same value as the metric in `aggregated`) |
| `stdev` | Sample standard deviation (`null` when `n < 2`) |
| `ci95_low` / `ci95_high` | 95% confidence interval using the t-distribution (`mean ± t(0.975, n-1) · stdev / √n`); `null` when `n < 2`. For `n - 1 > 30` the normal approximation (1.96) is used. |

### config.yaml

Frozen snapshot of the run configuration. Captures everything needed to reproduce the run.

```yaml
source: outputs/ai_conference_experiment/2026-02-07_09-43-11
model_name: gpt-4o
model_config: gpt4o
scenario: ai_conference
world_description: Simulates groupthink dynamics at an AI conference
max_steps: 10
seed: 42
condition: gpt4o
hypothesis: h1_model_capacity
cli_overrides:
  - sim.llm.name=gpt-4o
  - num_steps=10
run_command: >-
  uv run python -m silisocs.runtime.runner --config-path scenarios/ai_conference/conf
  sim.llm.name=gpt-4o num_steps=10
```

**Required fields:**

| Field | Type | Description |
|-------|------|-------------|
| `source` | string | Path to the original simulation output |
| `model_name` | string | Actual model identifier used by the API |
| `model_config` | string | Runtime model provider, for example `openai` or `scripted` |
| `scenario` | string | Scenario name |
| `max_steps` | int | Number of simulation steps |
| `seed` | int | Random seed |
| `condition` | string | IV condition value |
| `hypothesis` | string | Hypothesis this run belongs to |
| `cli_overrides` | list[string] | The exact Hydra task overrides passed on the CLI, read from the current run config snapshot when available |
| `run_command` | string | Full command to reproduce this run (generated by the organizer from the source config path and CLI overrides) |

### eval.json

Per-run evaluation output. Contains three sections: per-agent metrics, aggregated metrics, and summary counts.

```json
{
  "checkpoint": "path/to/source/checkpoint.json",
  "agents": {
    "Agent Name": {
      "self_bleu": 0.05,
      "lexical_diversity": 0.45,
      ...
    }
  },
  "aggregated": {
    "self_bleu": 0.04,
    "lexical_diversity": 0.45,
    ...
    "inter_agent_distinctiveness": 0.36
  },
  "summary": {
    "total_posts": 96,
    "seed_posts": 15,
    "model_posts": 81,
    "replies": 60,
    "boosts": 2,
    "original_posts": 19,
    "total_actions": 90,
    "agents": 9,
    "steps": 9
  }
}
```

**Sections:**

| Section | Description |
|---------|-------------|
| `agents` | Per-agent metric dict. Keys are agent names; values are metric dicts. |
| `aggregated` | Mean across agents for each metric, plus any population-level metrics (e.g. `inter_agent_distinctiveness`). |
| `summary` | Integer counts: posts, actions, agents, steps. Used for sanity checks and action-type breakdowns. |

### summary.json

Generated at the study level. Two sections: a flat `conditions` list for per-run lookups, and `metrics_by_condition` for cross-condition comparison plots.

```json
{
  "conditions": [
    {
      "hypothesis": "h1_model_capacity",
      "condition": "gpt4o-mini",
      "scenario": "ai_conference",
      "aggregated": { "self_bleu": 0.45, "lexical_diversity": 0.23, ... },
      "summary": { "total_posts": 96, "agents": 9, "steps": 9 }
    },
    {
      "hypothesis": "h1_model_capacity",
      "condition": "gpt4o",
      "scenario": "ai_conference",
      "aggregated": { ... },
      "summary": { ... }
    }
  ],
  "metrics_by_condition": {
    "h1_model_capacity": {
      "gpt4o-mini": { "self_bleu": 0.33, "lexical_diversity": 0.29, ... },
      "gpt4o":      { "self_bleu": 0.04, "lexical_diversity": 0.49, ... }
    },
    "h2_temperature_effect": {
      "temperature=0.2": { ... },
      "temperature=1.0": { ... }
    }
  }
}
```

`conditions` contains one entry per (hypothesis, condition, scenario) triple — identical in shape to a per-run `eval.json` entry but without per-agent detail. `metrics_by_condition` averages each metric across scenarios, nested by hypothesis so condition names that appear in multiple hypotheses don't collide.

A parallel `metrics_stats_by_condition` section reports cross-replicate statistics for each averaged metric — `n`, `mean`, `stdev`, `ci95_low`, `ci95_high` (same field semantics as `aggregated_stats` in `runs.json`). `metrics_by_condition` keeps its plain-mean shape for backward compatibility; use `metrics_stats_by_condition` when you need error bars or confidence intervals across seed replicates.

## The eval.py Contract

Every study that needs style-diversity metrics ships `experiments/studies/{study_name}/eval.py`. `run_study.py` discovers and invokes it automatically via the `builtin.study_eval` preset.

### Required CLI interface

```bash
# Primary — called by run_study.py for each run:
uv run python experiments/studies/{study_name}/eval.py \
    --run-dir <path/to/run_dir> \
    --output  <path/to/eval.json>

# Optional — manual comparison across runs:
uv run python experiments/studies/{study_name}/eval.py \
    --compare <run_dir1> <run_dir2> ...
```

| Argument | Required | Description |
|----------|----------|-------------|
| `--run-dir PATH` | yes (primary) | Simulation run directory containing `action_events.jsonl` |
| `--output PATH` | yes | Output path for `eval.json` (must end in `.json`) |
| `--compare DIR...` | alt to `--run-dir` | Two or more run dirs for side-by-side comparison |

### Input files

`eval.py` reads from the run directory, not a checkpoint file:

| File | Required | Purpose |
|------|----------|---------|
| `action_events.jsonl` | yes | Post/reply/repost content — drives all text metrics |
| `checkpoints/step_*_checkpoint.json` | no | Optional checkpoint state for evaluators that need it. Study runs enable per-step checkpoints by default for evaluator support; tune or disable this via `study.run_defaults.checkpoint_every_n_steps` (or override `sim.checkpoint.every_n_steps` directly in `study.run_defaults.overrides`) if a study does not need them. If absent, checkpoint-derived metrics should be `null` or omitted rather than crashing. |
| `probe_events.jsonl` | no | Free-text probe responses for `probe_diversity` section |

The script finds the latest checkpoint automatically (`step_N` with largest N). It never crashes if the checkpoint directory is missing.

### Output format (eval.json)

```json
{
  "source": "outputs/misinformation/.../run_dir",
  "agents": {
    "Alice": { "self_bleu": 0.05, "lexical_diversity": 0.45, ... },
    ...
  },
  "aggregated": {
    "self_bleu": 0.04,
    "lexical_diversity": 0.45,
    "inter_agent_distinctiveness": 0.36,
    ...
  },
  "summary": {
    "total_posts": 96, "seed_posts": 15, "model_posts": 81,
    "replies": 60, "boosts": 2, "original_posts": 19,
    "total_actions": 90, "agents": 9, "steps": 9
  },
  "probe_diversity": { ... }
}
```

### Wiring into study.yaml

Add `builtin.study_eval` to the study-level `evaluations` list:

```yaml
evaluations:
  - id: style_diversity_eval
    preset: builtin.study_eval
```

`run_study.py` resolves `./eval.py` relative to the study directory and raises a clear error if the file doesn't exist.

### Writing eval.py for a new study

1. Accept `--run-dir` and `--output` (required) plus `--compare` (optional) — see interface above.
2. Use `load_run_dir(run_dir)` (or equivalent) to obtain posts and raw_log.
3. Compute metrics; write output as `eval.json` in the schema format above.
4. Return exit code 0 on success, non-zero on error.

Studies that don't need custom metrics can omit `eval.py` and use only the `builtin.*` presets (`builtin.action_metrics_detailed`, `builtin.probe_*`).

## Running a Study

To run a new study from scratch:

```bash
# Run all conditions × scenarios, evaluate, register, and organize in one command:
uv run python -m experiments.run_study --study experiments/studies/{study_name} run

# Run only a specific hypothesis:
uv run python -m experiments.run_study --study experiments/studies/{study_name} run \
    --only-hypothesis h1_model_capacity

# Preview without executing:
uv run python -m experiments.run_study --study experiments/studies/{study_name} run \
    --dry-run
```

`run_study.py` reads `study.yaml`, expands concrete runs, executes fresh runs or
declared `reuse_existing` records, runs the configured evaluators, writes
reproducibility artifacts, and rebuilds the organized view. Re-running a study is
safe when run output paths are deterministic or when conditions intentionally use
`execution.mode: reuse_existing`; the runner does not infer old runs from
arbitrary directories.

### Idempotent resume (RUN_COMPLETE markers)

After every successful run the runner writes a `RUN_COMPLETE.json` marker into
the run directory containing `run_id`, `finished_at`, `effective_config_sha256`,
and `return_code`. On the next `run` invocation, any run whose planned output
directory already contains this marker is skipped instead of re-executed: it is
recorded with `status: skipped_complete` (counted as a success in the summary),
the existing `effective_config.yaml` is re-hashed into the repro lock, and any
prior evaluator outputs under `generated/eval/` are re-linked. The runner prints
`Skipped N already-complete runs (use --force to re-run)` at the end.

```bash
# Resume a partially completed study (only failed/missing runs execute):
uv run python -m experiments.run_study --study experiments/studies/{study_name} run

# Ignore markers and re-run everything:
uv run python -m experiments.run_study --study experiments/studies/{study_name} run --force
```

Resume applies only to runs with a deterministic planned output directory (the
default `experiments/studies/{study_id}/runs/...` layout or an explicit
`output_root_override`). Failed or timed-out runs never write a marker, so they
re-run automatically.

### Cost/scale preflight

Before launching (and in `plan` output), the runner prints a preflight summary:
the number of planned runs, per-run `num_agents`/`num_steps` when derivable from
the resolved overrides (`?` otherwise), and the estimated total agent-steps.
When more than 50 runs would actually execute, the runner asks for confirmation
on a TTY and aborts in non-interactive sessions unless `--yes` is passed:

```bash
uv run python -m experiments.run_study --study experiments/studies/{study_name} run --yes
```

Already-complete (skipped) runs do not count toward the confirmation threshold.

**Prerequisites before running:**
1. `study.yaml` exists with `study.run_defaults`, `hypotheses`, and condition `overrides`
2. `eval.py` exists in the study directory
3. Conditions that should reuse prior outputs declare `execution.mode: reuse_existing`
   and list those outputs under `reuse.runs`

To re-organize without re-running simulations (e.g. after editing `study.yaml`):

```bash
uv run python -m experiments.run_study \
    --study experiments/studies/{study_name} organize
```

## Analysis Pipeline

The study runner owns planning, simulation execution, evaluation hooks, and
artifact organization. Generated study artifacts are written under
`experiments/studies/{study_id}/generated/`; raw simulation output goes wherever
the expanded Hydra overrides place it, commonly `outputs/` or a study-specific
`output_root_override`.

```
1. plan       expand hypotheses, conditions, scenarios, seeds, and overrides
2. run        call silisocs.runtime.runner for each runnable expanded run
3. evaluate   run configured builtin or study-local evaluator hooks
4. record     write repro_lock.jsonl, repro_lock.json, and study_index.json
5. organize   build generated/organized/ for notebook-friendly browsing
```

`organize` is idempotent and can be re-run from `repro_lock.json` after a
completed or partially completed study.

## Notebook Structure

The results notebook (`experiments/studies/{name}/notebook.ipynb`) follows a fixed 9-section structure. Each section serves a specific role in the analysis narrative.

### Section 1: Title + Setup
- **Type:** markdown + code
- **Content:** Study title, load `study.yaml`, load all `eval.json` files into a structured dict keyed by `(hypothesis_id, condition, scenario)`, load `summary.json`, set matplotlib defaults.
- **Output:** Print study name, question, hypotheses, number of eval files loaded.

### Section 2: Study Overview
- **Type:** markdown + code
- **Content:** Hypothesis statement, IV, prediction. Table of conditions showing: model, scenario, agents, steps, total posts, replies, originals, boosts.

### Section 3: Key Metrics Explained
- **Type:** markdown
- **Content:** For each key metric (typically 3-5): plain-language definition, display equation (labeled as *exact* or *intuitive* form), and a "why it matters" paragraph connecting the metric to the research question.

### Section 4: Headline Comparison
- **Type:** code + markdown
- **Plot:** Grouped bar chart of key metrics, values averaged across scenarios. Annotate direction (lower/higher = better). Add value labels on bars.
- **Narrative:** One-paragraph takeaway beneath the plot.

### Section 5: Full Metric Profile
- **Type:** code + markdown
- **Plot:** Radar/spider chart with all metrics, both conditions overlaid. Normalize so outward = better (flip repetition metrics via `1 - x`).
- **Narrative:** What the overall shape tells us; call out exceptions.

### Section 6: Scenario Consistency
- **Type:** code + markdown
- **Plot:** Faceted figure (one panel per scenario), each showing all metrics as grouped horizontal bars by condition.
- **Narrative:** Is the effect consistent across scenarios or scenario-dependent?

### Section 7: Per-Agent Distributions
- **Type:** code + markdown
- **Plot:** Strip/dot plots for key metrics, each agent as a point, colored by condition, pooled across scenarios. Mean markers. Print mean and std table.
- **Narrative:** Does the IV shift the mean, tighten variance, or both?

### Section 8: Behavioral Breakdown
- **Type:** code + markdown
- **Plot:** Stacked bar chart of action type counts (e.g. replies, originals, boosts) per condition, pooled across scenarios. Label segment counts.
- **Narrative:** Qualitative behavioral differences between conditions.

### Section 9: Takeaways
- **Type:** markdown
- **Content:** Bulleted key findings (with numbers), limitations (sample size, confounds), next steps.

### Conventions

- Load data via `Path(".")` — the notebook lives in the study directory alongside `study.yaml`.
- Use `%matplotlib inline`.
- Working/exploratory style: default matplotlib theme, clear axis labels.
- Figures approximately 8x5 to 10x6 inches, 100 dpi.
- Colors: use a consistent two-color scheme for the two conditions throughout.

## Extending the Schema

### Adding a new hypothesis to an existing study

1. Add the hypothesis entry to `study.yaml` (the source of truth). Include `statement`, `independent_variable`, `prediction`, `status: testing`, and an empty `conditions` map. The `hypothesis.yaml` files under `experiments/` are generated by the organizer — do not create them by hand.
2. Run simulations for each condition x scenario combination using `study.run_defaults` as the base, adding per-condition overrides.
3. Evaluate each run to produce `eval.json`.
4. Re-run `uv run python -m experiments.run_study --study experiments/studies/{study_name} organize`
   when you want to rebuild only the notebook-friendly organized tree from an
   existing `repro_lock.json`.
5. For baseline reuse, add `execution.mode: reuse_existing` plus `reuse.runs`
   under the relevant condition instead of duplicating simulation work.
6. Add hypothesis-specific sections to the notebook or create a separate notebook.

### Reusing a baseline condition across hypotheses

If a condition from an earlier hypothesis serves as the control for a later one
(e.g. `sim.llm.name=gpt-4o-mini` in H1 is also the baseline for H2), mark the
later condition as `execution.mode: reuse_existing` and reference the earlier
run's `source` and optional `eval` paths under `reuse.runs`. The organizer links
the existing run into the new hypothesis view. This avoids redundant API costs
and keeps results comparable.

```yaml
hypotheses:
  h1_model_capacity:
    conditions:
      gpt4o-mini:
        overrides:
          sim.llm.name: gpt-4o-mini

  h2_temperature_effect:
    follows_from: h1_model_capacity
    conditions:
      temperature=0.2:           # same run as h1 gpt4o-mini baseline — reused
        execution:
          mode: reuse_existing
        reuse:
          runs:
            - scenario: ai_conference
              seed: 42
              source: outputs/ai_conference_experiment/2026-02-06_23-50-55
              eval:   outputs/eval_style_diversity/baseline/ai_conference/eval.json
      temperature=1.0:
        overrides:
          sim.llm.temperature: 1.0
```

### Adding a followup hypothesis

A followup hypothesis is motivated by the result of a completed hypothesis. The workflow is:

1. **Close the parent.** Update its `status` in `study.yaml` to `supported`, `refuted`, or `inconclusive`. Record the key finding in a `finding` field (optional but recommended):
   ```yaml
   h1_model_capacity:
     status: supported
     finding: >-
       gpt4o produced 3× higher inter-agent distinctiveness than gpt4o-mini
       across both scenarios.
   ```

2. **Add the followup entry** to `study.yaml` with `follows_from` and `motivation`:
   ```yaml
   h2_temperature_effect:
     follows_from: h1_model_capacity
     motivation: >-
       H1 finding raises the question of whether temperature, not model size,
       is the true driver of diversity.
     statement: ...
     independent_variable: temperature
     ...
   ```

3. **Run, evaluate, and organize** as for any new hypothesis (steps 2–4 above).

4. **Extend the notebook.** Add a new section after the parent's section. Open with a "Motivation" cell that references the parent finding before presenting the new results. The Section 9 Takeaways should reflect the full hypothesis chain.

### Adding a new study

1. Create `experiments/studies/{study_name}/study.yaml` with the full study definition (see format above).
2. Preview the expanded runs with `uv run python -m experiments.run_study --study experiments/studies/{study_name} plan`.
3. Write `experiments/studies/{study_name}/eval.py` to produce per-run evaluation output when the builtin evaluator presets are not enough.
   - **Note:** not all studies need a standalone eval script. If the simulation already writes probe
     results (e.g. `probe_events.jsonl`) or requires a post-processing step (e.g.
     `scripts/judge_probe_results.py` for LLM-judged probes), adapt stage 2 accordingly and
     document the deviation in `study.yaml` under a top-level `analysis.notes` key.
4. Run `uv run python -m experiments.run_study --study experiments/studies/{study_name} run` to execute simulations, evaluators, reproducibility logging, and organization.
5. Use `organize` later to rebuild only `generated/organized/` from an existing `repro_lock.json`.
6. Create `experiments/studies/{study_name}/notebook.ipynb` following the notebook structure above.

### Adding replicate runs

Multiple `run_{timestamp}/` directories under the same `{iv}={condition}/{scenario}/` path represent replicate runs (e.g. different seeds). The analysis pipeline should average across replicates when computing `summary.json`, and the notebook should show replicate variance where available.
