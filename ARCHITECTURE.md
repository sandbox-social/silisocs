# Mastodon-Sim Architecture: Multi-Flow & Component Routing

**This guide is for LLM agents helping understand or extend the framework's architecture.**

**For designing experiments via configuration:** See [EXPERIMENTS.md](EXPERIMENTS.md)

**For understanding code extension points:** See [AGENTS.md](AGENTS.md)

---

## Overview

The mastodon-sim simulator is designed as a highly configurable system with distinct abstraction layers:

1. **Agent Layer**: Configurable agent designs with multiple initialization methods
2. **Environment Layer**: Pluggable engines and game masters coordinating agent-environment interaction
3. **Backend Layer**: Pluggable social media platform implementations (Mastodon, Twitter-like, Reddit-like)
4. **Component Layer**: Fine-grained observation, resolution, and scheduling components with optional multi-flow routing

This document describes how these layers work together, with special focus on the multi-flow architecture for component routing.

---

## Part 1: Core Execution Model

### Simple Mode (enable_gm_multi_flow: false)

By default, the simulator uses **BaseGM** with a single component instance per role:

```
Engine schedules agents
  ↓
BaseGM.SMAct selects active entity
  ↓
Context components execute in order:
  - NextActing: Who acts next?
  - Observe: What does active entity see? (single TimelineObservation)
  - Resolve: How is action executed?
  - Recommendation: Update recommendations
  ↓
Agent receives observation and acts
```

**Characteristics:**
- All agents see same type of observation (timeline)
- All agents use same action resolution logic
- All agents get same recommendation algorithm
- No per-flow customization
- Simplest, most efficient path

**Configuration:**
```yaml
sim:
  enable_gm_multi_flow: false  # ← or simply omit (default)
  gm:
    preset: base
```

### Multi-Flow Mode (enable_gm_multi_flow: true)

When multi-flow is enabled, the simulator uses **MultiFlowGM** with multiple component instances and explicit routing:

```
Engine schedules agents
  ↓
MultiFlowGM.MultiFlowSMAct selects active entity
  ↓
1. Determine entity's flow (from entity_action_flows map)
2. Look up flow → {role → component_key} mapping
3. Execute only those components for this entity:
  - NextActing: (shared)
  - Observe: [timeline_make_observation | episode_observation] ← flow-specific
  - Resolve: [parsed_action | generic_action] ← flow-specific
  - Recommendation: (shared, but multi-algorithm)
  ↓
Agent receives flow-specific observation and acts
```

**Characteristics:**
- Different agents can receive different observation types
- Fixed agents see episode summaries; active agents see timelines
- Per-flow customization of component behavior via multi-fields
- Different recommendation algorithms for different flows
- More flexible, slight overhead

**Configuration:**
```yaml
sim:
  enable_gm_multi_flow: true  # ← Enable multi-flow mode
  gm:
    preset: shared_flow  # ← Use MultiFlowGM
    components:
      observe:
        # Multi-instance config: each nested dict is a component instance
        timeline:
          built_in: timeline_every_turn
          params: {}
        episode:
          built_in: episode_only
          params: {}
       # Now agents can be routed to timeline OR episode based on flow
```

---

## Part 2: Agent Flows & Component Routing

### Entity Action Flows

Agents are assigned to flows, which determine their component routing:

```yaml
scenario:
  entities:
    - agent_name: alice
      action_flow: active       # ← Flow assignment
    - agent_name: bob
      action_flow: active
    - agent_name: sentinel_1
      action_flow: fixed_pre    # ← Different flow
```

Multiple agents can share the same flow. All agents in a flow use the same components.

### Flow-to-Component Mapping

MultiFlowGM builds an explicit mapping from flows to component instances:

```python
flow_to_component_map = {
    "active": {
        "observe": "observe__timeline_make_observation",   # Active agents see timelines
        "resolve": "resolve__parsed_action"               # Active agents use standard parser
    },
    "fixed_pre": {
        "observe": "observe__episode_observation",        # Fixed agents see episodes
        "resolve": "resolve__generic_action"              # Fixed agents use generic parser
    }
}
```

**How it's built:**
1. Detect multi-instance component config (nested dicts in YAML)
2. Build each component instance
3. Auto-key by class name (TimelineObservation → observe__timeline_make_observation)
4. Extract unique flows from entity_action_flows
5. Map each flow to component instances

**How it's used:**
1. Concordia calls MultiFlowSMAct.pre_act() for active entity
2. MultiFlowSMAct looks up entity's flow
3. MultiFlowSMAct gets component keys for that flow
4. Only those components execute for this entity
5. Next entity is processed, potentially with different components

---

## Part 3: Multi-Field Component Configuration

### What are Multi-Fields?

Multi-fields allow a **single component instance** to behave differently per entity, based on flow-to-field mapping.

**Example:** RecommendationComponent with different algorithms per flow:

