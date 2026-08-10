# Evaluation Probes

Probes are structured surveys deployed to agents during the simulation. They
measure agent attitudes, preferences, and knowledge over time, providing
quantitative data for analysis.

!!! probe "What a probe captures"
    A probe asks every agent the same question on a schedule (e.g. every episode)
    and records typed responses to `probe_events.jsonl`, turning a running
    society into a longitudinal panel survey.

## Overview

Probes are configured under `eval.probes`. In a scenario that means the flat
`scenarios/<name>/conf/eval.yaml` file, whose top-level keys are merged under
`eval` — so the top-level `probes:` key in the examples below lands at
`eval.probes`. (Do **not** put `probes:` in `world/default.yaml`: that file
carries `# @package _global_`, so the block would land at the config root where
nothing reads it, and the runtime rejects it at build time.) The system supports
multiple probe types, configurable deployment schedules, and concurrent execution
across large agent populations.

---

## Probe Types

### Built-in Types

| Type | Response | Example |
|------|----------|---------|
| `NumericRatingProbe` | Integer on a configurable scale (default 1-10) | "Rate your satisfaction 1-10" |
| `BinaryProbe` | Yes/No | "Would you vote for candidate X?" |
| `ChoiceProbe` | One of N options | "Which candidate do you prefer: A, B, or C?" |
| `FreeTextProbe` | Open-ended string | "What are your concerns about the election?" |

### Probe Configuration

```yaml
probes:
  probes:
    satisfaction:
      probe_name: satisfaction
      probe_type: NumericRatingProbe
      probe_data:
        name: Satisfaction
        question: "Return one number from {lo} to {hi}: how satisfied are you with community discussions?"
        lo: 1
        hi: 10

    turnout_intent:
      probe_name: turnout_intent
      probe_type: BinaryProbe
      probe_data:
        name: VoteIntent
        question: "Will you participate in the upcoming vote? Reply yes or no."

    topic_preference:
      probe_name: topic_preference
      probe_type: ChoiceProbe
      probe_data:
        name: TopicPreference
        question: "Which topic interests you most?"
        choices: [Technology, Politics, Entertainment]
```

---

## Deployment Schedule

Control when and to whom probes are deployed:

```yaml
probes:
  deployment:
    enabled: true
    start_step: 1             # First step to deploy probes (steps are 0-indexed)
    every_n_steps: 5          # Deploy every N steps
    include_agents: []      # Empty = all agents
    exclude_agents:         # Skip specific agents
      - "Storhampton Gazette"
```

### Step indices are 0-indexed

A run's steps are numbered `0, 1, 2, ... num_steps - 1`. A probe is due on step
`s` when `s >= start_step` and `(s - start_step) % every_n_steps == 0`.

!!! warning "The default `start_step: 1` skips step 0"
    Because steps start at 0, the default `start_step: 1` means **step 0 is never
    probed** — you get no baseline measurement before the first round of agent
    behavior. Set `start_step: 0` if you want a pre-simulation baseline. A
    `num_steps: 1` run left on the default runs step 0 only and therefore writes
    no `probe_events.jsonl` at all.

