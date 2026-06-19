# Designing and Running a Study

A **study** is a research question asked on top of one or more scenarios. It defines:
- the **hypotheses** you want to test
- the **conditions** (what you vary between runs)
- the **scenarios** you run them on
- how many **replicate seeds** to use

Studies live in `experiments/studies/<study_name>/` and are version-controlled.
The scenario is the stage; the study is the experiment.

**Shortcut:** If you are using a coding agent (Claude Code, Cursor, etc.), you can
type `/new-study` to be guided through this process interactively.

---

## Concepts

**Scenario** — A shared social world (agents, backend, event). Reusable across studies.

**Hypothesis** — A falsifiable claim about what will happen if you vary something.
Example: *"Larger LLMs produce more stylistically diverse posts."*

**Condition** — One value of the independent variable. A hypothesis has 2+ conditions.
Example: `sim.llm.name=gpt-4o-mini` and `sim.llm.name=gpt-4o`.

**Run** — One simulation of one (condition x scenario x seed) combination.

**Evaluation** — A script that reads a completed run and produces metrics (`eval.json`).

---

## Step 1 — Create the study directory

```
experiments/studies/my_study/
  study.yaml      ← you write this
  eval.py         ← you write this (or use built-in presets)
  notebook.ipynb  ← you create this after runs complete
```

---

## Step 2 — Write `study.yaml`

This is the single source of truth. It describes your research question, hypotheses,
and maps each condition to concrete run configurations.

```yaml
schema_version: 1

study:
  name: my_study
  question: >-
    Does a richer agent persona produce more stylistically distinct posts?
  scenarios:
    - neighborhood_forum
    - hobby_collective
  run_defaults:
    config_path: scenarios/{scenario}/conf
    seed_start: 42
    seed_repeats: 3          # runs seeds 42, 43, 44 for each condition x scenario
    overrides:
      num_steps: 10

hypotheses:
  h1_persona_richness:
    statement: >-
      Agents with detailed backstory context produce more stylistically
      diverse posts than agents with minimal context.
    independent_variable: persona
    prediction: >-
      Rich persona condition will show higher inter-agent distinctiveness
      across both scenarios.
    status: testing
    conditions:
      rich:
        overrides:
          agents: default       # uses scenarios/<name>/conf/agents/default.yaml
      thin:
        overrides:
          agents: thin          # uses scenarios/<name>/conf/agents/thin.yaml
```

**Key fields:**

| Field | What it does |
|---|---|
| `study.scenarios` | List of scenarios to run each condition on |
| `run_defaults.seed_start` + `seed_repeats` | Expands to N consecutive seeds per run |
| `run_defaults.checkpoint_every_n_steps` | Checkpoint cadence injected into every run (default `1`, i.e. a checkpoint every step so evaluators can read the final checkpoint). Set a larger int for sparser checkpoints, or `null`/`0`/`false` to disable the injection. |
| `hypotheses.<id>.conditions.<name>.overrides` | Hydra CLI overrides for this condition |
| `hypotheses.<id>.status` | `testing` → `supported` / `refuted` / `inconclusive` |

The `overrides` values are passed directly to `uv run silisocs` as CLI overrides
(e.g. `agents=thin` → `--config-path ... agents=thin`).

---

## Step 3 — Write `eval.py` (or use built-in presets)

`eval.py` reads a completed run directory and writes `eval.json` with your metrics.

**Minimal interface:**
```bash
uv run python experiments/studies/my_study/eval.py \
    --run-dir outputs/neighborhood_forum_experiment/2026-05-01T10-00-00 \
    --output  path/to/eval.json
```

To use the built-in action and probe metrics without a custom script, add to `study.yaml`:

```yaml
evaluations:
  - id: action_metrics
    preset: builtin.action_metrics_detailed
  - id: probe_metrics
    preset: builtin.probe_metrics_detailed
```

If you need custom metrics (e.g. lexical diversity, inter-agent distinctiveness),
write `eval.py`. See `docs/study_schema.md` for the required output format.

---