```yaml
gm:
  components:
    recommend:
      entities:           # ← Flow-to-field mapping
        active:
          recsys_type: "twitter"      # Active agents get embedding-based recommendations
        lurker:
          recsys_type: "reddit"       # Lurkers get hot-score recommendations
```

At runtime:
1. RecommendationComponent extracts unique recsys_types: {"twitter", "reddit"}
2. Initializes backend with all types: backend.init_recsys("twitter"); backend.init_recsys("reddit")
3. Updates backend once per episode, backend generates recommendations for all types
4. Each agent sees recommendations computed with their flow's algorithm

### How Multi-Fields Work

**Component-side (declare fields):**
```python
class TimelineObservation(FlowComponent):
    def get_timeline(self, entity_name):
        # Get timeline strategy for this entity's flow
        strategy = self.get_field_for_entity("timeline_strategy", entity_name, default="follower_chronological")
        # Use strategy to fetch timeline
        return backend.get_timeline_strategy(strategy, ...)
```

**GM-side (initialize values):**
```python
# In MultiFlowGM.build():
observe_component = build_observe_component(...)
initialize_component_multi_fields(observe_component, {
    "active": {"timeline_strategy": "pure_recsys"},
    "lurker": {"timeline_strategy": "follower_chronological"}
})
```

**Data structure passed:**
```python
entity_field_map = {
    "active": {       # flow name (not entity name!)
        "timeline_strategy": "pure_recsys",
        "recsys_type": "twitter"
    },
    "lurker": {
        "timeline_strategy": "follower_chronological",
        "recsys_type": "reddit"
    }
}
```

### Which Components Support Multi-Fields?

- ✅ **Observe components** (TimelineObservation): timeline_strategy, recsys_type
- ✅ **RecommendationComponent**: recsys_type (extracts unique types, initializes all)
- ⏳ **Resolve components**: Can add if needed (e.g., action_parser_style)
- ⏳ **Next-acting components**: Can add if needed

---

## Part 4: Recommendation System (Multi-Algorithm Support)

### Architecture

Recommendations now work with multiple algorithms simultaneously:

**Step 1: Initialization (MultiFlowGM.build())**
```python
recommend_component = build_recommendation_component(...)
initialize_component_multi_fields(recommend_component, {
    "active": {"recsys_type": "twitter"},
    "lurker": {"recsys_type": "reddit"}
})
```

**Step 2: First pre_act() call (RecommendationComponent)**
- Extracts unique recsys_types from multi-field mapping
- Calls backend.init_recsys("twitter") and backend.init_recsys("reddit")
- Each call adds to backend's _recsys_types dict (cumulative, not overwriting)

**Step 3: Every N steps (RecommendationComponent)**
- Calls backend.update_recommendations() once
- Backend iterates all initialized types, generates recommendations for each
- Stores recommendations with recsys_type column in database

**Step 4: Agent sees recommendation**
- When TimelineObservation fetches timeline, it includes recommendations
- Recommendations are for agent's configured algorithm type

### Backend Multi-Algorithm State

**Old (single type):**
```python
self.recsys_type = "reddit"  # One type at a time
```

**New (multi-type):**
```python
self._recsys_types = {
    "twitter": {"type": "twitter", "model": SentenceTransformer(...), "embeddings_cache": {}},
    "reddit": {"type": "reddit", "model": None, "embeddings_cache": {}}
}
```

All algorithms are initialized and updated in every call, maximizing recommendation freshness.

---

## Part 5: Timeline Observations

### Timeline Strategy Configuration

Agents can receive timelines computed with different strategies:

```yaml
sim:
  timeline_strategy: follower_chronological  # Default for all agents
  timeline_config:
    recsys_ratio: 0.6  # (for hybrid_recsys_follower strategy)
```

**Available strategies:**
- `follower_chronological`: Posts from followed users, chronologically ordered
- `pure_recsys`: Only recommendations from configured algorithm
- `hybrid_recsys_follower`: Mix of recommendations and follower posts
- `curated_global`: Trending/curated posts (if implemented in backend)

### Per-Flow Timeline Configuration

With multi-fields, different flows can see different timelines:

```yaml
gm:
  components:
    observe:
      timeline:
        built_in: timeline_every_turn
        entities:
          active:
            timeline_strategy: "pure_recsys"        # Recommendations only
          lurker:
            timeline_strategy: "follower_chronological"  # Followers only
```

---

## Part 6: Component Architecture

### Component Types

**1. NextActing Component** (role: next_acting)
- Determines random order or policy
- Decides which agent acts next
- Built-in: activity_markov, activity_probability, all_entities, fixed_order

**2. Observe Component** (role: observe)
- Generates observation text for active entity
- Built-in: TimelineObservation, EpisodeObservation, ChunkStartMakeObservation
- Multi-instance support: Can have both Timeline and Episode simultaneously
- Multi-field support: timeline_strategy, recsys_type

