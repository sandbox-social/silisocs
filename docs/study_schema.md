# Study Schema

A study is a self-contained investigation of a research question using simulation
experiments. This page is the **reference** for the directory layout, every file
format, and the notebook structure that studies follow.

For what a study *is* and how to design one, read the
[Study Guide](study_guide.md). For the commands that produce these files, read
the [Study Runner Reference](experiments.md).

## Directory Layout

A study's **workspace** is not the directory its `study.yaml` sits in. It is
derived, once, from the repo root and the study's `study_id` (which itself falls
back to `study.name`):

```text
workspace  = <repo-root>/experiments/studies/<study_id>
generated/ = <repo-root>/experiments/studies/<study_id>/generated
```

`<repo-root>` is the `--repo-root` option of `silisocs-study` (default: the
current directory). `run`, `organize`, and `summary-append` all resolve from this
one derivation, so a study cannot end up with two artifact trees. The practical
consequence: the repository's `experiments/studies/study_template_v1/study.yaml`
declares `study_id: recsys_behavior_sweep`, so *its* runs and generated files
land under `experiments/studies/recsys_behavior_sweep/`. Keep the directory name
and `study_id` identical unless you mean to split them.

```
experiments/
  studies/
    study_template_v1/                   # Canonical study template (repository content)
    {study_id}/                          # Workspace: derived from study_id, not the study.yaml dir
      study.yaml                        # Study definition (authored, version-controlled)
      eval.py                           # Study-specific evaluation script (authored per study)
      notebook.ipynb                    # Results notebook (authored, version-controlled)
      SUMMARY.md                        # Human-readable notes and findings (summary-append default)
      generated/                        # Reproducibility locks, eval copies, organized views
        plan.json                       # Expanded run plan (written at the start of EVERY `run`)
        run_study.sh                    # The plan as a bash script (written by `run`)
        summary_log.jsonl               # summary-append entries (default location)
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
| `plan` | nothing by default (prints the plan; `--output` writes it as JSON). Also composes every unique condition config and exits 1 if any fails — see [Plan-time config check](experiments.md#plan-time-config-check) |
| `run` (start) | `generated/plan.json`, `generated/run_study.sh` — written at the start of **every** `run` invocation, not only `--dry-run`, so a study directory always records what the last invocation was about to launch |
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
| `overrides` | dict | Hydra override map for this condition, for example `sim.llm.name: gpt-4o-mini`. A key inside a slot's `params` block that the base config leaves empty **must** be written with a `++` prefix (quoted, e.g. `"++sim.engine.turn_policy.params.max_actions": 3`) — see below. |
| `execution.mode` | string | `run` or `reuse_existing`. |
| `reuse.runs` | list | Existing run records used only when `execution.mode: reuse_existing`. |

**Overrides of slot `params` need `++`.** Hydra struct-validates CLI overrides
against the base config *before* a scenario's flat `sim.yaml` / `env.yaml` is
merged, and every [slot](configuration.md#slots) defaults to an empty `params`
map there. So `sim.engine.turn_policy.params.max_actions: 3` fails with
`Could not override ... Key 'max_actions' is not in struct`, while
`"++sim.engine.turn_policy.params.max_actions": 3` works. This applies to
turn/step/loop policy params, GM component params, memory params, backend
params, and checkpoint save/restore params. `silisocs-study ... plan` catches it
before you spend anything — see
[Overriding slot `params` needs `++`](experiments.md#slot-params-plus-plus).

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
    "seed": 42,
    "run_id": "h1_model_capacity__gpt4o-mini__ai_conference__seed42",
    "status": "success",
    "run_dir": "experiments/studies/style_diversity/runs/h1_model_capacity/gpt4o-mini/ai_conference/seed_42/run",
    "eval_paths": {
      "action_metrics": "experiments/studies/style_diversity/generated/eval/h1_model_capacity/gpt4o-mini/ai_conference/seed_42/action_metrics/action_metrics_detailed.json"
    },
    "agents": {
      "Agent Name": { "self_bleu": 0.32, "lexical_diversity": 0.30 }
    },
    "aggregated": {
      "self_bleu": 0.45, "lexical_diversity": 0.23, "inter_agent_distinctiveness": 0.56
    },
    "aggregated_stats": { },
    "summary": { "total_posts": 96, "agents": 9, "steps": 9 }
  },
  {
    "condition": "gpt4o",
    "scenario": "ai_conference",
    "seed": 42
  }
]
```

| Field | Description |
|-------|-------------|
| `condition` / `scenario` / `seed` | The run's coordinates in the grid |
| `run_id` | The expanded run id (`{hypothesis}__{condition}__{scenario}__seed{seed}`) |
| `status` | `success`, `reused`, `skipped_complete`, `failed`, or `timeout` |
| `run_dir` | Absolute path to the simulation output directory (`null` if the run never produced one) |
| `eval_paths` | `{eval_id: output path}` for every evaluator that produced an output |
| `agents` / `aggregated` / `aggregated_stats` / `summary` | The run's merged evaluator payloads (see below) |

