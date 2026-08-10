# Designing and Running a Study

A **study** is a research question asked on top of one or more scenarios. It defines:
- the **hypotheses** you want to test
- the **conditions** (what you vary between runs)
- the **scenarios** you run them on
- how many **replicate seeds** to use

Studies live in `experiments/studies/<study_name>/` and are version-controlled.
The scenario is the stage; the study is the experiment.

This page is the walkthrough. Two companion pages hold the details it links to:

| Page | Answers |
|---|---|
| **This page** | What a study is and how to design one, step by step |
| [Study Runner Reference](experiments.md) | Every `silisocs-study` command, filter, execution mode, evaluator preset, and cluster dispatch option |
| [Study Schema](study_schema.md) | The exact shape of `study.yaml` and every generated file |

**Shortcut:** If you are using a repo-aware coding agent such as Codex, Claude
Code, Cursor, or another agent that can read `AGENTS.md`, ask it to follow
`agent_docs/skills/new-study.md` for an interactive `/new-study`-style workflow.
The workflow ends by calling `silisocs new-study --from-spec-json '<spec>'`,
which writes the validated study files — you can also call it directly with a
hand-written spec (`silisocs new-study --help`).

---

## Concepts

**Scenario**: A shared social world (agents, backend, event). Reusable across studies.

**Hypothesis**: A falsifiable claim about what will happen if you vary something.
Example: *"Larger LLMs produce more stylistically diverse posts."*

**Condition**: One value of the independent variable. A hypothesis has 2+ conditions.
Example: `sim.llm.name=gpt-4o-mini` and `sim.llm.name=gpt-4o`.

**Run**: One simulation of one (condition x scenario x seed) combination.

**Evaluation**: A script that reads a completed run and produces metrics (`eval.json`).

---

## Step 1: Create the study directory

```
experiments/studies/my_study/
  study.yaml      ← you write this
  eval.py         ← you write this (or use built-in presets)
  notebook.ipynb  ← you create this after runs complete
```

---

## Step 2: Write `study.yaml`

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

The `overrides` values become Hydra CLI override tokens on the run command the
runner builds — `<python> -m silisocs.runtime.runner --config-path <config_path>
seed=... run_name=... ++output_dir=... agents=thin` — where `<python>` is the
interpreter running `silisocs-study` (or `RUN_STUDY_PYTHON`). Nothing hard-codes
`uv run`, so a plain `pip install silisocs` works.

### Overrides of slot `params` need `++`

