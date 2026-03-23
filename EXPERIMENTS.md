# EXPERIMENTS.md

This guide is for LLM agents helping design and create new simulation scenarios using configuration (YAML) — without modifying code.

## 1) Scenario Design Workflow

A complete scenario consists of:

1. **Persona Pipeline** — Define agent populations (classes, counts, data sources, personas)
2. **Social Network** — Define followership patterns, activity rates, network topology
3. **Probes** — Define evaluation questions to ask agents during/after simulation
4. **Setting & Event** — Provide narrative context for the simulation
5. **Optional sim/platform overrides** — Customize sim parameters or platform type for this scenario

### Creating a New Scenario

**Directory Structure:**
```
scenarios/{scenario_name}/conf/
├── scenario/
│   └── {scenario_name}.yaml        (Required: @package scenario)
├── sim.yaml                         (Optional: @package sim, scenario-specific overrides)
└── social_media.yaml               (Optional: @package social_media, platform choice)
```

### File 1: scenario/{name}.yaml

**Required header:**
```yaml
# @package scenario
```

**Required fields:**
```yaml
scenario_name: election
jobname_format: "N${sim.num_agents}_T${sim.num_steps}_${experiment_name}_run1"

setting:
  name: "Storhampton"
  background:
    - "Small town with ~2500 residents"
    - "Economic tensions between communities"

event:
  name: "Mayoral Election"
  context: "Heated campaign on social media between two candidates"
```

**Complete example:**

See `scenarios/election/conf/scenario/election.yaml` for a production scenario with all sections.

### File 2: Persona Pipeline

Defines how to construct agent entities from data sources:

```yaml
persona_pipeline:
  processing_mode: raw              # raw | formative | llm_formative

  defaults:                         # Applied to all classes
    params:
      scenario_context: ${scenario.event.context}
      bio: ""
      style: ""
      goal: null
    shared_memories:
      - "You are a resident of Storhampton"
      - ${scenario.event.context}

  classes:
    voter:
      count: 497
      prefab_module: mastodon_sim.agents.entity
      sim_role_name: voter           # For activity rates
      model: null                     # null = use sim.llm_name, or override per-class
      data:
        source: hf_dataset
        dataset: nvidia/Nemotron-Personas-USA
        split: train
      field_map:
        context: persona
        name: full_name              # Map HF dataset field to agent field
      params:
        goal: "Have a good day and vote in the election"
      shared_memories:
        - "Voters care about the town's economy"
      flow_tag: default              # Optional: for multi-flow routing

    candidate:
      count: 2
      prefab_module: mastodon_sim.agents.entity
      sim_role_name: candidate
      data:
        source: config_path           # Reference another config section
        path: candidates              # Dot-path in this YAML
        expand_values: true
      field_map:
        name: name
        context: persona
        style: style
        goal: goal
      flow_tag: active

    news_account:
      count: 1
      prefab_module: mastodon_sim.agents.entity
      sim_role_name: news_account
      data:
        source: config_path
        path: news_account
        expand_values: true
      field_map:
        name: name
        bio: bio
        seed_post: seed_post
```

**Data Sources:**

| Source | Config | Example |
|--------|--------|---------|
| HuggingFace Dataset | `source: hf_dataset`<br>`dataset: nvidia/Nemotron-Personas-USA` | Pulls personas from public dataset |
| Local JSON | `source: local_json`<br>`path: agents.json` | File with array of objects |
| Inline YAML | `source: inline`<br>`records: [{name: Alice, bio: "..."}]` | Defined directly in scenario |
| Config Reference | `source: config_path`<br>`path: candidates` | Dot-path into this YAML file |

### File 3: Social Network Topology

Controls how agents follow each other:

```yaml
social_network:
  # Activity state transitions (per sim_role_name)
  activity_transition_rates:
    voter:
      inactive_to_active: 0.1
      active_to_inactive: 0.2
    candidate:
      inactive_to_active: 0.8
      active_to_inactive: 0.1

  # Agents that everyone follows
  fully_connected_targets:
    - candidate
    - news_account

  # Base probability for random followership
  base_followership_probability: 0.4

  # Network topology algorithm
  network_type: barabasi_albert      # barabasi_albert | erdos_renyi
  barabasi_albert_m: 30              # Preferential attachment parameter
```

### File 4: Shared Memories

Context injected into all agents at initialization:

```yaml
shared_memories:
  - "You are a long-time resident of Storhampton"
  - ${scenario.event.context}
  - ${scenario.setting.background}
```

### File 5: Initial Observations

Templates for starting observations (one per agent):

