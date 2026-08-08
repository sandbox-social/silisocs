# Study Schema

A study is a self-contained investigation of a research question using simulation
experiments. This page is the **reference** for the directory layout, every file
format, and the notebook structure that studies follow.

For what a study *is* and how to design one, read the
[Study Guide](study_guide.md). For the commands that produce these files, read
the [Study Runner Reference](experiments.md).

## Directory Layout

```
experiments/
  studies/
    study_template_v1/                   # Canonical study template (repository content)
    {study_name}/
      study.yaml                        # Study definition (authored, version-controlled)
      eval.py                           # Study-specific evaluation script (authored per study)
      notebook.ipynb                    # Results notebook (authored, version-controlled)
      SUMMARY.md                        # Human-readable notes and findings
      generated/                        # Reproducibility locks, eval copies, organized views
        plan.json                       # Expanded run plan (written by `run`)
        run_study.sh                    # The plan as a bash script (written by `run`)
        repro_lock.jsonl
        repro_lock.json
        study_index.json
        study_enriched.yaml
        logs/                           # Per-run stdout/stderr
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
      runs/                             # Study-owned simulation output root (default)
        {hypothesis_id}/{condition_id}/{scenario}/seed_{seed}/run/
                                        # One run directory per expanded run row
          run_manifest.json             # ... and the usual run artifacts
          RUN_COMPLETE.json             # Marker written when the run finishes

outputs/                                # Simulation output root for plain CLI runs (gitignored)
```