One entry per run: replicate seeds of one condition appear as separate entries,
distinguishable by `seed` and `run_id`. When a run has several evaluators, their
payloads are **merged** into one entry — `aggregated` and `agents` metrics are
averaged across the payloads and numeric `summary` keys are summed. There is no
`checkpoint` key in a `runs.json` entry; use `run_dir` and read the run's
`checkpoints/` directory if you need one.

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
      "seed": 42,
      "run_id": "h1_model_capacity__gpt4o-mini__ai_conference__seed42",
      "aggregated": { "self_bleu": 0.45, "lexical_diversity": 0.23 },
      "summary": { "total_posts": 96, "agents": 9, "steps": 9 }
    },
    {
      "hypothesis": "h1_model_capacity",
      "condition": "gpt4o-mini",
      "scenario": "ai_conference",
      "seed": 43,
      "run_id": "h1_model_capacity__gpt4o-mini__ai_conference__seed43",
      "aggregated": { },
      "summary": { }
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

`conditions` contains **one entry per run**, not one per (hypothesis, condition,
scenario) triple: replicate seeds of the same condition each get their own
entry, and `seed` plus `run_id` are what tell them apart. Each entry carries
`hypothesis`, `condition`, `scenario`, `seed`, `run_id`, and the run's merged
`aggregated` and `summary` blocks (no per-agent detail — that stays in
`runs.json`).

`metrics_by_condition` is the reduction over those entries: for each
`(hypothesis, condition)` it averages every `aggregated` metric across the
entries that reported it — i.e. across scenarios *and* seed replicates —
nested by hypothesis so condition names that appear in multiple hypotheses don't
collide.

**Only the `aggregated` block reaches this comparison surface.** An evaluator
that emits nothing under `aggregated` contributes no comparable metrics, which
is why a study running only `builtin.activity_summary` / `builtin.probe_summary`
produces empty `metrics_by_condition` — see
[Which presets produce comparable metrics](experiments.md#which-presets-produce-comparable-metrics).

A parallel `metrics_stats_by_condition` section reports cross-replicate statistics for each averaged metric: `n`, `mean`, `stdev`, `ci95_low`, `ci95_high` (same field semantics as `aggregated_stats` in `runs.json`). `metrics_by_condition` keeps its plain-mean shape for backward compatibility; use `metrics_stats_by_condition` when you need error bars or confidence intervals across seed replicates.

## The eval.py Contract

A study that needs metrics the builtin presets don't cover ships
`experiments/studies/{study_name}/eval.py`. The study runner discovers and
invokes it automatically via the `builtin.study_eval` preset.

### Required CLI interface

```bash
# Primary: how the study runner invokes it for each run
# (<python> is the study's resolved interpreter, cwd is the repo root):
uv run python experiments/studies/{study_name}/eval.py \
    --run-dir <path/to/run_dir> \
    --output  <path/to/eval.json>

# Optional convention, never used by the runner: manual comparison across runs
uv run python experiments/studies/{study_name}/eval.py \
    --compare <run_dir1> <run_dir2> ...
```

| Argument | Required | Description |
|----------|----------|-------------|
| `--run-dir PATH` | yes (primary) | Simulation run directory containing `action_events.jsonl` |
| `--output PATH` | yes | Output path for `eval.json` (must end in `.json`) |
| `--compare DIR...` | alt to `--run-dir` | Two or more run dirs for side-by-side comparison |

### Input files

`eval.py` reads from the run directory, not a checkpoint file. Locate the event
logs with `resolve_action_event_files` / `load_run` rather than by path — a
multi-GM run writes them per game master under `<run>/<gm_name>/`:

| File | Required | Purpose |
|------|----------|---------|
| `run_manifest.json` | yes, for `load_run` | Run metadata and artifact index |
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

1. Accept `--run-dir` and `--output` (both required); `--compare` is an optional
   convenience the runner never uses.
2. Read the run through the supported loaders, not by rediscovering the file
   layout:
   - `silisocs.evaluations.run_artifact.load_run(run_dir) -> RunArtifact` reads
     the run's required `run_manifest.json` (raising `FileNotFoundError` if it is
     missing) and gives you `.iter_actions()`, `.iter_exposures()`,
     `.iter_probes()`, `.iter_harness_events()`, the cached `.actions` /
     `.exposures` / `.probes` lists, plus `.metrics`, `.status`, `.scenario`,
     `.seed`, `.num_agents`, `.num_steps`, `.llm_name`, `.health`, and
     `.provenance`.
   - `silisocs.evaluations.action_events.resolve_action_event_files(run_dir)`
     returns the run's action logs directly. **Use it (or `load_run`) rather than
     opening `<run>/action_events.jsonl`:** a multi-GM run has no flat file — each
     game master writes `<run>/<gm_name>/action_events.jsonl`, and the resolver
     returns all of them. `resolve_exposure_event_files` /
     `resolve_probe_event_files` / `resolve_harness_event_files` are the
     equivalents for the other streams.
   - `silisocs.evaluations.run_artifact.iter_jsonl(files)` is the tolerant reader
     for those paths (it skips blank and malformed lines).
3. Compute metrics; write `eval.json` in the schema format above.
4. Exit 0 on success. **A non-zero exit is a loud failure:** the runner counts it,
   prints `⚠ Evaluators failed: N/M` on stderr, and `run` itself returns 1 (see
   [Exit status](experiments.md#exit-status-and-evaluator-reporting)). Do not
   swallow errors to keep the exit code clean.

**Only numeric values under the `aggregated` key reach the study comparison
surface** — `generated/organized/summary.json`, `metrics_by_condition`, and
`metrics_stats_by_condition`, which are what the notebooks and Studio's
condition-comparison panel read. The `summary` and `agents` blocks are per-run
artifacts only: they are preserved in `runs.json` and in each `conditions` entry,
but nothing averages or compares them across conditions. Put every number you
intend to compare in `aggregated`, keep it flat (no nesting), and use plain
numbers or `null`.

Your script runs under [the same interpreter as the runs it evaluates](experiments.md#the-interpreter-runs-and-evaluators-share)
(`RUN_STUDY_PYTHON`, else the interpreter running `silisocs-study`), with the
repo root as its working directory, so `import silisocs...` resolves exactly as
it did during the run.

#### The `action_events.jsonl` row schema

Each line is one committed action. The fields:

```json
{
  "source_user": "Alice",
  "label": "post",
  "data": {"content": "Beautiful morning in the neighborhood!", "post_id": 127},
  "gm_name": "twitter_like_gm",
  "backend_type": "twitter_like",
  "episode": 3,
  "event_type": "action",
  "event_index": 42
}
```

| Field | Meaning |
|---|---|
| `source_user` | The acting agent's name (`system` for backend-initiated events) |
| `label` | The action label, e.g. `post`, `reply`, `repost`, `like` |
| `data` | Action-specific payload (content, ids, derived logged fields) |
| `gm_name` / `backend_type` | Which game master and backend committed it |
| `episode` | The simulation step, 1-based for agent actions |
| `event_type` | `action` for these rows |
| `event_index` | Monotonic index within the log |

**Filter out the initialization rows.** World setup writes rows such as
`init_create_user`, `init_follow`, and a `system`-sourced `initialize` — all at
`episode: 0`. They describe the starting world, not agent behavior, and counting
them inflates every action metric. Filter on `label` not starting with `init_`
plus `source_user != "system"`, or simply take `episode > 0`, whichever matches
what you are measuring.

#### A complete `eval.py`

Copy this, then replace the metrics. It is a trimmed version of the working
`experiments/studies/misinformation_cta_demo/eval.py` — read that file for a
fuller example that also reads probe events.

```python
#!/usr/bin/env python3
"""Per-run evaluator: committed action mix for one run directory."""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from silisocs.evaluations.action_events import resolve_action_event_files
from silisocs.evaluations.run_artifact import iter_jsonl

CONTENT_LABELS = ("post", "reply", "repost", "like")


def evaluate_run_dir(run_dir: Path) -> dict[str, Any]:
    """Compute per-run scalars from the run's committed action events."""
    rows = [
        row
        for row in iter_jsonl(resolve_action_event_files(run_dir))
        if row.get("event_type") == "action"
        and not str(row.get("label", "")).startswith("init_")
    ]
    counts = Counter(str(row.get("label")) for row in rows)
    content_total = sum(counts[label] for label in CONTENT_LABELS)

    per_agent: dict[str, dict[str, float]] = defaultdict(dict)
    for row in rows:
        agent, label = str(row.get("source_user") or ""), str(row.get("label"))
        if agent and label in CONTENT_LABELS:
            per_agent[agent][label] = per_agent[agent].get(label, 0.0) + 1.0

    aggregated: dict[str, float | None] = {
        f"{label}_count": float(counts[label]) for label in CONTENT_LABELS
    }
    aggregated["amplification_share"] = (
        (counts["repost"] + counts["like"]) / content_total if content_total else None
    )
    # Only numbers under "aggregated" are compared across conditions.
    return {
        "source": str(run_dir),
        "aggregated": aggregated,
        "agents": dict(per_agent),
        "summary": {"action_events": len(rows), "content_actions": content_total},
    }


def main() -> None:
    """Parse --run-dir/--output and write the eval JSON."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(evaluate_run_dir(Path(args.run_dir)), indent=2, default=str),
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
```

Studies that don't need custom metrics can omit `eval.py` and use only the
`builtin.*` presets — see
[Evaluator presets](experiments.md#evaluator-presets) for the full list, and
[Which presets produce comparable metrics](experiments.md#which-presets-produce-comparable-metrics)
for which of them fill `metrics_by_condition`.

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
