# Study Runner Reference

This page is the reference for the **study runner** — the commands, selection
filters, execution modes, evaluator presets, and cluster dispatch that turn a
`study.yaml` into a grid of runs.

The three study pages divide up like this:

| Page | Answers |
|---|---|
| [Study Guide](study_guide.md) | What a study is and how to design one, step by step |
| **This page** | How to invoke the runner and what each knob does |
| [Study Schema](study_schema.md) | The exact shape of `study.yaml` and every generated file |

The runner ships inside the package as the `silisocs.studies` subpackage,
exposed as the **`silisocs-study`** console command (available after
`pip install silisocs`). The examples below use `silisocs-study`; the equivalent
module form is `python -m silisocs.studies.run_study ...`. The old
`experiments.run_study` module path is removed in the 0.x native runtime.

Use the runner when you need hypothesis trees, seed replication,
condition-specific Hydra overrides, optional exact run commands, or multiple
evaluators per run.

## Commands

```sh
uv run silisocs-study --study experiments/studies/study_template_v1 plan
uv run silisocs-study --study experiments/studies/study_template_v1 generate-bash
uv run silisocs-study --study experiments/studies/study_template_v1 run
uv run silisocs-study --study experiments/studies/study_template_v1 organize
uv run silisocs-study --study experiments/studies/study_template_v1 summary-append \
  --author analyst --hypothesis h1_timeline_mechanism \
  --note "Observed higher interaction counts in recsys arms" \
  --evidence experiments/studies/recsys_behavior_sweep/generated/repro_lock.json
```

