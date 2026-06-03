# Experiment Studies

This guide describes how to run multi-condition studies with `experiments/run_study.py`.

Use this when you need:
- hypothesis trees
- seed replication
- condition-specific Hydra overrides
- optional exact run commands
- multiple evaluators per run

## Quick Start

```sh
uv run python -m experiments.run_study --study experiments/studies/study_template_v1 plan
uv run python -m experiments.run_study --study experiments/studies/study_template_v1 generate-bash
uv run python -m experiments.run_study --study experiments/studies/study_template_v1 run
uv run python -m experiments.run_study --study experiments/studies/study_template_v1 run --only-hypothesis h2_followup_from_h1
uv run python -m experiments.run_study --study experiments/studies/study_template_v1 run --only-sub-experiment bill_bias
uv run python -m experiments.run_study --study experiments/studies/study_template_v1 summary-append --author analyst --hypothesis h1_timeline_mechanism --note "Observed higher interaction counts in recsys arms" --evidence experiments/studies/recsys_behavior_sweep/generated/repro_lock.json
```

Compatibility note:
- Use the canonical module entrypoint: `uv run python -m experiments.run_study ...`.

## Minimal Study File

```yaml
schema_version: 1

study:
  name: recsys_behavior_sweep
  study_id: recsys_behavior_sweep
  question: "How do timeline settings shift engagement?"
  study_summary_path: experiments/studies/recsys_behavior_sweep/SUMMARY.md
  summary_log_path: experiments/studies/recsys_behavior_sweep/generated/summary_log.jsonl
  scenarios: [election_recsys_engagement]
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
    statement: "Recommendation-heavy timelines increase interactive actions."
    conditions:
      chronological:
        sub_experiment: bill_bias
        overrides:
          env.gm.components.observe.params.timeline_mode: follower_chronological
      recsys:
        sub_experiment: bill_bias
        overrides:
          env.gm.components.observe.params.timeline_mode: pure_recsys
```

## Exact Command Mode

For a condition, you can fully override execution command:

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
    - scenario={scenario}
    - seed={seed}
```

Supported placeholders:
- `{run_id}`
- `{study_name}`
- `{study_id}`
- `{hypothesis_id}`
- `{condition_id}`
- `{scenario}`
- `{seed}`

The same placeholders also work in:
- `study.run_defaults.run_name_template`
- `study.run_defaults.output_root_override`
- `conditions.<id>.run_name_template`
- `conditions.<id>.output_root_override`

Fine-grained run control fields:
- `conditions.<id>.sub_experiment`: logical run group label (for example `bill_bias`, `bradley_bias`).
- `conditions.<id>.config_path`: optional per-condition scenario config root override.

CLI selection knobs:
- `--only-hypothesis`
- `--only-condition`
- `--only-sub-experiment`
- `--only-seed`
- `--only-run-id`

## Where To Plug In Custom Commands

Most studies should use the default runner path and vary behavior with Hydra
overrides in `study.run_defaults.overrides` or
`hypotheses.<id>.conditions.<condition>.overrides`.

Use explicit commands only where the default runtime is not the thing you want
to execute:

- Simulation command replacement: set
  `hypotheses.<id>.conditions.<condition>.execution.command`.
- Existing run reuse: set `execution.mode: reuse_existing` and list prior runs
  under `reuse.runs`.
- Evaluation and post-processing: add entries under `evaluations` with a
  `preset` or explicit `command`, plus `static_args` when needed.
- Local/HPC setup: use `submitit` or `slurm-array` with `--setup-command`,
  `--server-command`, and `--server-ready-url`, or export the matching
  `SILISOCS_HPC_*` environment variables for the generic Slurm templates.

## Default Evaluators

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

Detailed probe evaluators now also generate probe-type-specific PNG plots in
`*_plots/` directories next to each evaluator JSON output.

Extension hook mechanism (for custom plotting/post-processing):
- Add one or more `--postprocessor` args via evaluator `static_args`.
- Format: `module:function`.
- Function signature: `(records_by_type, out_dir, context) -> dict | list | None`.

Example:

```yaml
evaluations:
  - id: probe_metrics
    preset: builtin.probe_metrics_detailed
    static_args:
      - --postprocessor
      - silisocs.evaluations.postprocessors:episode_probe_volume
