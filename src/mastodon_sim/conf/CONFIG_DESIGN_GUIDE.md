# Configuration Design Guide

This guide explains how to design simulation configs using Hydra, including defaults fallback, multi-flow engine, multi-flow GM, and multi-GM orchestration.

---

## Part 1: Hydra Defaults & Fallback Behavior

### How @package Works

The `@package` directive tells Hydra where to place file contents in the merged config hierarchy:

```yaml
# @package scenario
scenario_name: election
...
```

This places everything under `cfg.scenario.*`. Without `@package`, keys would go to root (wrong).

### Defaults Composition & Fallback

Hydra's `defaults:` list specifies config inheritance:

```yaml
# config.yaml
defaults:
  - sim: base
  - social_media: twitter_like
  - scenario: default
  - _self_
```

**Processing order** (lowest to highest priority):
1. `sim/base.yaml` — loaded first
2. `social_media/twitter_like.yaml` — merged
3. `scenario/default.yaml` — merged
4. `_self_` — current file (highest priority)

**Fallback behavior**: If a key exists in step 1 but is missing in step 2, step 1's value is used.

### External Scenario Defaults

When using `--config-path scenarios/election/conf`, Hydra:
1. Loads package defaults (src/mastodon_sim/conf/)
2. Overlays searchpath configs (scenarios/election/conf/)
3. Merges in order: sim.yaml ← social_media.yaml ← scenario/election.yaml

**Missing fields fallback**: If `scenarios/election/conf/sim.yaml` is missing `num_agents`, the base default (100) is used.

### Example: Election Scenario Setup

**Structure**:
```
scenarios/election/conf/
├── scenario/
│   └── election.yaml              # @package scenario (agents, network)
├── sim.yaml (optional)             # @package sim (experiment overrides)
└── social_media.yaml (optional)    # @package social_media (platform)
```

**Run with defaults**:
```bash
python -m mastodon_sim.runtime.runner --config-path scenarios/election/conf
# Uses: base sim (100 agents) + twitter_like platform + election scenario
```

**Run with custom sim params**:
```bash
python -m mastodon_sim.runtime.runner --config-path scenarios/election/conf \
    sim.num_agents=500 sim.num_steps=200
# Overrides: 500 agents, 200 steps (merged with other base params)
```

**Scenario with custom sim.yaml**:
```yaml
# scenarios/election/conf/sim.yaml
# @package sim

# Only override what's different from base
num_agents: 250              # Override: 250 agents
num_steps: 100               # Override: 100 steps
action_mode: tool_calling    # Override: tool-calling mode
# All other fields (llm_name, timeline_strategy, etc.) fall back to base defaults
```

---

## Part 2: Simple Simulation (Default)

**When to use**: Single action-per-step, single GM, no flows.

### Minimal Config

```bash
python -m mastodon_sim.runtime.runner
# Uses: base.yaml (100 agents, 50 steps, tool_calling) + twitter_like + default scenario
```

### Key Settings in base.yaml

```yaml
enable_gm_multi_flow: false       # Single component per role (BaseGM)
enable_engine_multi_flow: false    # No flow scheduling (BaseEngine)

gm:
  preset: base                     # One social media GM

engine:
  preset: base                     # Simple engine
  action_loop:
    built_in: single_action        # One action per step
  flow_routing:
    flow_order: [default]          # Single flow
```

---

## Part 3: Multi-Flow GM

**When to use**: Different agent groups need different components per flow.

**Example**: Some agents see timeline, others see episode number.

### Enable in sim.yaml

```yaml
# scenarios/my_scenario/conf/sim.yaml
# @package sim

enable_gm_multi_flow: true        # ← Enable multi-flow GM

gm:
  preset: base                     # Will use MultiFlowGM if enable_gm_multi_flow=true

  # Component instances per role (one object per flow)
  components:
    observe:
      # Multiple observe components, keyed by internal name
      timeline_make_observation:
        built_in: timeline_every_turn
        class_path: null
        params: {}

      episode_make_observation:
        built_in: episode_number
        class_path: null
        params: {}
```