Study run directories are **not** timestamped and are not nested under a
`jobname_format` level: the study runner passes each expanded run an explicit
`++output_dir=experiments/studies/{study_id}/runs/{hypothesis_id}/{condition_id}/{scenario}/seed_{seed}/run`,
and a non-empty `output_dir` overrides Hydra's per-run path entirely (see
[Output Configuration](configuration.md#output-configuration)). That is what
makes a run's location a pure function of its coordinates in the grid — so
re-running a row replaces it in place, and `organize` can symlink to it without
a lookup. To point runs somewhere else, set
[`output_root_override`](experiments.md#templating-and-placeholders) instead.

`study.yaml` is the single source of truth: it defines the scientific hierarchy
*and* maps conditions to concrete run/eval paths. It is authored by the user and
checked into version control. Everything under
`experiments/studies/{study_id}/generated/` is written by the study runner —
reproducibility locks, stable evaluator copies, and the organized view. Raw
simulation outputs usually live under `outputs/`, the study-owned `runs/` root,
or a study-specific `output_root_override`.

### Naming conventions

| Element | Format | Example |
|---------|--------|---------|
| Study name | `snake_case` | `style_diversity` |
| Hypothesis ID | `h{N}_{short_name}` | `h1_model_capacity` |
| Condition directory | `{iv}={value}` | `sim.llm.name=gpt-4o-mini` |
| Run directory | `{hypothesis_id}/{condition_id}/{scenario}/seed_{seed}/run` | `h1_model_capacity/sim.llm.name=gpt-4o-mini/style_forum/seed_11/run` |
| Notebook file | `notebook.ipynb` (inside study dir) | `experiments/studies/style_diversity/notebook.ipynb` |

The `{iv}={value}` convention (inspired by Hive-style partitioning) makes the independent variable and its level readable from the path alone.

## Producing These Files

The [Study Runner Reference](experiments.md) documents every command and flag.
What matters for the schema is which files each stage writes:

| Stage | Writes |
|---|---|
| `plan` | nothing by default (prints the plan; `--output` writes it as JSON) |
| `run` (start) | `generated/plan.json`, `generated/run_study.sh` |
| `run` | run directories (plus a `RUN_COMPLETE.json` marker per finished run), `generated/logs/` |
| evaluate | `generated/eval/{hypothesis_id}/{condition_id}/{scenario}/seed_{seed}/{eval_id}/` |
| record | `generated/repro_lock.jsonl`, `repro_lock.json`, `study_index.json`, `study_enriched.yaml` |
| `organize` | `generated/organized/` (idempotent — rebuildable from `repro_lock.json` alone) |

Before a study can run: `study.yaml` declares `study.run_defaults`,
`hypotheses`, and condition `overrides`; `eval.py` exists if any evaluation uses
`builtin.study_eval`; and conditions that should reuse prior outputs declare
`execution.mode: reuse_existing` with those outputs under `reuse.runs`.
Re-running a study is safe when run output paths are deterministic or when
conditions intentionally use `reuse_existing` — the runner never infers old runs
from arbitrary directories.

## File Formats

### study.yaml

The study definition file. This is the single source of truth: it defines the scientific hierarchy and maps each condition to concrete simulation output and eval paths. It is authored by the user and version-controlled.

Execution-behavior keys (`run_name_template`, `output_root_override`,
`sub_experiment`, `execution.command`, `working_directory`, `runner_module`,
`seed_start`/`seed_repeats`) are documented where the runner is:
[Study Runner Reference](experiments.md).

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
`study.yaml`. A flat summary of one hypothesis, with no run paths.

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
    "checkpoint": "outputs/ai_conference/N30_T10_independent_run1/ai_conference_2026-02-06_23-50-55/checkpoints/step_10_checkpoint.json",
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
source: outputs/ai_conference/N30_T10_independent_run1/ai_conference_2026-02-07_09-43-11
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

`conditions` contains one entry per (hypothesis, condition, scenario) triple, identical in shape to a per-run `eval.json` entry but without per-agent detail. `metrics_by_condition` averages each metric across scenarios, nested by hypothesis so condition names that appear in multiple hypotheses don't collide.

A parallel `metrics_stats_by_condition` section reports cross-replicate statistics for each averaged metric: `n`, `mean`, `stdev`, `ci95_low`, `ci95_high` (same field semantics as `aggregated_stats` in `runs.json`). `metrics_by_condition` keeps its plain-mean shape for backward compatibility; use `metrics_stats_by_condition` when you need error bars or confidence intervals across seed replicates.

## The eval.py Contract

A study that needs metrics the builtin presets don't cover ships
`experiments/studies/{study_name}/eval.py`. The study runner discovers and
invokes it automatically via the `builtin.study_eval` preset.

### Required CLI interface

```bash
# Primary: called by the study runner for each run:
uv run python experiments/studies/{study_name}/eval.py \
    --run-dir <path/to/run_dir> \
    --output  <path/to/eval.json>

# Optional: manual comparison across runs:
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
| `action_events.jsonl` | yes | Post/reply/repost content: drives all text metrics |
| `checkpoints/step_*_checkpoint.json` | no | Optional checkpoint state for evaluators that need it. Study runs enable per-step checkpoints by default for evaluator support; tune or disable this via `study.run_defaults.checkpoint_every_n_steps` (or override `sim.checkpoint.every_n_steps` directly in `study.run_defaults.overrides`) if a study does not need them. If absent, checkpoint-derived metrics should be `null` or omitted rather than crashing. |
| `probe_events.jsonl` | no | Free-text probe responses for `probe_diversity` section |

The script finds the latest checkpoint automatically (`step_N` with largest N). It never crashes if the checkpoint directory is missing.

### Output format (eval.json)

```json
{
  "source": "outputs/misinformation/N20_T10_independent_run1/misinformation_2026-02-06_23-50-55",
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

The runner resolves `./eval.py` relative to the study directory and raises a clear error if the file doesn't exist.

### Writing eval.py for a new study

1. Accept `--run-dir` and `--output` (required) plus `--compare` (optional). See interface above.
2. Use `load_run_dir(run_dir)` (or equivalent) to obtain posts and raw_log.
3. Compute metrics; write output as `eval.json` in the schema format above.
4. Return exit code 0 on success, non-zero on error.

Studies that don't need custom metrics can omit `eval.py` and use only the
`builtin.*` presets — see
[Evaluator presets](experiments.md#evaluator-presets) for the full list.

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

- Load data via `Path(".")`: the notebook lives in the study directory alongside `study.yaml`.
- Use `%matplotlib inline`.
- Working/exploratory style: default matplotlib theme, clear axis labels.
- Figures approximately 8x5 to 10x6 inches, 100 dpi.
- Colors: use a consistent two-color scheme for the two conditions throughout.

## Extending the Schema

The authoring workflow for a new study, a new hypothesis, and a follow-up
hypothesis is the [Study Guide](study_guide.md). This section covers only the
schema-level rules those workflows depend on.

**`study.yaml` is the only file you hand-write.** The `hypothesis.yaml` files
under `generated/organized/` are produced by the organizer — creating one by hand
is always wrong; add the hypothesis entry to `study.yaml` and re-`organize`
instead. A new hypothesis entry needs `statement`, `independent_variable`,
`prediction`, `status: testing`, and a `conditions` map.

**Studies without an eval script.** If the simulation already writes the numbers
you need (e.g. `probe_events.jsonl`) or the metrics come from a separate
post-processing step (e.g. an LLM-judge pass over probe results), skip `eval.py`,
use the `builtin.probe_*` presets, and document the deviation in `study.yaml`
under a top-level `analysis.notes` key.

**Extending the notebook for a follow-up.** Add a new section after the parent's,
opening with a "Motivation" cell that references the parent's `finding` before
presenting new results. Section 9 Takeaways should reflect the full hypothesis
chain.

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
      temperature=0.2:           # same run as h1 gpt4o-mini baseline, reused
        execution:
          mode: reuse_existing
        reuse:
          runs:
            - scenario: ai_conference
              seed: 42
              source: outputs/ai_conference/N30_T10_independent_run1/ai_conference_2026-02-06_23-50-55
              eval:   outputs/eval_style_diversity/baseline/ai_conference/eval.json
      temperature=1.0:
        overrides:
          sim.llm.temperature: 1.0
```

### Follow-up hypothesis fields

A follow-up hypothesis carries two extra keys, and closing its parent adds a
third. Nothing else about the entry differs from a first-round hypothesis.

```yaml
hypotheses:
  h1_model_capacity:
    status: supported            # testing | supported | refuted | inconclusive
    finding: >-                  # optional, recommended once status is closed
      gpt4o produced 3× higher inter-agent distinctiveness than gpt4o-mini
      across both scenarios.

  h2_temperature_effect:
    follows_from: h1_model_capacity
    motivation: >-
      H1 finding raises the question of whether temperature, not model size,
      is the true driver of diversity.
    statement: ...
    independent_variable: temperature
```

### Adding replicate runs

Replicates are seeds: the `seed_{seed}/run` directories under the same
`{hypothesis_id}/{condition_id}/{scenario}/` path are replicate runs of one
condition. The analysis pipeline should average across them when computing
`summary.json`, and the notebook should show replicate variance where available.
