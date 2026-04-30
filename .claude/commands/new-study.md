# /new-study — Design a new silisocs study

A study is a **research question asked on top of a scenario**. It defines hypotheses,
conditions (what you vary), evaluators (what you measure), and seed replication.
Studies live in `experiments/studies/<study_id>/study.yaml`.

Scenarios are shared social worlds — reusable across studies. Multiple researchers
can ask different questions on the same scenario. If no existing scenario fits your
question, this skill can branch into `/new-scenario` and then return here.

Work conversationally, one section at a time.

---

## Step 1 — Research question

Ask:
> "What is your research question? Describe the social phenomenon you want to study
> and what you're trying to find out."

Use their answer to:
1. Identify the **independent variable** — what they'd vary across conditions.
2. Identify the **dependent variable** — what outcome they'd measure.
3. Draft a crisp one-sentence research question to confirm with them.

---

## Step 2 — Scenario selection

List the available scenarios and their descriptions:
```bash
ls scenarios/
```

For each scenario directory that has a `README.md`, read the first two lines of
description from it. If no README exists, fall back to the `description:` field
in `scenarios/<name>/conf/scenario/default.yaml`.

Ask:
> "Which of these scenarios best fits your question, or do none of them fit?
> I can also build a new scenario if needed."

- If user picks an existing scenario: note its `conf/` path and proceed.
- If no scenario fits: say "Let me help you design one first" and invoke the
  `/new-scenario` flow. Once that completes, return here with the new scenario selected.
- If a question spans multiple scenarios (comparative study): collect all of them.

---

## Step 3 — Hypotheses

Ask:
> "What do you expect to find? State 1–2 hypotheses — what you predict will happen
> when you change [independent variable]."

For each hypothesis, expand into the study schema fields:
- `statement`: the hypothesis as a testable claim
- `independent_variable`: the config key being varied (e.g. `env.timeline_mode`)
- `prediction`: expected direction of effect
- `status`: `testing` (default)

Show the expanded form and ask for confirmation.

---

## Step 4 — Conditions

For each hypothesis, ask:
> "What are the specific conditions you want to compare? For each one, what Hydra
> config overrides does it require?"

Help the user translate their intent into concrete Hydra override diffs. Reference
the scenario's config structure (read `scenarios/<name>/conf/`) to suggest valid keys.

Common patterns to suggest:
- Timeline mode: `env.timeline_mode: follower_chronological` vs `pure_recsys`
- Agent counts: `agents.persona_pipeline.classes.<role>.count: N`
- Network: `env.social_network.base_followership_probability: 0.8`
- Scenario content: `num_steps: 20`, `seed: 42`

For each condition, also ask if it needs a `sub_experiment` label (useful for
grouping conditions that share the same content variation, e.g. `bill_bias` vs `bradley_bias`).

---

## Step 5 — Evaluators and seed replication

Ask:
> "What do you want to measure? And how many random seeds do you want to replicate over?"

**Evaluators** — suggest defaults and let user add:
- `builtin.action_metrics_detailed` — post/reply/like/repost counts (always recommended)
- `builtin.probe_metrics_detailed` — probe responses over time (if probes are configured)
- `builtin.probe_binary_detailed`, `builtin.probe_numeric_detailed`, etc. — type-specific

**Seeds** — suggest `seed_repeats: 3` starting from `seed_start: 42` as a reasonable default.

---

## Step 6 — Study metadata

Collect:
- `study_id`: short snake_case identifier (e.g. `echo_chamber_timeline_v1`)
- `study.name`: human-readable title
- `study.question`: the confirmed research question from Step 1
- `num_agents` and `num_steps` shared defaults (can override per condition)

Suggest paths:
- `study_summary_path`: `experiments/studies/<study_id>/SUMMARY.md`
- `summary_log_path`: `experiments/studies/<study_id>/generated/summary_log.jsonl`
- `output_root_override`: `experiments/studies/<study_id>/runs/{hypothesis_id}/{condition_id}/{scenario}/seed_{seed}/run`

---

## Step 7 — Write files

Assemble the full spec as JSON (schema below) and write the study file:

```bash
uv run silisocs new-study --from-spec-json '<JSON>'
```

This writes:
```
experiments/studies/<study_id>/
  study.yaml
  SUMMARY.md    # empty template ready for researcher notes
```

Then validate the study plan:
```bash
uv run python -m experiments.run_study --study experiments/studies/<study_id> plan
```

Report the expanded plan to the user. Fix any config key errors before finishing.

---

## StudySpec JSON schema

```json
{
  "study_id": "snake_case_id",
  "name": "Human Readable Title",
  "question": "One sentence research question?",
  "scenarios": ["scenario_name"],
  "run_defaults": {
    "config_path": "scenarios/<name>/conf",
    "run_name_template": "{study_id}_{hypothesis_id}_{condition_id}_{scenario}_seed{seed}",
    "output_root_override": "experiments/studies/{study_id}/runs/{hypothesis_id}/{condition_id}/{scenario}/seed_{seed}/run",
    "seed_start": 42,
    "seed_repeats": 3,
    "overrides": {
      "num_agents": 50,
      "num_steps": 10
    }
  },
  "evaluations": [
    {"id": "action_metrics", "preset": "builtin.action_metrics_detailed"},
    {"id": "probe_metrics", "preset": "builtin.probe_metrics_detailed"}
  ],
  "hypotheses": [
    {
      "id": "h1_short_id",
      "statement": "Testable hypothesis statement.",
      "independent_variable": "config_key_being_varied",
      "prediction": "Expected direction or outcome.",
      "status": "testing",
      "conditions": [
        {
          "id": "condition_id",
          "sub_experiment": null,
          "overrides": {
            "env.timeline_mode": "follower_chronological"
          }
        }
      ]
    }
  ]
}
```