```

Detailed probe evaluators use `effective_config.yaml` to map probe labels to configured probe types when available.

## Outputs

Study artifacts are written under the study directory:

```text
experiments/studies/{study_id}/generated/
  plan.json
  run_study.sh
  repro_lock.jsonl
  repro_lock.json
  study_index.json
  study_enriched.yaml
  logs/
  eval/
```

Simulation outputs are grouped by hypothesis/condition/scenario/seed:

```text
experiments/studies/{study_id}/runs/{hypothesis_id}/{condition_id}/{scenario}/seed_{seed}/run
```

Evaluator outputs mirror that hierarchy:

```text
experiments/studies/{study_id}/generated/eval/{hypothesis_id}/{condition_id}/{scenario}/seed_{seed}/{eval_id}/...
```

When you run a study, the workflow is:

1. validate and expand the study YAML into concrete runs
2. execute fresh runs or reuse existing ones
3. run configured evaluators for each record
4. write reproducibility artifacts (`repro_lock.jsonl`, `repro_lock.json`, `study_index.json`, `study_enriched.yaml`)
5. rebuild a notebook-friendly organized tree under `generated/organized/`

The organized tree looks like this:

```text
experiments/studies/{study_id}/generated/organized/
  study_summary.yaml
  summary.json
  {hypothesis_id}/
    hypothesis.yaml
    runs.json
    {condition_id}/{scenario}/seed_{seed}/
      config.yaml
      run -> <symlink to the run directory when available>
      eval.json -> <symlink to the first evaluator output>
      eval/{eval_id}/...
```

`run` builds both the raw and organized outputs. `organize` can be called later
to rebuild just the organized view from `repro_lock.json`.

## Iterative Workflow (h1 -> analyze -> h2)

1. Execute initial hypotheses:

```sh
uv run python -m experiments.run_study --study experiments/studies/election_opinion_program_v1 run --only-hypothesis h1_initial_news_bias_shift
```

2. Review evidence and append summary:

```sh
uv run python -m experiments.run_study --study experiments/studies/election_opinion_program_v1 summary-append --author researcher --hypothesis h1_initial_news_bias_shift --note "Bias direction changed vote and favorability trajectories" --evidence experiments/studies/election_opinion_program_v1/generated/repro_lock.json
```

3. Run follow-up hypothesis only:

```sh
uv run python -m experiments.run_study --study experiments/studies/election_opinion_program_v1 run --only-hypothesis h2_initial_persona_prior_carryover
```

Sample study file:
- `experiments/studies/election_opinion_program_v1/study.yaml`

## Public HPC Usage

Local orchestration does **not** require Slurm/HPC:

```sh
uv run python -m experiments.run_study --study experiments/studies/election_opinion_program_v1 run --only-hypothesis h1_initial_news_bias_shift
```

For clusters, keep site-specific account, partition, module, cache, and model
startup choices outside the repository. The public templates only wire Silisocs
study/runner commands into Slurm; they do not launch any specific model server.
If you use a local OpenAI-compatible server, configure `sim.llm.provider` and
`sim.llm.api_base` in your study overrides or scenario config.

### Submitit study submission

Install the optional HPC dependencies:

```sh
uv sync --extra hpc --group dev
```

Then submit study run groups through the study runner:

```sh
uv run python -m experiments.run_study \
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
uv run python -m experiments.run_study \
  --study experiments/studies/election_opinion_program_v1 \
  submitit \
  --array-mode seed \
  --setup-command 'module load cuda && source .venv/bin/activate' \
  --server-command './scripts/start-my-llm-server.sh' \
  --server-ready-url 'http://127.0.0.1:8000/v1/models'
```

Silisocs treats those hooks as user-owned shell commands; it does not ship
model-specific vLLM or cluster defaults.

### Generic Slurm templates

Use `slurm-array` when you want to keep using direct `sbatch` scripts. It
computes array size from filtered study runs and prints/submits the command:

```sh
uv run python -m experiments.run_study \
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

The `slurm-array` command preserves the same study filters as local execution:
`--only-hypothesis`, `--only-condition`, `--only-sub-experiment`,
`--only-seed`, and `--only-run-id`. It also accepts `--runner-python` and the
same hook options (`--setup-command`, `--server-command`,
`--server-ready-url`, `--server-timeout-seconds`) and exports them to the
generic template.

Array modes:
- `case` (default): one task per case, all case seeds executed inside that task.
- `seed`: one task per seed of a case.
- `hypothesis`: one task per hypothesis.
- `run`: one task per expanded run row.

For full schema details, see [Study Schema Reference](study_schema.md).
