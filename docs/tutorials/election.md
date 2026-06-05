# Election Scenario Walkthrough

This tutorial walks through the bundled election world — a complex simulation
with multiple agent classes, custom probes, and a realistic setting.

## Overview

The election world simulates a mayoral election in **Storhampton**, a
fictional small town. It has three agent classes:

| Class | Count | Role |
|-------|-------|------|
| `voter` | 497 | Residents with unique personas from HuggingFace |
| `candidate` | 2 | Bill Fredrickson (conservative) and Bradley Carter (progressive) |
| `news_account` | 1 | Storhampton Gazette — posts news headlines |

---

## Scenario Config

The scenario config is split across:

- `scenarios/election/conf/world/default.yaml` (run params, setting, event, data)
- `scenarios/election/conf/agents/default.yaml` (persona pipeline)
- `scenarios/election/conf/eval/default.yaml` (probe config)

### Setting

```yaml
setting:
  name: Storhampton
  background:
    - Storhampton is a small town with a population of approximately 2,500 people.
    - Founded in the early 1800s as a trading post along the banks of the Avonlea River...
    - The town's economy was built on manufacturing...
    - Storhampton's population consists of 60% native-born residents and 40% immigrants...
```

### Agent Classes

**Voters** use the HuggingFace persona dataset:

```yaml
classes:
  voter:
    count: 497
    class_path: silisocs.agents.native.NativeAgent
    sim_role_name: voter
    data:
      source: hf_dataset
      dataset: nvidia/Nemotron-Personas-USA
      split: train
    field_map:
      context: persona
    params:
      goal: Their goal is have a good day and vote in the election.
```

Every final agent spec must have a unique `name`. This world can use the
default builder's built-in name derivation for `nvidia/Nemotron-Personas-USA`;
other persona-only datasets should map a name field, set
`derive_name_from_context: true`, or provide a custom Agent Builder.

**Candidates** are defined inline via `config_path` referencing the `candidates`
section:

```yaml
candidate:
  count: 2
  data:
    source: config_path
    path: candidates
    expand_values: true
  field_map:
    name: name
    context: persona
    style: style
    goal: goal

# Later in the same file:
candidates:
  conservative:
    name: Bill Fredrickson
    persona: Bill Fredrickson is a 45 year old local businessman...
    style: Bill Fredrickson uses direct language...
    goal: Bill Fredrickson's goal is to win the election...
  progressive:
    name: Bradley Carter
    persona: Bradley Carter is a 35 year old high school teacher...
    style: Bradley Carter uses inviting language...
    goal: Bradley Carter's goal is to win the election...
```

### GM Component Behavior

Candidates and the news account are fully connected targets — everyone follows them:

```yaml
gm:
  components:
    initialize:
      params:
        graph:
          fully_connected_targets:
            - candidate
            - news_account
    next_acting:
      params:
        activity_transition_rates:
          voter:
            inactive_to_active: 0.1     # Voters are mostly passive
            active_to_inactive: 0.2
          candidate:
            inactive_to_active: 0.8     # Candidates are very active
            active_to_inactive: 0.1
          news_account:
            inactive_to_active: 1       # News always posts
            active_to_inactive: 0
```

### Probes

Named built-in probes track voter attitudes every step:

```yaml
probes:
  probes:
    vote_pref:
      probe_name: vote_pref
      probe_type: ChoiceProbe
      probe_data:
        name: VotePref
        question: "In one word, name the candidate you want to vote for."
        choices:
          - Bill Fredrickson
          - Bradley Carter
    favorability_bill:
      probe_name: favorability_bill
      probe_type: NumericRatingProbe
      probe_data:
        name: FavorabilityBill
        question: "Return a single numeric value ranging from {lo} to {hi} for Bill Fredrickson."
        lo: 1
        hi: 10
    favorability_bradley:
      probe_name: favorability_bradley
      probe_type: NumericRatingProbe
      probe_data:
        name: FavorabilityBradley
        question: "Return a single numeric value ranging from {lo} to {hi} for Bradley Carter."
        lo: 1
        hi: 10
    vote_intent:
      probe_name: vote_intent
      probe_type: BinaryProbe
      probe_data:
        name: VoteIntent
        question: "In one word, will you cast a vote? Reply yes or no."
```

---

## Running the Election

```sh
# Full scale (497 voters, 200 steps)
uv run silisocs --config-path scenarios/election/conf

# Quick test
uv run silisocs --config-path scenarios/election/conf num_agents=20 num_steps=5
```

!!! note
    When you override `num_agents`, the voter count adjusts automatically
    because it references `${num_agents}` minus the fixed candidate and news
    account slots.

---

## Output

Output lands in `outputs/election/`:

- `action_events.jsonl` — Campaign posts, voter discussions, candidate interactions
- `probe_events.jsonl` — Favorability ratings, vote preferences, intent over time
- `prompts_and_responses.jsonl` — All LLM calls for debugging

---

## Customizing the Election

### Change the Candidates

Edit the `candidates` section in `election.yaml`:

```yaml
candidates:
  conservative:
    name: Jane Doe
    persona: Jane Doe is a 50 year old retired military officer...
```

### Add a Third Candidate

Add a new entry under `candidates` and increase the count:

```yaml
candidate:
  count: 3
```

### Use Formative Memories

Switch from raw to formative initialization for richer agent backstories:

```yaml
sim:
  initialization:
    agents:
      built_in: formative_memory
```

This generates LLM-powered backstory episodes for each voter before the
simulation begins.

### Add News Bias

The election world supports news headline files. Configure in the `data` section:

```yaml
data:
  news_file: v1_news_bill_bias
  use_news_agent: with_images
```

Place news JSON files in the world's input directory.