**3. Resolve Component** (role: resolve)
- Parses LLM output into backend-executable actions
- Built-in: ParsedActionResolve, GenericActionResolve, ToolCallingResolve
- Single instance per GM (could extend with multi-instance if needed)

**4. Recommendation Component** (role: recommendation)
- Updates backend recommendations on schedule
- Responsible for initializing multiple algorithms
- Multi-field support: Extracts unique recsys_type, initializes all, updates all

**5. Backend Initializer** (role: initializer)
- Calls backend.initialize() with seed posts, social network, etc.
- Built-in: DefaultBackendInitializer
- Usually single for a GM

---

## Part 7: Configuration Examples

### Simple Configuration (No Multi-Flow)

```yaml
sim:
  enable_gm_multi_flow: false
  gm:
    preset: base
    components:
      observe:
        built_in: timeline_every_turn
      resolve:
        built_in: parsed_action

  # All agents see same timeline, use same resolution
  # Simplest, most efficient path
```

**Multi-flow config with multi-instance component support :**
```yaml
sim:
  enable_gm_multi_flow: true
  gm:
    preset: shared_flow
    components:
      observe:
        timeline:
          built_in: timeline_every_turn
          params: {}
        episode:
          built_in: episode_only
          params: {}
        entities:
          active:
            timeline_strategy: "pure_recsys"
            recsys_type: "twitter"
          fixed_pre:
            timeline_strategy: "follower_chronological"
            recsys_type: "reddit"

      resolve:
        built_in: parsed_action
        entities:
          active:
            action_parser: "strict"
          fixed_pre:
            action_parser: "lenient"

      recommend:
        entities:
          active:
            recsys_type: "twitter"
          fixed_pre:
            recsys_type: "reddit"

scenario:
  entities:
    - agent_name: alice
      action_flow: active      # Uses active flow components
    - agent_name: sentinel_1
      action_flow: fixed_pre   # Uses fixed_pre flow components
```

### Advanced: Multiple Component Instances

```yaml
gm:
  components:
    observe:
      timeline_active:
        built_in: timeline_every_turn
        params:
          history_size: 50
      timeline_passive:
        built_in: timeline_every_turn
        params:
          history_size: 10
      episode:
        built_in: episode_only
      entities:
        active:
          timeline_strategy: "pure_recsys"
        lurker:
          timeline_strategy: "follower_chronological"
        fixed_observer:
          # fixed_observer gets episode observation instead
          # (handled by explicit mapping in MultiFlowSMAct)
```

---

## Part 8: Data Flow

### Pre_act Execution Sequence

1. **Engine schedules entity**
   - Calls flow_engine or base_engine step()

2. **Engine calls GM.pre_act(action_spec)**
   - action_spec.output_type = MAKE_OBSERVATION

3. **SMAct (or MultiFlowSMAct) routes to components**
   - Simple: Call all components in order
   - Multi-flow: Determine entity's flow, call only flow-specific components

4. **Components execute in order:**
   - NextActing.pre_act(action_spec)
   - Observe.pre_act(action_spec) ← Returns observation text
   - Resolve.pre_act(action_spec) ← Prepared for next action parsing
   - Recommend.pre_act(action_spec) ← Updates backend

5. **Observation returned to agent**
   - Agent uses observation in LLM context

---

## Part 9: Backward Compatibility

### Simple → Multi-Flow Migration

Existing scenarios using simple mode can be migrated:

1. Set `enable_gm_multi_flow: true`
2. Change `gm.preset: base` → `gm.preset: shared_flow`
3. No other changes needed - falls back to single-instance mode

The MultiFlowGM detects if multi-instance component config exists; if not, it builds single instances and routes all agents to them.

### API Stability

- BaseGM.build() unchanged
- SMAct class remains for simple mode
- MultiFlowSMAct is new, doesn't interfere with existing code
- All factory functions are backward compatible

---

## Part 10: Future Extensions

### Possible Enhancements

1. **Per-flow next-acting policies**: Different flows might use different activity dynamics
2. **Per-flow resolve policies**: Different parsing strategies for different agent types
3. **Explicit component routing in config**: Instead of auto-detection, allow explicit flow → component mapping
4. **Recommendation federation**: Chain multiple recommendation components or systems
5. **Component inheritance**: Templates for component configs shared across flows

---

## Summary

The multi-flow architecture provides:

| Feature | Simple Mode | Multi-Flow Mode |
|---------|-------------|-----------------|
| Components per role | 1 | 1+ (with routing) |
| Per-flow customization | ❌ | ✅ (via multi-fields) |
| Multi-algorithm recommendations | ❌ | ✅ |
| Different observation types | ❌ | ✅ |
| Configuration complexity | Low | Medium |
| Execution overhead | Minimal | Low |
| Backward compatible | ✅ | ✅ |

Choose **simple mode** for most scenarios. Use **multi-flow** when agents need substantially different behaviors (e.g., active vs passive, fixed vs variable) or when running multi-algorithm recommendation experiments.
