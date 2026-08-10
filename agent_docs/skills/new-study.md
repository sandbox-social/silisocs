# Study Design Workflow

A study is a **research question asked on top of a scenario**. It defines hypotheses,
conditions (what you vary), evaluators (what you measure), and seed replication.
Studies live in `experiments/studies/<study_id>/study.yaml` and are version-controlled.

Scenarios are shared social scenarios — reusable across studies. Multiple researchers
can ask different questions on the same scenario.

Studies are **living documents**: they grow as hypotheses are tested, findings recorded,
and followup hypotheses added. Design with that lifecycle in mind.

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
description from it. If no README exists, fall back to the relevant semantic
fields in `scenarios/<name>/conf/world/default.yaml`.

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

For each hypothesis, expand into:
- `id`: short snake_case identifier, format `h{N}_{short_name}` (e.g. `h1_timeline_effect`)
- `statement`: the hypothesis as a falsifiable claim (one sentence)
- `independent_variable`: the config key being varied (e.g. `env.gm.components.observe.params.timeline_mode`)
- `prediction`: expected direction or outcome if hypothesis is true
- `status`: always start as `testing`

Show the expanded form and ask for confirmation.

**On hypothesis status lifecycle** — explain to the user:
> After running and analyzing, you'll update `status` to one of:
> `testing → supported | refuted | inconclusive`
> and record a `finding:` field with the key result in plain language.
> This is what motivates followup hypotheses.

---

## Step 4 — Conditions

For each hypothesis, ask:
> "What are the specific conditions you want to compare?"

**Condition naming convention**: use `{iv}={value}` format where possible
(e.g. `timeline=chronological`, `timeline=recsys`). This makes the independent
variable readable from directory paths alone. For content variations (e.g. news
bias), use a descriptive label (e.g. `bill_bias`, `bradley_bias`) and set
`sub_experiment` accordingly.

Help the user translate intent into Hydra override dicts. Reference the scenario's
config structure (read `scenarios/<name>/conf/`) to suggest valid keys.

Common patterns:
- Timeline: `env.gm.components.observe.params.timeline_mode: follower_chronological` vs `pure_recsys`
- Agent counts: `agents.persona_pipeline.classes.<role>.count: N`
- Graph: `env.gm.components.initialize.params.graph.base_followership_probability: 0.8`
- Scale: `num_steps: 20`, `num_agents: 100`

**Slot `params` keys need a `++` prefix.** Hydra struct-validates CLI overrides
against the BASE config before the scenario's flat `sim.yaml`/`env.yaml` is
merged, and every slot (`turn_policy`, `step`, GM components, memory, backend,
checkpoint save/restore) defaults to an EMPTY `params: {}` there. A bare
override of a key inside such a block is rejected even when the scenario sets
it:

```sh
# FAILS: Could not override 'sim.engine.turn_policy.params.max_actions'.
#        Key 'max_actions' is not in struct
sim.engine.turn_policy.params.max_actions=3
# WORKS
++sim.engine.turn_policy.params.max_actions=3
```

In `study.yaml` the override keys are YAML mapping keys, so quote them:

```yaml
overrides:
  "++sim.engine.turn_policy.params.max_actions": 3
```

Keys that already exist in the base config (`num_steps`, `num_agents`,
`sim.llm.name`, `env.gm.components.observe.params.timeline_mode` where the
component declares it) need no prefix. When unsure, write it and let the Step 8
`plan` check tell you — see [docs/configuration.md](../../docs/configuration.md)
for how composition layers.

For each condition also ask:
- Does it need a `sub_experiment` label for grouping/filtering?
- Should any conditions **reuse runs from a previous hypothesis** as a baseline?
  (If yes: note this — the condition's `execution.mode` will be `reuse_existing`
  and it will reference the prior run's output path. Avoids redundant API costs
  and keeps results comparable across hypotheses.)

---

## Step 5 — Evaluators and seed replication

Ask:
> "What do you want to measure? And how many random seeds do you want to replicate over?"

**Evaluators** — suggest defaults and let user add:
- `builtin.action_metrics_detailed` — post/reply/like/repost counts (always recommended)
- `builtin.probe_metrics_detailed` — probe responses over time (if probes are configured)
- `builtin.probe_binary_detailed`, `builtin.probe_numeric_detailed`,
  `builtin.probe_choice_detailed`, `builtin.probe_freetext_detailed` — type-specific

Always include at least one `*_detailed` preset (or a `builtin.study_eval`
script that emits an `aggregated` block). Only `aggregated` reaches
`summary.json`'s `metrics_by_condition` / `metrics_stats_by_condition`, the
cross-condition comparison surface. `builtin.activity_summary` and
`builtin.probe_summary` emit NO `aggregated` block — a study using only those
runs fine but produces empty cross-condition metrics.

Condition-local evaluators are supported — if a particular condition needs extra
measurement, add an `evaluations:` block under that condition with
`evaluation_mode: append`.

**Seeds** — suggest `seed_repeats: 3` starting from `seed_start: 42` as a reasonable
default. More seeds = more robust conclusions but more API cost.

---

## Step 6 — Study metadata and notes