### How It Works

1. **GM builds multiple component instances** (e.g., TimelineObservation + EpisodeObservation).
2. **Routes agents to instances** based on `entity_action_flows` mapping.
3. **Each agent uses assigned flow** → gets matched component instance.

### Flow Assignment

Define in scenario:

```yaml
# @package scenario
persona_pipeline:
  classes:
    observer:
      count: 100
      sim_role_name: observer
      flow_tag: timeline_flow       # ← Route to timeline component

    reporter:
      count: 50
      sim_role_name: reporter
      flow_tag: episode_flow        # ← Route to episode component
```

---

## Part 4: Multi-Flow Engine

**When to use**: Different action policies for different flows (e.g., news posts in batch, user posts individual).

### Enable in sim.yaml

```yaml
# scenarios/my_scenario/conf/sim.yaml
# @package sim

enable_engine_multi_flow: true     # ← Enable flow-aware engine

engine:
  preset: flow                     # Use FlowEngine (if enabled)

  flow_routing:
    flow_order: [news_agent, user_agent]  # Execution order (sequential)
    entity_to_flow:
      news_account: news_agent
      user_1: user_agent
      user_2: user_agent
      # ... more agents

  # Per-flow action policies (override global action_loop)
  flow_policies:
    news_agent:
      built_in: fixed_count        # News posts N times
      params:
        count: 5

    user_agent:
      built_in: single_action      # Users post once
      params: {}
```

### How It Works

1. **FlowEngine processes flows sequentially** per `flow_order`.
2. **Each flow has its own action_loop policy**.
3. **Agents in a flow use flow's policy** (or fall back to global).

---

## Part 5: Multi-GM Orchestration

**When to use**: Multiple GMs managing different aspects (e.g., one for user actions, one for algorithm).

### Enable in sim.yaml

```yaml
# scenarios/my_scenario/conf/sim.yaml
# @package sim

gm_orchestration:
  gms:
    # List each GM with its config
    - name: user_gm
      module_path: mastodon_sim.environments.gm.base_game_master
      params: {}

    - name: algorithm_gm
      module_path: mastodon_sim.environments.gm.algorithm_game_master
      params: {}

  flow_bindings:
    # Which GM handles which flow
    flow_to_gm:
      user_flow: user_gm
      algorithm_flow: algorithm_gm

    # OR: Multiple GMs per flow (chained execution)
    flow_to_gms:
      actions_flow:
        - user_gm      # Step 1: User decides action
        - algorithm_gm # Step 2: Algorithm moderates
```

### How It Works

1. **Multiple GMs registered** with runner.
2. **Flow-to-GM mapping** routes entity flows to specific GMs.
3. **GMs execute in flow order** if `flow_to_gms` defined.

---

## Part 6: Combining Features (Advanced)

### Multi-Flow GM + Multi-Flow Engine

```yaml
enable_gm_multi_flow: true       # GM routes to component instances
enable_engine_multi_flow: true   # Engine enforces per-flow policies

gm:
  components:
    observe:
      timeline_obs:
        built_in: timeline_every_turn
      episode_obs:
        built_in: episode_number

engine:
  flow_routing:
    flow_order: [observer, reporter]
    entity_to_flow:
      user_1-100: observer
      reporter_1-50: reporter

  flow_policies:
    observer:
      built_in: single_action   # Observers post once
    reporter:
      built_in: fixed_count
      params:
        count: 3                 # Reporters post 3 times
```

**Execution**:
1. GM routes reporter_1 → episode_observation component.
2. Engine assigns reporter_1 → reporter flow.
3. Engine runs reporter flow → reporter fixed_count policy (3 posts).

### Multi-Flow GM + Multi-GM Orchestration