Hydra struct-validates CLI overrides against the base config **before** a
scenario's flat `sim.yaml` / `env.yaml` is merged in, and every
[slot](configuration.md#slots) — turn/step/loop policies, GM components, memory,
backends, checkpoint save/restore — defaults to an **empty** `params` map there.
So overriding a key inside one of those blocks fails unless you prefix it with
`++` (add-or-override):

```sh
# FAILS: Could not override 'sim.engine.turn_policy.params.max_actions'.
#        Key 'max_actions' is not in struct
uv run silisocs --config-path scenarios/misinformation/conf \
  'sim.engine.turn_policy.params.max_actions=3'

# WORKS
uv run silisocs --config-path scenarios/misinformation/conf \
  '++sim.engine.turn_policy.params.max_actions=3'
```

In `study.yaml` the override keys are YAML mapping keys, so quote them to keep
the `++`:

```yaml
conditions:
  many_actions:
    overrides:
      "++sim.engine.turn_policy.params.max_actions": 3
```

Keys that already exist in the base config (`num_steps`, `sim.llm.name`, …) need
no prefix. `silisocs-study ... plan` composes every condition and reports this
error before any run starts — see
[Plan-time config check](experiments.md#plan-time-config-check).

Every field, including per-condition `execution` / `reuse` blocks and the
`{scenario}`-style path placeholders, is listed in
[Study Schema → study.yaml](study_schema.md#studyyaml).

---

## Step 3: Write `eval.py` (or use built-in presets)

`eval.py` reads a completed run directory and writes `eval.json` with your metrics.

**Minimal interface:**
```bash
uv run python experiments/studies/my_study/eval.py \
    --run-dir experiments/studies/my_study/runs/h1_recsys/recsys=on/neighborhood_forum/seed_42/run \
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

**Pick at least one preset that produces comparable metrics.** Only the flat
numeric `aggregated` block an evaluator writes reaches the cross-condition
comparison surface (`metrics_by_condition` and `metrics_stats_by_condition` in
`generated/organized/summary.json`, which the notebooks and Studio's comparison
panel read):

| Preset | Fills `metrics_by_condition`? |
|---|---|
| `builtin.action_metrics_detailed` | yes |
| `builtin.probe_metrics_detailed` | yes |
| `builtin.probe_binary_detailed` / `_numeric_` / `_choice_` / `_freetext_detailed` | yes |
| `builtin.study_eval` (your `eval.py`) | yes, if it emits `aggregated` |
| `builtin.activity_summary` | **no** |
| `builtin.probe_summary` | **no** |

`builtin.activity_summary` and `builtin.probe_summary` are lightweight per-run
coverage summaries (label counts, unique users, episodes seen, probe response
counts). They emit no `aggregated` block, so a study configured with only those
two finishes successfully and writes per-run artifacts while its
`metrics_by_condition` comes out **empty** — nothing to compare conditions with.
Treat them as sanity checks alongside a `*_detailed` preset, not as the
measurement.

If you need custom metrics (e.g. lexical diversity, inter-agent distinctiveness),
write `eval.py`; its CLI, input files, and required output format are the
[eval.py contract](study_schema.md#the-evalpy-contract). The full preset list is
in the [Study Runner Reference](experiments.md#evaluator-presets).

---

## Step 4: Run the study

### Validate the grid offline first

Before paying for a single LLM call, smoke the whole grid with the scripted
provider by adding it to `run_defaults.overrides`:

```yaml
study:
  run_defaults:
    overrides:
      sim.llm.provider: scripted
      num_steps: 2
```

Then `plan` (which composes every condition config and exits 1 if any fails) and
a full `run` exercise composition, agent construction, the engine loop, every
backend action path, the evaluators, and the organized tree — in seconds, for
free. Delete the two lines once it is green.

The caveat: **scripted validates plumbing, not content.** Probe answers come back
`null`, and `open_ended` agents repeat one canned action, so any metric that
depends on what agents actually said will be degenerate. A green scripted pass
means "the grid runs end to end", not "the results are meaningful".

```bash
# Plan: preview what will be run, compose every condition config, execute nothing
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

Three runner behaviors are worth knowing before you launch a large grid:

- **Resume is automatic.** A completed run leaves a `RUN_COMPLETE.json` marker;
  re-running the same `run` command executes only the missing or failed runs.
  `--force` ignores the markers.
- **A preflight prompts above 50 runs.** The runner prints the run count and
  estimated agent-steps first; pass `--yes` in non-interactive sessions.
- **Checkpoints are on.** Every run gets `sim.checkpoint.every_n_steps=1` so
  evaluators can read the final checkpoint.

All three, plus every other flag and cluster dispatch, are in the
[Study Runner Reference](experiments.md#runner-behavior).

---

## Step 5: Analyse results

After runs complete, open or create `experiments/studies/my_study/notebook.ipynb`.
Studies in this repo follow a fixed 9-section notebook narrative (setup →
overview → metric definitions → headline comparison → full profile → scenario
consistency → per-agent distributions → behavioral breakdown → takeaways),
specified section by section in
[Study Schema → Notebook Structure](study_schema.md#notebook-structure).

Cross-replicate statistics (`n`, `mean`, `stdev`, and a t-distribution 95%
confidence interval as `ci95_low`/`ci95_high`) are generated automatically:
per condition in `generated/organized/summary.json` under
`metrics_stats_by_condition`, and per run in each hypothesis `runs.json` under
`aggregated_stats`.

Studio can also read the organized tree directly — see
[Studio Analysis Panels](analysis_panels.md).

---

## Step 6: Record findings and add follow-up hypotheses

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
      overrides: {sim.llm.name: gpt-4o-mini}
    gpt4o:
      overrides: {sim.llm.name: gpt-4o}
```

Then run only the new hypothesis and record the finding against the evidence
lock — the command sequence is in the
[Study Runner Reference](experiments.md#iterative-workflow-h1-analyze-h2).

---

## Reusing runs across hypotheses

If a condition from an earlier hypothesis serves as the control for a later one,
reference the same run paths in both condition entries. The organizer handles
duplicate paths: the run is not re-executed, just re-linked. The
`execution.mode: reuse_existing` / `reuse.runs` shape is in
[Study Schema](study_schema.md#reusing-a-baseline-condition-across-hypotheses).

---

## Where to look next

- **Runner reference:** [Study Runner Reference](experiments.md): every command,
  filter, execution mode, evaluator preset, and cluster dispatch option
- **Full `study.yaml` schema:** [Study Schema](study_schema.md): all fields, file
  formats, `eval.json` spec, notebook conventions
- **Scenario design:** [Scenario Guide](scenario_guide.md): how to build the
  scenario you study
- **Existing studies:** `experiments/studies/style_diversity/`: a working example
- **CLI help:** `uv run silisocs-study --help`
