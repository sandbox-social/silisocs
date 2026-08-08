# Election Scenario Walkthrough

This tutorial walks through the bundled election scenario: a large cast, several
agent classes, per-role activity rates, and custom probes. Read the
[Scenario Guide](../scenario_guide.md) first if you want the general recipe for
authoring one; this page reads a real scenario back to you. Every config key
mentioned here has its full semantics in the
[Configuration Reference](../configuration.md).

The scenario lives in the repository's `scenarios/` directory (example content,
not part of the installed wheel), and its persona source needs the `hf` extra:
`pip install "silisocs[hf]"`.

## Overview

The scenario simulates a mayoral election in **Storhampton**, a fictional small
town, on the Twitter-like backend. Three agent classes:

| Class | `count` | Role |
|-------|---------|------|
| `voter` | 497 | Residents, personas from the `nvidia/Nemotron-Personas-USA` HuggingFace dataset |
| `candidate` | 2 | Bill Fredrickson (conservative) and Bradley Carter (progressive) |
| `news_account` | 1 | Storhampton Gazette, posts news headlines |

**500 agents, 15 steps** at the shipped defaults. That is a real experiment, not
a smoke test — see [Running it](#running-it) for the cheap version.

---

## Scenario Config

The scenario config is split across four files:

| File | Contents |
|---|---|
| `scenarios/election/conf/world/default.yaml` | Run params, setting, event, news data selection |
| `scenarios/election/conf/agents/default.yaml` | Persona pipeline, candidate and news-account definitions |
| `scenarios/election/conf/sim.yaml` | Per-role participation rates |
| `scenarios/election/conf/env.yaml` | Backend, social graph, GM components |
| `scenarios/election/conf/eval.yaml` | Probe definitions and deployment schedule |

The `world/` and `agents/` files are config *groups* (note the `@package`
headers); `sim.yaml`, `env.yaml`, and `eval.yaml` are flat partial overrides
merged into the composed config. See
[How Config Overrides Work](../configuration.md#how-config-overrides-work).

### Setting

```yaml
# scenarios/election/conf/world/default.yaml
# @package _global_
scenario_name: election
num_agents: 500
num_steps: 15
seed: 1
output_dir: ""

setting:
  name: Storhampton
  background:
  - Storhampton is a small town with a population of approximately 2,500 people.
  - Founded in the early 1800s as a trading post along the banks of the Avonlea River...
  - The town's economy was built on manufacturing...
  - Storhampton's population consists of 60% native-born residents and 40% immigrants...
```

### Agent Classes

**Voters** are built from the HuggingFace persona dataset:

```yaml
# scenarios/election/conf/agents/default.yaml
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

`count: 497` is the authoritative number: the class builds 497 voters by taking
the first 497 records from the dataset. It is a literal, **not** a reference to
`${num_agents}` — the scenario's cast is a fixed editorial choice, and
`num_agents: 500` is only the declared total (it names the output directory and
is reported in run metadata; it neither caps nor pads the build). The two must
agree — `497 + 2 + 1 = 500` — or the runner logs a mismatch warning at build
time. This distinction is documented in full at
[`num_agents` vs. per-class `count`](../configuration.md#num_agents-vs-per-class-count),
along with what happens when `count` exceeds the records the source supplies.

Every final agent spec must have a unique `name`. This scenario relies on the
default builder's built-in name derivation for `nvidia/Nemotron-Personas-USA`;
other persona-only datasets should map a name field, set
`derive_name_from_context: true`, or provide a custom Agent Builder.

**Candidates** are defined inline via `config_path` referencing the `candidates`
section of the same file:

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

Candidates and the news account are fully connected targets — everyone follows
them. The GM `next_acting` slot holds only environment-derived selection
(`all_agents` / `fixed_order`):

```yaml
# scenarios/election/conf/env.yaml
gm:
  components:
    initialize:
      params:
        graph:
          fully_connected_targets:
            - candidate
            - news_account
          base_followership_probability: 0.4
          network_type: barabasi_albert
          barabasi_albert_m: 30
    next_acting:
      built_in: all_agents
    resolve:
      built_in: tool_calling
```

Who acts each step is a **sim-level participation model**. Config-derived
activity models live under `sim.engine.participation`, not in the GM's
`next_acting` slot; a config that still puts `activity_transition_rates` under
`next_acting` raises a build-time migration error.

```yaml
# scenarios/election/conf/sim.yaml
engine:
  participation:
    built_in: activity_probability
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

The participation filter runs before every GM's `next_acting`, so the agents
acting in a step are participation ∩ `next_acting`. Rates are keyed by
`sim_role_name`.

### Probes

Four probes track voter attitudes, deployed every step from step 1
(`eval.probes.deployment` in `conf/eval.yaml`):

```yaml
# scenarios/election/conf/eval.yaml
probes:
  deployment:
    enabled: true
    start_step: 1
    every_n_steps: 1
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
      probe_data: { name: FavorabilityBradley, lo: 1, hi: 10, question: "..." }
    vote_intent:
      probe_name: vote_intent
      probe_type: BinaryProbe
      probe_data:
        name: VoteIntent
        question: "In one word, will you cast a vote? (reply yes, or no.)"
```

Every probe fires for every agent on every deployment step, so the shipped
defaults mean 500 agents × 4 probes × 15 steps of extra LLM calls on top of the
turns themselves. See [Evaluation Probes](../probes.md) to narrow that with
`include_agents` / `exclude_agents`.

---

## Running it

```sh
# Shipped scale: 500 agents, 15 steps, probes every step. Expensive.
uv run silisocs --config-path scenarios/election/conf
```

To shrink it, override the class `count` — `num_agents` alone will not do it,
because it is the declared total, not the lever:

```sh
# Small run: 17 voters + 2 candidates + 1 news account = 20 agents, 3 steps
uv run silisocs --config-path scenarios/election/conf \
  agents.persona_pipeline.classes.voter.count=17 num_agents=20 num_steps=3
```

To check the config composes without spending a single token:

```sh
uv run silisocs --config-path scenarios/election/conf \
  num_steps=1 sim.llm.provider=scripted \
  agents.persona_pipeline.classes.voter.count=5 num_agents=8
```

---

## Output

The run directory is
`outputs/election/<jobname_format>/<scenario_name>_<timestamp>/`
— for the shipped defaults,
`outputs/election/N500_T15_independent_election/election_2026-05-01_12-30-00/`.
Notable files:

- `run_manifest.json`: the run index — load a run through this, not by guessing paths
- `action_events.jsonl`: campaign posts, voter discussions, candidate interactions
- `probe_events.jsonl`: favorability ratings, vote preferences, intent over time
- `prompts_and_responses.jsonl`: every LLM call, for debugging

The complete file list and the run-health counters are in
[Usage Overview: Output](../usage.md#output).

---

## Customizing the Election

### Change the Candidates

Edit the `candidates` section of `scenarios/election/conf/agents/default.yaml`:

```yaml
candidates:
  conservative:
    name: Jane Doe
    persona: Jane Doe is a 50 year old retired military officer...
```

### Add a Third Candidate

Add an entry under `candidates`, raise the class `count`, and keep the declared
total in step:

```yaml
# agents/default.yaml
candidate:
  count: 3
```

```yaml
# world/default.yaml
num_agents: 501
```

### Use Formative Memories

Switch from the default initialization to formative for richer backstories:

```yaml
# scenarios/election/conf/sim.yaml
initialization:
  agents:
    built_in: formative_memory
```

This generates LLM-powered backstory episodes for each agent before the
simulation begins — at 500 agents, budget for it.

### Add News Bias

The `news_account` class posts headlines from a JSON file under
`scenarios/election/input/news_data/`. Select one in the world config's `data`
section:

```yaml
# scenarios/election/conf/world/default.yaml
data:
  news_file: v1_news_bill_bias   # or v1_news_bradley_bias, v1_news_no_bias, v1_news
  use_news_agent: with_images
```

---

## Where to look next

- [Scenario Guide](../scenario_guide.md) — author your own scenario from scratch
- [Configuration Reference](../configuration.md) — every key used above
- [Study Guide](../study_guide.md) — turn "biased vs. unbiased news" into a
  multi-condition, multi-seed study