```yaml
initial_observations:
  - "{name} is at home and has just woken up"
  - "{name} remembers they want to check their social media feed"
  - "{name} notices the upcoming election"
```

### File 6: Probes (Evaluation)

Define questions to ask agents during/after simulation:

```yaml
probes:
  deployment:
    enabled: true
    start_step: 1
    every_n_steps: 1
    include_entities: []
    exclude_entities: []

  queries:
    vote_pref:
      probe_name: vote_pref
      query_type: ChoiceProbe
      query_data:
        name: VotePref
        question: "In one word, name the candidate you want to vote for"
        choices:
          - Bill Fredrickson
          - Bradley Carter

    favorability_bill:
      probe_name: favorability_bill
      query_type: NumericRatingProbe
      query_data:
        name: FavorabilityBill
        question: "Rate Bill Fredrickson on a scale of {lo} to {hi}"
        lo: 1
        hi: 10

    will_vote:
      probe_name: will_vote
      query_type: BinaryProbe
      query_data:
        name: WillVote
        question: "Will you cast a vote?"
```

### File 7: Scenario-Specific Entities (Optional)

Define special agents referenced in classes:

```yaml
news_account:
  name: "Storhampton Gazette"
  username: storhampton_gazette
  bio: "Local news for Storhampton"
  context: "Small-town newspaper"
  seed_post: "Good morning, Storhampton!"

candidates:
  conservative:
    name: "Bill Fredrickson"
    partisan_type: conservative
    persona: "45-year-old local businessman"
    style: "Direct, focuses on economics"
    goal: "Win the election"
  progressive:
    name: "Bradley Carter"
    partisan_type: progressive
    persona: "35-year-old high school teacher"
    style: "Inviting, focuses on environment"
    goal: "Win the election"
```

---

## 2) Optional: Scenario-Specific sim.yaml Overrides

Create `scenarios/{name}/conf/sim.yaml` to customize simulation parameters:

```yaml
# @package sim

# Only list fields that differ from base defaults
# Fields omitted here will use base.yaml values

num_agents: 500
num_steps: 200
action_mode: tool_calling

# Enable multi-flow routing for different agent types
enable_gm_multi_flow: false        # Set true if different agents need different components
enable_engine_multi_flow: false    # Set true if flows should be scheduled in phases

timeline_strategy: follower_chronological
timeline_config:
  recsys_ratio: 0.6
  follower_ratio: 0.4

enabled_actions:                   # Restrict to specific actions (exact function names)
  - create_tweet
  - like_tweet
  - repost_tweet
  - follow_user
```

**Important:** Missing fields automatically fallback to `src/mastodon_sim/conf/sim/base.yaml` defaults. This keeps scenario files minimal.

### File 8: Optional social_media.yaml

Create if you want a different platform:

```yaml
# @package social_media

platform_type: reddit_like         # twitter_like | reddit_like | mastodon
```

---

## 3) Common Scenario Patterns

### Pattern 1: Active vs Inactive Agents

**Goal:** Some agents post frequently, others are lurkers.

```yaml
persona_pipeline:
  classes:
    active_users:
      count: 100
      flow_tag: active
      sim_role_name: active
      ...

    lurkers:
      count: 400
      flow_tag: inactive
      sim_role_name: inactive
      ...

social_network:
  activity_transition_rates:
    active:
      inactive_to_active: 0.9
      active_to_inactive: 0.1
    inactive:
      inactive_to_active: 0.05
      active_to_inactive: 0.5
```

Then in `sim.yaml` (if needed):

```yaml
enable_gm_multi_flow: true
# This allows different observation/resolution for each flow
# (optional — both flows still work with default components)
```

### Pattern 2: Different Recommendation Algorithms per Agent Class

Configure in scenario persona_pipeline:

```yaml
persona_pipeline:
  classes:
    tech_savvy:
      count: 100
      data: ...
      params:
        recsys_type: twitter      # TF-IDF: content-based personalization

    general_users:
      count: 400
      data: ...
      params:
        recsys_type: reddit       # Engagement-based hot score
```

The backend automatically initializes all unique types and updates them.

### Pattern 3: Fixed-Action Agents (News Bot, Announcer)

For agents that perform deterministic actions:

```yaml
persona_pipeline:
  classes:
    news_bot:
      count: 1
      prefab_module: mastodon_sim.agents.fixed_entity
      data:
        source: config_path
        path: news_bot
      fixed_action:
        enabled: true
        action_set_ref: news_bot_actions
        selection_policy: round_robin
        on_exhaustion: loop

fixed_action_sets:
  inline:
    news_bot_actions:
      actions:
        - action: create_tweet
          args:
            status: "Breaking: {topic}"
          weight: 1.0
        - action: create_tweet
          args:
            status: "Update on {topic}"
          weight: 1.0
```

