# Evaluation Probes

Probes are structured surveys deployed to agents during the simulation. They
measure agent attitudes, preferences, and knowledge over time — providing
quantitative data for analysis.

## Overview

Probes are configured in the `probes` section of your scenario YAML. The system
supports multiple probe types, configurable deployment schedules, and concurrent
execution across large agent populations.

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
  queries:
    satisfaction:
      probe_name: satisfaction
      query_type: NumericRatingProbe
      query_data:
        name: Satisfaction
        question: "Return one number from {lo} to {hi}: how satisfied are you with community discussions?"
        context: "{agentname} rates current discussion quality."
        lo: 1
        hi: 10

    turnout_intent:
      probe_name: turnout_intent
      query_type: BinaryProbe
      query_data:
        name: VoteIntent
        question: "Will you participate in the upcoming vote? Reply yes or no."
        context: "{agentname} decides whether they intend to vote."

    topic_preference:
      probe_name: topic_preference
      query_type: ChoiceProbe
      query_data:
        name: TopicPreference
        question: "Which topic interests you most?"
        context: "{agentname} picks a single preferred topic."
        choices: [Technology, Politics, Entertainment]
```

---

## Deployment Schedule

Control when and to whom probes are deployed:

```yaml
probes:
  deployment:
    enabled: true
    start_step: 1             # First step to deploy probes
    every_n_steps: 5          # Deploy every N steps
    include_entities: []      # Empty = all agents
    exclude_entities:         # Skip specific agents
      - "Storhampton Gazette"
```

### Targeting

- **All agents**: Leave `include_entities` empty
- **Specific agents**: List agent names in `include_entities`
- **Exclude agents**: List names in `exclude_entities` (e.g., news bots)

---

## Custom Probe Types

For scenario-specific probes, create a module and reference it:

```yaml
probes:
  query_lib_module: my_scenario.probes
```

```python
# my_scenario/probes.py
from mastodon_sim.evaluations.probes.types import ProbeBase

class Favorability(ProbeBase):
    @property
    def question_text(self):
        candidate = self.query_data["interaction_premise_template"]["candidate"]
        return f"On a scale of 1-10, how favorable is your view of {candidate}?"

    def parse_answer(self, raw_response):
        # Extract numeric value from LLM response
        ...
```

Custom types are resolved via `importlib` at runtime. This is optional and most
scenarios can use the built-in generalist probe types directly.

---

## Questionnaire Batching

For efficiency, the probe system batches multiple probe questions into a single
LLM call per agent. This reduces API costs significantly when deploying many
probes.

The batched questionnaire prompt presents all questions numbered, and the
response parser extracts individual answers. If parsing fails for any question,
the system falls back to individual LLM calls for those specific questions.

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
    "query_type": "BinaryProbe",
    "raw_response": "I'd say about a 7",
    "query_return": "yes"
  }
}
```

The election scenario demonstrates probes in action with named built-in
queries (vote preference, favorability, intent) — see the
[Election Walkthrough](tutorials/election.md).

---

## Related

- [Building Agents](building_agents.md) — Agent construction and persona pipeline
- [Usage Overview](usage.md#evaluation-probes) — Probes in the end-to-end workflow
- [Configuration Reference](configuration.md) — Full probes config options