Collect:
- `study_id`: short snake_case identifier (e.g. `echo_chamber_timeline_v1`)
- `study.name`: human-readable title
- `study.question`: the confirmed research question from Step 1
- `num_agents` and `num_steps` shared defaults (can override per condition)

Ask:
> "Any context, constraints, or objectives worth recording in the study file?
> This is your lab notebook — future you will thank present you."

Capture as `study.notes`:
```yaml
notes:
  objective: ""    # what decision or insight this study informs
  context: ""      # prior work, related studies, why now
  constraints: ""  # time/cost/API limits affecting design choices
```

Suggest paths (all three are already the defaults the runner derives from
`study_id`, so they can be omitted entirely — declare them only to be explicit,
and keep them inside `experiments/studies/<study_id>/` or the runner warns that
the study's artifacts are split across two trees):
- `study_summary_path`: `experiments/studies/<study_id>/SUMMARY.md`
- `summary_log_path`: `experiments/studies/<study_id>/generated/summary_log.jsonl`
- `output_root_override`: `experiments/studies/<study_id>/runs/{hypothesis_id}/{condition_id}/{scenario}/seed_{seed}/run`

The workspace is derived from `study_id`, not from the directory name — keep
them identical.

---

## Step 7 — Notebook

Ask:
> "Do you want a starter analysis notebook? I can create a skeleton with the
> standard 9-section structure."

If yes, create `experiments/studies/<study_id>/notebook.ipynb` with these sections:
1. **Title + Setup** — load `study.yaml`, load eval files, set plot defaults
2. **Study Overview** — hypothesis statements, conditions table (agents, steps, posts)
3. **Key Metrics Explained** — plain-language definition + why it matters for each metric
4. **Headline Comparison** — grouped bar chart of key metrics across conditions
5. **Full Metric Profile** — radar chart, all metrics, both conditions overlaid
6. **Scenario Consistency** — faceted figure (one panel per scenario) if multi-scenario
7. **Per-Agent Distributions** — strip plots, each agent as a point, colored by condition
8. **Behavioral Breakdown** — stacked bar of action type counts per condition
9. **Takeaways** — key findings with numbers, limitations, next steps

---

## Step 8 — Write files

Assemble the full spec as JSON (schema below) and write the study file:

```bash
uv run silisocs new-study --from-spec-json '<JSON>'
```

This writes:
```
experiments/studies/<study_id>/
  study.yaml
  SUMMARY.md         # empty template ready for researcher notes
  notebook.ipynb     # if requested in Step 7
```

Then validate the study plan expands correctly:
```bash
uv run silisocs-study --study experiments/studies/<study_id> plan
```

(`--study` and `--repo-root` are top-level args and go BEFORE the subcommand.)

`plan` does two things: it expands the grid, and it composes every unique
`(config_path, override set)` combination through Hydra. Read the last line:

```text
Condition config check: 5/5 unique condition config(s) compose
```

If any condition fails, `plan` prints a per-condition block on stderr with the
Hydra error and **exits 1** — every run of that condition would die at startup.
The usual cause is a missing `++` on a slot `params.*` key (Step 4). Fix and
re-run `plan` until it is clean; do not report a study as ready while `plan`
exits non-zero. (`--skip-config-check` opts out, but only use it if composing is
genuinely impossible here.)

Report the expanded plan and the config-check line to the user.

Optionally, smoke the whole grid for free before any LLM spend by temporarily
adding `sim.llm.provider: scripted` (and a small `num_steps`) to
`run_defaults.overrides` and running `run`. Caveat: scripted validates PLUMBING
only — probe answers come back `null` and `open_ended` agents repeat one canned
action, so no metric is meaningful. Remove the override afterwards.

---

## Iterative workflow (h1 → analyze → h2)

Remind the user of this pattern after writing files:

> **After running H1:**
> 1. Update `status` in `study.yaml` to `supported`, `refuted`, or `inconclusive`
> 2. Record the key result as `finding: "gpt4o produced 3× higher diversity..."` on the hypothesis
> 3. Append a summary note: `uv run silisocs-study --study ... summary-append --author ... --hypothesis h1_... --note "..."`
> 4. Add H2 to `study.yaml` with `follows_from: h1_...` and `motivation:` explaining what the H1 finding raised
> 5. Run H2 — conditions that overlap with H1 can reuse existing runs via `execution.mode: reuse_existing`

---

## StudySpec JSON schema

```json
{
  "study_id": "snake_case_id",
  "name": "Human Readable Title",
  "question": "One sentence research question?",
  "scenarios": ["scenario_name"],
  "notes": {
    "objective": "",
    "context": "",
    "constraints": ""
  },
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
      "id": "h1_short_name",
      "statement": "Falsifiable hypothesis in one sentence.",
      "independent_variable": "env.gm.components.observe.params.timeline_mode",
      "prediction": "Expected direction or outcome.",
      "status": "testing",
      "follows_from": null,
      "motivation": null,
      "conditions": [
        {
          "id": "timeline=chronological",
          "sub_experiment": null,
          "overrides": {
            "env.gm.components.observe.params.timeline_mode": "follower_chronological"
          },
          "execution_mode": "run",
          "reuse_source": null
        }
      ]
    }
  ],
  "generate_notebook": false
}
```
