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
uv run silisocs-study --study experiments/studies/study_template_v1 generate-bash \
  --output /tmp/run_study.sh
uv run silisocs-study --study experiments/studies/study_template_v1 run
uv run silisocs-study --study experiments/studies/study_template_v1 organize
uv run silisocs-study --study experiments/studies/misinformation_cta_demo summary-append \
  --author analyst --hypothesis h1_cta_framing \
  --note "Reactive framing produced a higher amplification share" \
  --evidence experiments/studies/misinformation_cta_demo/generated/repro_lock.json
```

| Command | Does |
|---|---|
| `plan` | Expand the study into concrete runs, print the plan (plus the preflight summary), and compose every unique condition config. Executes nothing, but **exits 1 if any condition fails to compose** — see [Plan-time config check](#plan-time-config-check) |
| `generate-bash` | Write a bash script of the plan to the **required** `--output` path, so the same runs can be executed outside the runner (`run` itself also records its plan as `generated/run_study.sh`) |
| `run` | Execute runs, run evaluators, write reproducibility artifacts, and rebuild the organized tree. **Exits 1 if any run OR any evaluator failed** — see [Exit status](#exit-status-and-evaluator-reporting) |
| `organize` | Rebuild only `generated/organized/` from an existing `repro_lock.json` (idempotent; safe after editing `study.yaml`) |
| `summary-append` | Append a dated finding to the study's summary log and `SUMMARY.md` |
| `submitit` / `slurm-array` | Dispatch the same filtered plan to a cluster (see [Cluster dispatch](#cluster-dispatch)) |

`--study` and `--repo-root` are **global** options and go before the subcommand.
`--repo-root` (default `.`) is the directory every run is launched from and the
root the study's workspace is derived under — see
[Where a study's files land](#where-a-studys-files-land).

`run` takes three execution knobs beyond the selection filters:

| Flag | Default | Does |
|---|---|---|
| `--dry-run` | off | Write `generated/plan.json` and `generated/run_study.sh`, print the preflight, and stop before launching anything |
| `--max-concurrent N` | `1` | Number of runs executed in parallel |
| `--timeout-seconds N` | `0` (disabled) | Per-subprocess timeout, applied to each run **and** each evaluator; a run that exceeds it is recorded with `status: timeout` |

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

# --only-sub-experiment matches conditions.<id>.sub_experiment, so it only
# selects anything in a study that declares those labels:
uv run silisocs-study --study experiments/studies/election_opinion_program_v1 run \
  --only-sub-experiment bill_bias
```

A filter that matches nothing is not an error — the runner plans zero runs and
exits 0. Check the printed `Total expanded runs` before assuming a filter did
what you meant.

## Runner behavior

### Plan-time config check

`plan` does more than expand the grid: it composes every unique
`(config_path, override set)` combination through Hydra — read-only, no run, no
writes — and prints

```text
Condition config check: 5/5 unique condition config(s) compose
```

Replicate seeds of one condition compose identically, so each distinct
combination is checked once; conditions with a full `execution.command` override
or `execution.mode: reuse_existing` are skipped (the runner does not compose
those). If any combination fails, `plan` prints a per-condition block on stderr
naming the hypothesis, condition, scenario, `config_path`, the exact override
tokens, and the Hydra error — then **exits 1**. This is the failure that
otherwise kills 100% of a study's runs at startup with nothing in the plan to
hint at it.

Pass `--skip-config-check` to expand the plan without composing anything (the
runner prints `Condition config check: skipped (--skip-config-check)` and exits
0).