## Step 4 — Run the study

```bash
# Plan: preview what will be run without executing
uv run silisocs-study \
    --study experiments/studies/my_study plan

# Run all conditions × scenarios × seeds
uv run silisocs-study \
    --study experiments/studies/my_study run

# Run only one hypothesis
uv run silisocs-study \
    --study experiments/studies/my_study run \
    --only-hypothesis h1_persona_richness
```

The runner writes deterministic run records and organized artifacts. To reuse
existing outputs instead of running a condition again, set
`execution.mode: reuse_existing` and list the prior output paths under
`reuse.runs` in `study.yaml`.

Outputs land in:
```
experiments/studies/my_study/runs/h1_persona_richness/persona=rich/neighborhood_forum/seed_42/run/
```

**Resuming an interrupted study.** Each successful run leaves a
`RUN_COMPLETE.json` marker in its run directory. Re-running the same `run`
command skips runs that already completed (reported as `skipped_complete` and
counted as successes) and only executes the missing or failed ones. The runner
prints `Skipped N already-complete runs (use --force to re-run)`; pass `--force`
to ignore the markers and re-run everything.

**Preflight check.** Before launching, the runner prints how many runs will
execute, their `num_agents`/`num_steps` (when derivable from overrides), and the
estimated total agent-steps. If more than 50 runs would launch, it asks for
confirmation — pass `--yes` to skip the prompt (required in non-interactive
sessions such as CI or batch jobs).

**Checkpoint cadence.** By default every run is launched with
`sim.checkpoint.every_n_steps=1` so evaluators can read the final checkpoint.
Set `run_defaults.checkpoint_every_n_steps` to a larger integer for sparser
checkpoints, or to `null`/`0`/`false` to disable the injection.

---

## Step 5 — Analyse results

After runs complete, open or create `experiments/studies/my_study/notebook.ipynb`.

The standard notebook structure has 9 sections:
1. Title + setup (load `study.yaml` and all `eval.json` files)
2. Study overview (hypothesis table, condition summary)
3. Key metrics explained
4. Headline comparison (grouped bar chart)
5. Full metric profile (radar chart)
6. Scenario consistency (faceted by scenario)
7. Per-agent distributions (strip plots)
8. Behavioral breakdown (action type counts)
9. Takeaways (key findings, limitations, next steps)

Cross-replicate statistics (`n`, `mean`, `stdev`, and a t-distribution 95%
confidence interval as `ci95_low`/`ci95_high`) are generated automatically:
per condition in `generated/organized/summary.json` under
`metrics_stats_by_condition`, and per run in each hypothesis `runs.json` under
`aggregated_stats`.

See `docs/study_schema.md` for the full notebook conventions.

---

## Step 6 — Record findings and add follow-up hypotheses

When a hypothesis is complete, update its `status` and add a `finding`:

```yaml
h1_persona_richness:
  status: supported
  finding: >-
    Rich persona condition showed 2.4× higher inter-agent distinctiveness
    than thin condition across both scenarios.
```

To add a follow-up hypothesis motivated by this finding:

```yaml
h2_model_capacity:
  follows_from: h1_persona_richness
  motivation: >-
    H1 confirmed persona richness drives diversity. H2 asks whether model
    capacity amplifies or dampens this effect.
  statement: ...
  independent_variable: model
  status: testing
  conditions:
    gpt4o-mini:
      overrides: {llm.name: gpt-4o-mini}
    gpt4o:
      overrides: {llm.name: gpt-4o}
```

---

## Reusing runs across hypotheses

If a condition from an earlier hypothesis serves as the control for a later one,
reference the same run paths in both condition entries. The organizer handles
duplicate paths — the run is not re-executed, just re-linked.

---

## Where to look next

- **Full study.yaml schema:** `docs/study_schema.md` — all fields, file formats, eval.json spec
- **Scenario design:** `docs/scenario_guide.md` — how to build the scenario you study
- **Existing studies:** `experiments/studies/style_diversity/` — a working example
- **Run study CLI help:** `uv run silisocs-study --help`