---

## 4) Running Your Scenario

**From CLI:**
```bash
python -m mastodon_sim.runtime.runner --config-path scenarios/my_scenario/conf
```

**With overrides:**
```bash
python -m mastodon_sim.runtime.runner --config-path scenarios/my_scenario/conf \
    sim.num_agents=1000 sim.num_steps=100
```

**From Dashboard:**
1. Open `streamlit run src/mastodon_sim/dashboard/launch_app.py`
2. Select scenario from dropdown
3. Modify settings as needed
4. Click "Save Scenario" to persist
5. Click "Run Simulation"

---

## 5) Understanding Multi-Flow (Advanced)

If you want different agent populations to receive different observations or components, enable multi-flow:

```yaml
enable_gm_multi_flow: true

gm:
  preset: shared_flow              # Allows multiple component instances
  components:
    observe:
      # Create two separate Observe components
      timeline:
        built_in: timeline_every_turn
        params: {}
      episode:
        built_in: episode_only
        params: {}
```

Then route agents by `flow_tag`:

```yaml
persona_pipeline:
  classes:
    active:
      flow_tag: active              # Will receive timeline observations
    lurkers:
      flow_tag: lurkers             # Will receive episode observations
```

The game master automatically routes each agent to the correct component instance based on their flow.

---

## 6) Config Composition & Fallback

**How Hydra merges configs (in order):**

1. **Package defaults** (lowest priority)
   - `src/mastodon_sim/conf/sim/base.yaml`
   - `src/mastodon_sim/conf/social_media/twitter_like.yaml`
   - `src/mastodon_sim/conf/scenario/default.yaml`

2. **Your scenario overrides** (higher priority)
   - `scenarios/my_scenario/conf/sim.yaml` (if present)
   - `scenarios/my_scenario/conf/social_media.yaml` (if present)
   - `scenarios/my_scenario/conf/scenario/my_scenario.yaml`

3. **CLI overrides** (highest priority)
   - `sim.num_agents=500`
   - `sim.llm_name=gpt-4o`

**Fallback behavior:**
- If `sim.yaml` omits `llm_name`, uses `base.yaml` value (gpt-4o-mini)
- If `social_media.yaml` omits `use_server`, uses `twitter_like.yaml` value (false)
- If scenario omits a probe, that probe is not deployed

This means your scenario files only need to specify what's **different** from defaults.

---

## 7) Reference: Default Values

**From `src/mastodon_sim/conf/sim/base.yaml`:**
- `llm_name`: gpt-4o-mini
- `num_agents`: 100
- `num_steps`: 50
- `action_mode`: tool_calling
- `memory_backend`: list
- `timeline_strategy`: follower_chronological
- `seed_posts.type`: llm
- `enable_gm_multi_flow`: false
- `enable_engine_multi_flow`: false

**From `src/mastodon_sim/conf/social_media/twitter_like.yaml`:**
- `platform_type`: twitter_like
- Supports: `create_tweet`, `like_tweet`, `repost_tweet`, `reply_to_tweet`, `follow_user`

**From `src/mastodon_sim/conf/scenario/default.yaml`:**
- Basic default personas from Nemotron dataset
- Fully connected news account
- Basic probes (none by default)

---

## 8) Troubleshooting

**Problem:** "Scenario file not found"
- **Check**: `scenarios/{name}/conf/scenario/{name}.yaml` exists
- **Check**: File has `# @package scenario` as first line

**Problem:** "Unknown field in persona_pipeline"
- **Check**: `docs/configuration.md` Scenario Config section for valid fields
- **Check**: All agent classes have `prefab_module` and `data.source`

**Problem:** "Agent count exceeds num_agents"
- **Check**: Sum of all `classes[*].count` <= `sim.num_agents`
- **Fix**: Adjust counts or increase `num_agents` in sim.yaml

**Problem:** "Probe query_type unknown"
- **Check**: Query types are: ChoiceProbe, NumericRatingProbe, BinaryProbe, FreeTextProbe
- **Check**: `docs/probes.md` for detailed field requirements per type

---

## 9) Next Steps

1. **Study existing example**: review `scenarios/election/conf/scenario/election.yaml`
2. **Understand defaults**: check `src/mastodon_sim/conf/sim/base.yaml`
3. **Create your scenario**: mkdir and scaffold with the structure above
4. **Test it**: run via CLI with `--config-path`
5. **For complex scenarios**: consult ARCHITECTURE.md (multi-flow deep dive) and AGENTS.md (custom agents)