The most common failure this catches is a missing `++` on a slot `params.*`
override — see [Overriding slot `params` needs `++`](#slot-params-plus-plus)
below.

### Overriding slot `params` needs `++` {#slot-params-plus-plus}

Every pluggable [slot](configuration.md#slots) defaults to an **empty** `params`
map in the base config, and Hydra struct-validates CLI overrides against the
base config *before* a scenario's flat `sim.yaml` / `env.yaml` is merged in. So a
bare override of a key inside such a `params` block is rejected even when the
scenario itself sets it:

```sh
# FAILS
uv run silisocs --config-path scenarios/misinformation/conf \
  'sim.engine.turn_policy.params.max_actions=3'
```

```text
Could not override 'sim.engine.turn_policy.params.max_actions'.
To append to your config use +sim.engine.turn_policy.params.max_actions=3
Key 'max_actions' is not in struct
    full_key: sim.engine.turn_policy.params.max_actions
    object_type=dict
```

Prefix the key with `++` (add-or-override) and it works:

```sh
# WORKS
uv run silisocs --config-path scenarios/misinformation/conf \
  '++sim.engine.turn_policy.params.max_actions=3'
```

The same applies inside `study.yaml`, where the override keys are YAML mapping
keys — quote them so the `++` survives:

```yaml
overrides:
  "++sim.engine.turn_policy.params.max_actions": 3
```

This affects every slot `params` block the base config leaves empty: turn/step/
loop policy params, GM component params (`resolve`, `observe`, `next_acting`,
`update`, …), memory params, backend params, checkpoint save/restore params.
Keys that already exist in the base config (`num_steps`, `sim.llm.name`,
`env.gm.components.observe.params.timeline_mode` in a scenario that declares it,
…) need no prefix. When in doubt, run `plan` — the config check reports it
before you spend anything.

### Exit status and evaluator reporting

At the end of `run` the runner prints:

```text
Run complete
Success/reused: 12
Failed/timeout: 0
Evaluators run: 22/24 succeeded
```

and, when any evaluator failed, a warning on **stderr**:

```text
⚠ Evaluators failed: 2/24 — see <study workspace>/generated/eval (per-evaluator .log next to each output)
```

`run` returns **1 if any run failed or timed out, or if any evaluator failed**,
and 0 otherwise (2 when the preflight confirmation is declined). A study whose
evaluators all crash therefore no longer exits 0 with an empty `summary.json`.

Only evaluators launched by *this* invocation are counted: evaluator outputs
re-linked from an earlier invocation (`reused`) and `enabled: false` evaluators
are neither attempts nor failures, and the `Evaluators run:` line is omitted when
nothing was attempted. Each evaluator writes a `<output>.log` next to its output
JSON; that log holds the traceback.

### The interpreter runs and evaluators share

Simulation runs are launched as `<python> -m silisocs.runtime.runner ...` and
every built-in evaluator preset as `<python> -m <evaluator module> ...` (or
`<python> <study>/eval.py ...` for `builtin.study_eval`), where `<python>` is the
value of the `RUN_STUDY_PYTHON` environment variable when set, else
`sys.executable` — the interpreter running `silisocs-study` itself. Runs and
evaluators deliberately share this resolution: an evaluator launched under a
different interpreter than its run cannot import what the run imported.

Nothing in the pipeline hard-codes `uv run`, so a plain
`pip install silisocs` in a virtualenv works without further configuration. See
[Environment variables](#environment-variables) and
[Evaluator presets](#evaluator-presets) for the `{python}` token custom evaluator
commands use.

### Where a study's files land

A study's workspace is derived from the repo root and the study's **`study_id`**
(falling back to `study.name`), never from the directory the `study.yaml`
happens to sit in:

```text
<repo-root>/experiments/studies/<study_id>/            # workspace
<repo-root>/experiments/studies/<study_id>/generated/  # everything the runner writes
```

`<repo-root>` is `--repo-root` (default: the current directory). `run`,
`organize`, and `summary-append` all resolve from the same derivation, so a
study cannot end up with two artifact trees.

This is why `experiments/studies/study_template_v1/study.yaml` — whose
`study_id` is `recsys_behavior_sweep` — writes its runs and generated artifacts
under `experiments/studies/recsys_behavior_sweep/`. Keep the directory name and
`study_id` identical unless you have a reason not to.

`summary-append` follows the same tree: `SUMMARY.md` defaults to
`<workspace>/SUMMARY.md` and the JSONL log to
`<workspace>/generated/summary_log.jsonl`. `study.study_summary_path` and
`study.summary_log_path` still redirect them (a relative value resolves against
the repo root), but a value that lands outside the workspace prints a warning on
stderr, because it splits the study's artifacts across two directories.

### Environment variables

| Variable | Read by | Effect |
|---|---|---|
| `RUN_STUDY_PYTHON` | `plan`, `generate-bash`, `run` | Interpreter used to launch **both** simulation runs and evaluators. Defaults to `sys.executable`. |
| `RUN_STUDY_GPU_IDS` | `run` | Comma-separated GPU ids study runs are distributed across (randomized round-robin, one `CUDA_VISIBLE_DEVICES` value per run) when `--max-concurrent > 1`. Falls back to the ambient `CUDA_VISIBLE_DEVICES` when unset; with neither, GPU distribution is disabled and the runner says so. |

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
- `{sub_experiment}`
- `{scenario}`
- `{seed}`

They work in:

- `study.run_defaults.run_name_template`
- `study.run_defaults.output_root_override`
- `study.run_defaults.config_path` and `conditions.<id>.config_path`
- `study.run_defaults.working_directory` and `conditions.<id>.working_directory`
- `conditions.<id>.run_name_template`
- `conditions.<id>.output_root_override`
- `conditions.<id>.execution.command` entries

```yaml
study:
  run_defaults:
    config_path: scenarios/{scenario}/conf
    run_name_template: "{study_id}_{hypothesis_id}_{condition_id}_{scenario}_seed{seed}"
    output_root_override: "experiments/studies/{study_id}/runs/{hypothesis_id}/{condition_id}/{scenario}/seed_{seed}/run"
```

Evaluator `command` and `static_args` entries are templated too, but against a
different context: `{run_id}`, `{study_name}`, `{study_id}`, `{hypothesis_id}`,
`{condition_id}`, `{scenario}`, `{seed}`, plus `{run_dir}` and `{output_path}`.
An evaluator `command` (only — not `static_args`) additionally accepts
`{python}`, the study's resolved interpreter (see
[The interpreter runs and evaluators share](#the-interpreter-runs-and-evaluators-share)).

More per-condition run-control fields:

- `conditions.<id>.sub_experiment`: logical run group label (for example
  `bill_bias`, `bradley_bias`) that `--only-sub-experiment` selects on.
- `conditions.<id>.config_path`: per-condition scenario config root override,
  defaulting to `study.run_defaults.config_path`. Passed to the runner as
  `--config-path`; a relative value resolves against the run's working
  directory, else `--repo-root`.
- `conditions.<id>.working_directory`: the working directory the run subprocess
  is launched from, defaulting to `study.run_defaults.working_directory` and, if
  neither is set, to `--repo-root`. A relative value resolves against
  `--repo-root`. Use it for a study whose scenario or runner expects a different
  cwd; `generate-bash` wraps such runs in `(cd <dir> && ...)`.
- `study.run_defaults.runner_module` (study-wide, no per-condition form):
  the module each run is launched as, `<python> -m <runner_module>`. Defaults to
  `silisocs.runtime.runner`. Set it only when running a custom runner entry
  point; a full `execution.command` is the more explicit alternative.

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

| Preset | Feeds cross-condition comparison? | Reads |
|---|---|---|
| `builtin.activity_summary` | **no** | `action_events.jsonl` |
| `builtin.probe_summary` | **no** | `probe_events.jsonl` |
| `builtin.action_metrics_detailed` | yes | `action_events.jsonl` |
| `builtin.probe_metrics_detailed` | yes | `probe_events.jsonl` |
| `builtin.probe_binary_detailed` | yes | `probe_events.jsonl` |
| `builtin.probe_numeric_detailed` | yes | `probe_events.jsonl` |
| `builtin.probe_choice_detailed` | yes | `probe_events.jsonl` |
| `builtin.probe_freetext_detailed` | yes | `probe_events.jsonl` |
| `builtin.study_eval` | yes, if your `eval.py` emits `aggregated` | whatever your script reads |

### Which presets produce comparable metrics

Only the flat numeric `aggregated` block of an evaluator's JSON reaches the
study-level comparison surface — `metrics_by_condition` and
`metrics_stats_by_condition` in `generated/organized/summary.json`, which is what
the notebooks and the Studio condition-comparison panel read.

The `*_detailed` presets and `builtin.study_eval` (via your own `eval.py`) emit
that block. **`builtin.activity_summary` and `builtin.probe_summary` do not.**
They are lightweight per-run coverage summaries — label counts, unique users,
episodes seen, probe response counts — written per run and nothing more. A study
configured with only those two presets completes successfully and produces
per-run artifacts, but its `metrics_by_condition` and
`metrics_stats_by_condition` come out **empty**, and there is nothing to compare
conditions with.

So: include at least one `*_detailed` preset (or a `builtin.study_eval` script
that emits `aggregated`) in any study whose point is comparing conditions. Use
the light summaries as an extra sanity check, not as the measurement.

Study-local script:

- `builtin.study_eval` runs the study directory's own `eval.py` (contract:
  [Study Schema](study_schema.md#the-evalpy-contract))

Detailed probe evaluators also generate probe-type-specific PNG plots in
`*_plots/` directories next to each evaluator JSON output, and use
`effective_config.yaml` to map probe labels to configured probe types when
available.

### The `{python}` token in evaluator commands

Every built-in preset's command begins with the `{python}` placeholder, which the
runner substitutes with [the study's resolved
interpreter](#the-interpreter-runs-and-evaluators-share). Custom evaluator
commands may use the same token — prefer it to a hard-coded `uv run python`,
which breaks in any non-`uv` environment:

```yaml
evaluations:
  - id: custom_eval
    command: ["{python}", "tools/my_eval.py"]
```

A command token starting with `./` is resolved relative to the study directory
(and must exist, or the study fails to load) — that is how `builtin.study_eval`
finds `./eval.py`.

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