```yaml
enable_gm_multi_flow: true

gm_orchestration:
  gms:
    - name: user_interaction_gm
      # ...
    - name: content_moderation_gm
      # ...

gm:
  components:
    observe:
      user_timeline: { built_in: timeline_every_turn }
      moderation_view: { built_in: moderation_queue }
```

**Behavior**: Multiple GMs, each can route agents to different components.

---

## Part 7: Configuration Examples

### Example 1: Simple Election Scenario

```yaml
# scenarios/election/conf/sim.yaml
# @package sim

num_agents: 500                      # Override: 500 voters
num_steps: 200                       # Override: 200 campaign days
action_mode: tool_calling            # Tool-calling for voting
timeline_strategy: hybrid_recsys_follower
```

**Fallback behavior**:
- `num_agents` = 500 (overridden)
- `llm_name` = gpt-4o-mini (from base default)
- `enable_gm_multi_flow` = false (from base default)
- etc.

### Example 2: Multi-Flow Simulation

```yaml
# scenarios/misinformation/conf/sim.yaml
# @package sim

enable_gm_multi_flow: true

gm:
  components:
    observe:
      user_timeline:
        built_in: timeline_every_turn
      bot_detection:
        built_in: bot_detection_view

engine:
  preset: base                       # No engine multi-flow
```

### Example 3: Full Advanced Setup

```yaml
# scenarios/complex/conf/sim.yaml
# @package sim

enable_gm_multi_flow: true
enable_engine_multi_flow: true

gm:
  components:
    observe:
      main_timeline: { built_in: timeline_every_turn }
      moderation: { built_in: moderation_queue }

engine:
  flow_routing:
    flow_order: [main_users, content_mods, admins]
    entity_to_flow:
      regular_user_1-1000: main_users
      moderator_1-100: content_mods
      admin_1-10: admins

  flow_policies:
    main_users:
      built_in: single_action
    content_mods:
      built_in: fixed_count
      params: { count: 5 }
    admins:
      built_in: open_ended
      params:
        max_actions: 20
        done_token: DONE

gm_orchestration:
  gms:
    - name: social_media_gm
      module_path: mastodon_sim.environments.gm.base_game_master
    - name: moderation_gm
      module_path: custom.moderation.game_master

  flow_bindings:
    flow_to_gm:
      main_users: social_media_gm
      content_mods: moderation_gm
```

---

## Part 8: Configuration Tips

### Best Practices

1. **Keep scenario-specific sim.yaml minimal**:
   ```yaml
   # Good: Only overrides
   num_agents: 500
   num_steps: 200

   # Bad: All fields (causes confusion and hard to track changes)
   num_agents: 500
   num_steps: 200
   llm_name: gpt-4o-mini
   action_mode: tool_calling
   ...
   ```

2. **Use comments to explain overrides**:
   ```yaml
   num_agents: 500  # Election scenario needs larger town
   num_steps: 200   # Campaign lasts 200 days
   ```

3. **Test locally before scaling**:
   ```bash
   # Test with small numbers
   python -m mastodon_sim.runtime.runner \
     --config-path scenarios/my_scenario/conf \
     sim.num_agents=10 sim.num_steps=5
   ```

4. **Check merged config before running**:
   ```bash
   # View final merged config
   python -m mastodon_sim.runtime.runner \
     --config-path scenarios/my_scenario/conf \
     --cfg job
   ```

### Troubleshooting

**"Missing required field XXX"**:
- Check if field needs to be in sim.yaml (scenario-specific)
- Or if it should come from base.yaml default
- Ensure `@package` directive is correct

**"Flow XXX has no GM assigned"**:
- Check `gm_orchestration.flow_bindings.flow_to_gm`
- All flows in `entity_to_flow` must have a GM

**"Enable XXX flag but feature not available"**:
- Flags require companion config structure
- e.g., `enable_engine_multi_flow=true` requires `engine.flow_policies`