!!! tip "For a terminal measurement, anchor with `at: run_end`"
    `start_step`/`every_n_steps` cannot reliably express "ask this once at the
    end": `num_steps - 1` changes whenever the run length changes, and the last
    step's `pre_step` probe still runs *before* that step's actions. Give the
    probe its own `deployment: {at: run_end}` block instead — a `run_end` probe
    fires exactly once after the loop finishes and ignores the step cadence
    entirely. See [Per-Probe Deployment and Loop Anchors](#per-probe-deployment-and-loop-anchors)
    below.

    ```yaml
    probes:
      probes:
        closing_view:
          probe_name: closing_view
          probe_type: FreeTextProbe
          probe_data:
            name: ClosingView
            question: "What is your final view on the proposal?"
          deployment:
            at: run_end
    ```

### Targeting

- **All agents**: Leave `include_agents` empty
- **Specific agents**: List agent names in `include_agents`
- **Exclude agents**: List names in `exclude_agents` (e.g., news bots)

---

## Custom Probe Types

For scenario-specific probes, create a module and reference it:

```yaml
probes:
  probe_lib_module: my_world.probes
```

```python
# my_world/probes.py
from silisocs.evaluations.probes.types import ProbeBase

class Favorability(ProbeBase):
    # How the built-in evaluators score responses: one of
    # "binary" | "numeric" | "choice" | "freetext" (omit for unscored).
    analysis_kind = "numeric"

    def __init__(self, probe_data=None):
        self.name = "Favorability"
        self._candidate = (probe_data or {}).get("candidate", "Candidate A")

    def form_question_for_agent(self, agent):
        return f"On a scale of 1-10, how favorable is your view of {self._candidate}?"

    def parse_answer(self, raw_response):
        # Extract numeric value from LLM response
        ...
```

Custom types are resolved via `importlib` at runtime. A probe must return a
question string from `form_question_for_agent(agent)` and a parsed string or
`None` from `parse_answer(raw_response)`. Declaring `analysis_kind` is what
makes the built-in evaluators (`builtin.probes_*` presets, probe panels)
bucket the probe's responses — scoring is registry-driven, and the evaluators
import the run's recorded `probe_lib_module` themselves, so a custom probe
scores correctly even though study evaluation runs in a different process
than the simulation. Probe prompts should contain the
measurement question and any answer-format constraint only. Agent identity,
persona, and recent observations should come from the agent runtime itself.
This is optional and most worlds can use the built-in generalist probe types
directly.

---

## Questionnaire Batching

For efficiency, the probe system batches multiple probe questions into a single
LLM call per agent. This reduces API costs significantly when deploying many
probes.

The batched questionnaire prompt presents all questions numbered, and the
response parser extracts individual answers. If parsing fails for any question,
the system falls back to individual LLM calls for those specific questions.

Batching holds even with per-probe schedules: probes due for the same agent on
the same step (and loop anchor) are still sent in one call.

---

## Per-Probe Deployment and Loop Anchors

Each probe entry may carry its own `deployment:` block that overrides the global
`probes.deployment` block per field (unset fields inherit the global value), so a
single run can mix probes on different schedules, target cohorts, sampling caps,
and loop positions. The `at` field chooses the loop anchor — `pre_step` (default),
`post_step`, or `run_end` (a single terminal measurement after the run) — and each
`probe_events.jsonl` row records its `anchor`. See
[Configuration → Probes](configuration.md#probes) for the full schema and rules.

---

## Output

Probe results are saved to `probe_events.jsonl` in the simulation output directory:

```json
{
  "episode": 5,
  "event_type": "probe",
  "source_user": "Alice Smith",
  "label": "turnout_intent",
  "data": {
    "probe_type": "BinaryProbe",
    "raw_response": "I'd say about a 7",
    "probe_return": "yes"
  }
}
```

The election world demonstrates probes in action with named built-in
probes (vote preference, favorability, intent): see the
[Election Walkthrough](tutorials/election.md).

### Default Detailed Probe Evaluators

When running studies with `silisocs-study`, you can use built-in probe evaluator presets:

- `builtin.probe_metrics_detailed` (all probe events)
- `builtin.probe_binary_detailed`
- `builtin.probe_numeric_detailed`
- `builtin.probe_choice_detailed`
- `builtin.probe_freetext_detailed`

Plot outputs are generated by these detailed probe evaluators directly (in sibling
`*_plots/` directories next to each evaluator JSON), rather than through a separate
plot-only evaluator hook.

These evaluators:
- read `action_events.jsonl`
- aggregate metrics per agent and per episode
- aggregate per probe label and per inferred/configured probe type
- use `effective_config.yaml` to map labels to configured probe types when available

For orchestration details and preset usage, see the
[Study Runner Reference](experiments.md#evaluator-presets).

---

## Related

- [Building Agents](building_agents.md): Agent construction and persona pipeline
- [Usage Overview](usage.md#evaluation-probes): Probes in the end-to-end workflow
- [Configuration Reference](configuration.md): Full probes config options
