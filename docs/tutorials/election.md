# Election Scenario Walkthrough

This tutorial walks through the bundled election scenario — a complex simulation
with multiple agent classes, custom probes, and a realistic setting.

## Overview

The election scenario simulates a mayoral election in **Storhampton**, a
fictional small town. It has three agent classes:

| Class | Count | Role |
|-------|-------|------|
| `voter` | 497 | Residents with unique personas from HuggingFace |
| `candidate` | 2 | Bill Fredrickson (conservative) and Bradley Carter (progressive) |
| `news_account` | 1 | Storhampton Gazette — posts news headlines |

---

## Scenario Config

The full config lives at `scenarios/election/conf/scenario/election.yaml`.

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
    prefab_module: mastodon_sim.agents.entity
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

### Social Network

Candidates and the news account are fully connected targets — everyone follows them:

```yaml
social_network:
  fully_connected_targets:
    - candidate
    - news_account
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

Three custom probe types track voter attitudes every step:

```yaml
probes:
  query_lib_module: mastodon_sim.scenarios.election.config_utils.probe_lib
  queries:
    0:
      query_type: VotePref          # "Who do you prefer?"
      query_data:
        interaction_premise_template:
          candidate1: Bill Fredrickson
          candidate2: Bradley Carter
    1:
      query_type: Favorability      # "Rate Bill Fredrickson 1-10"
      query_data:
        interaction_premise_template:
          candidate: Bill Fredrickson
    2:
      query_type: Favorability      # "Rate Bradley Carter 1-10"
      query_data:
        interaction_premise_template:
          candidate: Bradley Carter
    3:
      query_type: VoteIntent        # "Will you vote?"
```

---

## Running the Election

```sh
# Full scale (497 voters, 200 steps)
uv run mastodon-sim --config-path scenarios/election/conf

# Quick test
uv run mastodon-sim --config-path scenarios/election/conf sim.num_agents=20 sim.num_steps=5
```

!!! note
    When you override `sim.num_agents`, the voter count adjusts automatically
    because it references `${sim.num_agents}` minus the fixed candidate and news
    account slots.

---

## Output

Output lands in `scenarios/election/outputs/`:

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
persona_pipeline:
  processing_mode: formative
```

This generates LLM-powered backstory episodes for each voter before the
simulation begins.

### Add News Bias

The election scenario supports news headline files. Configure in the `data` section:

```yaml
data:
  news_file: v1_news_bill_bias
  use_news_agent: with_images
```

Place news JSON files in the scenario's input directory.