| Command | Does |
|---|---|
| `plan` | Expand the study into concrete runs and print the plan (plus the preflight summary) without executing anything |
| `generate-bash` | Write a bash script of the plan to the required `--output` path, so the same runs can be executed outside the runner (`run` itself also records its plan as `generated/run_study.sh`) |
| `run` | Execute runs, run evaluators, write reproducibility artifacts, and rebuild the organized tree |
| `organize` | Rebuild only `generated/organized/` from an existing `repro_lock.json` (idempotent; safe after editing `study.yaml`) |
| `summary-append` | Append a dated finding to the study's summary log and `SUMMARY.md` |
| `submitit` / `slurm-array` | Dispatch the same filtered plan to a cluster (see [Cluster dispatch](#cluster-dispatch)) |

When `run` executes, the pipeline is:

```text
1. plan       expand hypotheses, conditions, scenarios, seeds, and overrides
2. run        launch the runtime for each runnable expanded run
3. evaluate   run configured builtin or study-local evaluator hooks
4. record     write repro_lock.jsonl, repro_lock.json, and study_index.json
5. organize   build generated/organized/ for notebook-friendly browsing
```

`run` builds both the raw and organized outputs; `organize` rebuilds just the
organized view later.

### Selection filters

Every filter applies to `plan`, `run`, `generate-bash`, `submitit`, and
`slurm-array` alike:

- `--only-hypothesis`
- `--only-condition`
- `--only-sub-experiment`
- `--only-seed`
- `--only-run-id`

```sh
uv run silisocs-study --study experiments/studies/study_template_v1 run \
  --only-hypothesis h2_followup_from_h1
uv run silisocs-study --study experiments/studies/study_template_v1 run \
  --only-sub-experiment bill_bias
```

## Runner behavior

### Idempotent resume (`RUN_COMPLETE` markers)

After every successful run the runner writes a `RUN_COMPLETE.json` marker into
the run directory containing `run_id`, `finished_at`, `effective_config_sha256`,
and `return_code`. On the next `run` invocation, any run whose planned output
directory already contains this marker is skipped instead of re-executed: it is
recorded with `status: skipped_complete` (counted as a success in the summary),
the existing `effective_config.yaml` is re-hashed into the repro lock, and any
prior evaluator outputs under `generated/eval/` are re-linked. The runner prints
`Skipped N already-complete runs (use --force to re-run)` at the end.

```sh
# Resume a partially completed study (only failed/missing runs execute):
uv run silisocs-study --study experiments/studies/my_study run

# Ignore markers and re-run everything:
uv run silisocs-study --study experiments/studies/my_study run --force
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
on a TTY and aborts in non-interactive sessions (CI, batch jobs) unless `--yes`
is passed:

```sh
uv run silisocs-study --study experiments/studies/my_study run --yes
```

Already-complete (skipped) runs do not count toward the confirmation threshold.

### Checkpoint cadence

Study runs are launched with `sim.checkpoint.every_n_steps=1` by default so
evaluators can read the final checkpoint. Tune it with
`study.run_defaults.checkpoint_every_n_steps`, or disable the injection with
`null`/`0`/`false`; an explicit `sim.checkpoint.every_n_steps` in any
`overrides` map still wins. See
[`run_defaults`](study_schema.md#studyyaml).

## Templating and placeholders

Supported placeholders:

- `{run_id}`
- `{study_name}`
- `{study_id}`
- `{hypothesis_id}`
- `{condition_id}`
- `{scenario}`
- `{seed}`

They work in:

- `study.run_defaults.run_name_template`
- `study.run_defaults.output_root_override`
- `conditions.<id>.run_name_template`
- `conditions.<id>.output_root_override`
- `conditions.<id>.execution.command` entries

```yaml
study:
  run_defaults:
    run_name_template: "{study_id}_{hypothesis_id}_{condition_id}_{scenario}_seed{seed}"
    output_root_override: "experiments/studies/{study_id}/runs/{hypothesis_id}/{condition_id}/{scenario}/seed_{seed}/run"
```

Two more per-condition run-control fields:

- `conditions.<id>.sub_experiment`: logical run group label (for example
  `bill_bias`, `bradley_bias`) that `--only-sub-experiment` selects on.
- `conditions.<id>.config_path`: per-condition scenario config root override.

For the full field list, see [Study Schema](study_schema.md#studyyaml).

## Execution modes

Most studies should use the default runner path and vary behavior with Hydra
overrides in `study.run_defaults.overrides` or
`hypotheses.<id>.conditions.<condition>.overrides`. Reach for an explicit
execution block only where the default runtime is not the thing you want to
execute:

| Want | Set |
|---|---|
| Replace the simulation command entirely | `hypotheses.<id>.conditions.<c>.execution.command` |
| Reuse runs that already exist | `execution.mode: reuse_existing` plus `reuse.runs` |
| Add evaluation / post-processing | entries under `evaluations` with a `preset` or explicit `command`, plus `static_args` |
| Dispatch to a cluster | `submitit` or `slurm-array` (below) |

An exact command overrides execution for that condition:

```yaml
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

## Evaluator presets

Light summaries:

- `builtin.activity_summary`
- `builtin.probe_summary`

Detailed summaries:

- `builtin.action_metrics_detailed`
- `builtin.probe_metrics_detailed`
- `builtin.probe_binary_detailed`
- `builtin.probe_numeric_detailed`
- `builtin.probe_choice_detailed`
- `builtin.probe_freetext_detailed`

Study-local script:

- `builtin.study_eval` runs the study directory's own `eval.py` (contract:
  [Study Schema](study_schema.md#the-evalpy-contract))

Detailed probe evaluators also generate probe-type-specific PNG plots in
`*_plots/` directories next to each evaluator JSON output, and use
`effective_config.yaml` to map probe labels to configured probe types when
available.

Extension hook for custom plotting/post-processing — add `--postprocessor
module:function` args via evaluator `static_args`, where the function signature
is `(records_by_type, out_dir, context) -> dict | list | None`:

```yaml
evaluations:
  - id: probe_metrics
    preset: builtin.probe_metrics_detailed
    static_args:
      - --postprocessor
      - silisocs.evaluations.postprocessors:episode_probe_volume
```

## Output locations

Simulation outputs are grouped by hypothesis/condition/scenario/seed:

```text
experiments/studies/{study_id}/runs/{hypothesis_id}/{condition_id}/{scenario}/seed_{seed}/run
```

Evaluator outputs mirror that hierarchy:

```text
experiments/studies/{study_id}/generated/eval/{hypothesis_id}/{condition_id}/{scenario}/seed_{seed}/{eval_id}/...
```

Everything the runner generates lands under
`experiments/studies/{study_id}/generated/`. For the full tree and the format of
each file (`plan.json`, `repro_lock.json`, `study_index.json`,
`study_enriched.yaml`, `organized/`), see
[Study Schema → Directory Layout](study_schema.md#directory-layout).

## Iterative workflow: h1, analyze, h2

The [Study Guide](study_guide.md#step-6-record-findings-and-add-follow-up-hypotheses)
covers what to write in `study.yaml`; the runner side is three commands:

```sh
# 1. Execute the initial hypothesis
uv run silisocs-study --study experiments/studies/election_opinion_program_v1 run \
  --only-hypothesis h1_initial_news_bias_shift

# 2. Record the finding against the evidence lock
uv run silisocs-study --study experiments/studies/election_opinion_program_v1 summary-append \
  --author researcher --hypothesis h1_initial_news_bias_shift \
  --note "Bias direction changed vote and favorability trajectories" \
  --evidence experiments/studies/election_opinion_program_v1/generated/repro_lock.json

# 3. Run the follow-up hypothesis only
uv run silisocs-study --study experiments/studies/election_opinion_program_v1 run \
  --only-hypothesis h2_initial_persona_prior_carryover
```

Sample study file: `experiments/studies/election_opinion_program_v1/study.yaml`.

## Cluster dispatch

Local orchestration does **not** require Slurm/HPC — plain `run` works
anywhere. For clusters, keep site-specific account, partition, module, cache, and
model startup choices outside the repository. The public templates only wire
SiliSocS study/runner commands into Slurm; they do not launch any specific model
server. If you use a local OpenAI-compatible server, configure
`sim.llm.provider` and `sim.llm.api_base` in your study overrides or scenario
config.

### Submitit study submission

Install the optional HPC dependencies:

```sh
uv sync --extra hpc --group dev
```

Then submit study run groups through the runner. `--array-mode` chooses the
array granularity — how many of the expanded runs share one HPC task. The
default is `case`: **one task per `(hypothesis, condition)` pair**, with every
scenario and seed of that pair executed sequentially inside it. The other modes
are listed under [Array modes](#array-modes) below.

```sh
uv run silisocs-study \
  --study experiments/studies/election_opinion_program_v1 \
  submitit \
  --array-mode case \
  --partition <partition> \
  --account <account> \
  --gpus-per-node 0 \
  --only-hypothesis h1_initial_news_bias_shift
```

By default, submitted jobs assume any LLM endpoint already exists. If your
cluster requires job-local setup, pass explicit hooks:

```sh
uv run silisocs-study \
  --study experiments/studies/election_opinion_program_v1 \
  submitit \
  --array-mode seed \
  --setup-command 'module load cuda && source .venv/bin/activate' \
  --server-command './scripts/start-my-llm-server.sh' \
  --server-ready-url 'http://127.0.0.1:8000/v1/models'
```

SiliSocS treats those hooks as user-owned shell commands; it does not ship
model-specific vLLM or cluster defaults.

### Generic Slurm templates

Use `slurm-array` when you want to keep using direct `sbatch` scripts. It
computes array size from filtered study runs and prints/submits the command:

```sh
uv run silisocs-study \
  --study experiments/studies/election_opinion_program_v1 \
  slurm-array \
  --base-script slurm_scripts/study-array-template.sh \
  --array-mode case \
  --only-hypothesis h1_initial_news_bias_shift \
  --submit
```

For one direct runner job, copy or submit `slurm_scripts/runner-template.sh`.
Both templates support the same optional hook environment variables:

- `SILISOCS_HPC_SETUP_COMMAND`
- `SILISOCS_HPC_SERVER_COMMAND`
- `SILISOCS_HPC_SERVER_READY_URL`
- `SILISOCS_HPC_SERVER_TIMEOUT_SECONDS`

`slurm-array` accepts the same study filters as local execution, plus
`--runner-python` and the same hook options (`--setup-command`,
`--server-command`, `--server-ready-url`, `--server-timeout-seconds`), which it
exports to the generic template.

### Array modes

`--array-mode` (accepted by both `submitit` and `slurm-array`) sets how the
expanded run rows are grouped into HPC array tasks. A **case** is one
`(hypothesis, condition)` pair — the unit a study compares against its siblings:

- `case` (default): one task per `(hypothesis, condition)` pair; every scenario
  and seed of that pair runs sequentially inside the task.
- `seed`: one task per `(hypothesis, condition, seed)`.
- `hypothesis`: one task per hypothesis.
- `run`: one task per expanded run row (`hypothesis × condition × scenario × seed`).
