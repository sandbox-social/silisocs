# Experiment Studies

This guide describes how to run multi-condition studies with `run_study.py`.

Use this when you need:
- hypothesis trees
- seed replication
- condition-specific Hydra overrides
- optional exact run commands
- multiple evaluators per run

## Quick Start

```sh
uv run python run_study.py --study experiments/study_template_v1.yaml plan
uv run python run_study.py --study experiments/study_template_v1.yaml generate-bash
uv run python run_study.py --study experiments/study_template_v1.yaml run
```

## Minimal Study File

```yaml
schema_version: 1

study:
  name: recsys_behavior_sweep
  question: "How do timeline settings shift engagement?"
  scenarios: [election_recsys_engagement]
  run_defaults:
    config_path: scenarios/election_recsys_engagement/conf
    seed_start: 11
    seed_repeats: 3
    overrides:
      sim.num_agents: 50
      sim.num_steps: 10

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
        overrides:
          sim.timeline_mode: follower_chronological
      recsys:
        overrides:
          sim.timeline_mode: pure_recsys
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
    - mastodon_sim.runtime.runner
    - --config-path
    - scenarios/election_recsys_engagement/conf
    - scenario={scenario}
    - sim.seed={seed}
```

Supported placeholders:
- `{run_id}`
- `{study_name}`
- `{hypothesis_id}`
- `{condition_id}`
- `{scenario}`
- `{seed}`

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

Detailed probe evaluators use `effective_config.yaml` to map probe labels to configured probe types when available.

## Outputs

Study artifacts are written under:

```text
experiments/{study_name}/generated/
  plan.json
  run_study.sh
  repro_lock.jsonl
  repro_lock.json
  study_enriched.yaml
  logs/
  eval/
```

For full schema details, see [EXPERIMENTS.md](../EXPERIMENTS.md).
