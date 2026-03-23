# Multi-Flow Component Routing Guide

**For**: Developers wanting different agents to receive different observations, resolutions, or recommendations.
**When to use**: Active vs lurker agents, trusted vs untrusted sources, different decision-making pipelines.

---

## Overview

Multi-flow allows different agent populations (flows) to use different component instances while sharing a single backend.

```
Without Multi-Flow (default):
  alice (active) ──┐
  bob (active)    ├──→ [Single Observe] ──→ [Single Resolve] ──→ Shared Backend
  charlie (lurk)  ┘

With Multi-Flow:
  alice (active) ────→ [Timeline Observe] ──┐
  bob (active)        [Timeline Observe]    ├──→ [Parsed Resolve] ──→ Shared Backend
                                           │
  charlie (lurk) ────→ [Episode Observe] ──┘    [Generic Resolve]
```

---

## Configuration

Enable with a single flag:

```yaml
sim:
  enable_gm_multi_flow: true  # Enable multi-flow routing
  gm:
    preset: shared_flow  # Use MultiFlowGM
```

Define multiple component instances and flow-to-field mappings:

```yaml
gm:
  components:
    # Define multiple observe components
    observe:
      timeline:
        built_in: timeline_every_turn
      episode:
        built_in: episode_only

      # Flow-to-field mapping
      entities:
        active:                        # Flow name
          timeline_strategy: pure_recsys
        fixed_pre:
          timeline_strategy: follower_chronological

    # Resolve can also have multi-fields if needed
    resolve:
      built_in: parsed_action
      entities:
        active:
          action_parser: strict
        fixed_pre:
          action_parser: lenient

    # Recommendation system
    recommend:
      entities:
        active:
          recsys_type: twitter
        fixed_pre:
          recsys_type: reddit

scenario:
  entities:
    - agent_name: alice
      action_flow: active      # Routes to active flow components
    - agent_name: bob
      action_flow: active
    - agent_name: sentinel_1
      action_flow: fixed_pre   # Routes to fixed_pre flow components
```

---

## How It Works

1. **Flow Assignment**: Each agent is assigned a flow (via `action_flow` parameter)
2. **Component Routing**: MultiFlowSMAct looks up which components the agent's flow uses
3. **Field Mapping**: Components receive flow→field mappings for per-flow customization
4. **Execution**: Only those components execute for that agent

```python
# Inside MultiFlowSMAct.get_components_for_entity(alice):
flow = entity_action_flows.get("alice", "default")  # → "active"
components = flow_to_component_map["active"]        # → ["observe__timeline_make_observation", "resolve__parsed_action"]
```

---

## Multi-Field Values

Components can access per-flow configuration:

```python
class TimelineObservation(FlowComponent):
    @property
    @multi_field(str)
    def timeline_strategy(self) -> str:
        # Get strategy for current entity's flow
        return self.get_field_for_entity("timeline_strategy", default="follower_chronological")
```

Configuration passes flow→field mappings:

```yaml
observe:
  entities:
    active:         # Flow name (not entity name!)
      timeline_strategy: pure_recsys
    lurker:
      timeline_strategy: follower_chronological
```

---

## Real-World Example: Information Accuracy Study

```yaml
sim:
  enable_gm_multi_flow: true
  gm:
    preset: shared_flow
    components:
      observe:
        # Trusted agents see verified content only
        trusted_timeline:
          built_in: timeline_every_turn
        # Untrusted agents see everything
        full_timeline:
          built_in: timeline_every_turn

        entities:
          trusted:
            timeline_strategy: pure_recsys  # Only AI recommendations (verified)
            recsys_type: twitter
          disinformation:
            timeline_strategy: hybrid_recsys_follower  # Mix from followers (unverified)
            recsys_type: reddit

scenario:
  entities:
    - agent_name: fact_checker_1
      action_flow: trusted
    - agent_name: fact_checker_2
      action_flow: trusted
    - agent_name: conspiracy_bot_1
      action_flow: disinformation
    - agent_name: conspiracy_bot_2
      action_flow: disinformation
```

---

## Migration from Simple to Multi-Flow

**Before**:
```yaml
sim:
  gm:
    components:
      observe:
        built_in: timeline_every_turn
      resolve:
        built_in: parsed_action
```

**After**:
```yaml
sim:
  enable_gm_multi_flow: true
  gm:
    preset: shared_flow
    components:
      observe:
        timeline:
          built_in: timeline_every_turn
        episode:
          built_in: episode_only
```

No other changes needed - existing code continues to work.

---

## Advanced: Per-Flow Policies

While MultiFlowGM handles component routing, different flows can also have different action frequencies by combining with engine-level flow routing:

```yaml
engine:
  flow_routing:
    flow_order: [fixed_pre, active, lurker]  # Execution order
    entity_to_flow:
      alice: active
      bob: active
      sentinel_1: fixed_pre
```

This ensures:
1. fixed_pre agents act first (without competition)
2. Then active agents (in parallel)
3. Then lurker agents (in parallel)

Each flow uses its configured components from the GM.

---

## See Also

- [RecommendationComponent](/docs/recommendation_system.md) for multi-algorithm usage
- [Component Extension](docs/environment_layer.md) for building custom components
