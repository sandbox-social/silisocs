# Multi-Flow & Multi-GM Architecture

Advanced composition model for routing agent flows through multiple native game
masters.

**For**: Developers extending the framework with complex orchestration needs.
**See also**: [Environment Layer](environment_layer.md), [Configuration Reference](configuration.md#gm-components)

---

## Overview

The multi-GM architecture provides:

1. **Flow-to-GM routing**: each configured agent flow maps to one or more GMs.
2. **Per-GM backends**: each GM owns exactly one backend for this release.
3. **Per-GM components**: each GM defines its own typed component slots.
4. **Sequential flow chains**: a flow can pass through multiple GMs in sequence.

### When to Use

- Simulating different backend worlds in the same run.
- Routing agent cohorts through different decision environments.
- Multi-stage pipelines such as public action → audit → moderation.

### When NOT to Use

- Simple single-backend scenario: use one GM.
- Agents only need sequencing: use a single GM with `FlowStepStrategy`.

---

## Architecture Model

### Layer Stack

```
┌─────────────────────────────────────────────────────┐
│ Flow → GM chains (env.gm_orchestration.flow_bindings)│
│ Agents are grouped by flow, then routed through GMs   │
├─────────────────────────────────────────────────────┤
│ Agent Flow Sequencing (flow_order, agent_to_flow)   │ ← Agent grouping within GM
│ Groups agents by flow, executes sequentially        │
├─────────────────────────────────────────────────────┤
│ GM component slots                                  │ ← initialize/next/observe/resolve/update
│ Components are configured per GM                    │
└─────────────────────────────────────────────────────┘
```

### Execution Model

**Single-GM Mode (Default):**
```
Engine Step Loop:
  → next_acting selects agents
  → group by flow
  → execute flows sequentially (within each, agents parallel)
  → next step
```

**Multi-GM Mode (Advanced):**
```
Engine Step Loop:
  → group agents by flow
  → for each flow in flow_order:
    → for each GM in that flow's configured GM chain:
      → gm.update(...)
      → gm.acting_agents(flow_agents)
      → each active agent observes/acts/resolves through that GM
  → next step
```

**Key property**: a GM owns one backend. Multi-backend simulations use multiple
GMs, each with its own backend and components.

---

## Configuration

### Simplest: Single GM

```yaml
gm:
  name: default_gm
  backend:
    type: twitter_like
    class_path: null
    params: {}
    enabled_actions: null
  components:
    initialize:
      built_in: social_media
      params: {graph: {}}
    next_acting:
      built_in: activity_probability
    observe:
      built_in: timeline_every_turn
    resolve:
      built_in: parsed_action
    update:
      built_in: social_recommendation
    action_prompt:
      built_in: default
```

All agents use this GM unless the engine step strategy routes by flow.

### Multi-GM: Different GMs for Different Flows

```yaml
gm_orchestration:
  gms:
    - name: social_gm
      sequence: 0
      backend:
        type: twitter_like
        class_path: null
        params: {}
        enabled_actions: null
      components:
        initialize: {built_in: social_media, params: {graph: {}}}
        next_acting: {built_in: activity_probability, params: {}}
        observe: {built_in: timeline_every_turn, params: {}}
        resolve: {built_in: tool_calling, params: {}}
        update: {built_in: social_recommendation, params: {}}
        action_prompt: {built_in: default, params: {}}

    - name: market_gm
      sequence: 1
      backend:
        type: resource_market
        class_path: null
        params: {}
        enabled_actions: null
      components:
        initialize: {built_in: app_initialize, params: {}}
        next_acting: {built_in: fixed_order, params: {}}
        observe: {built_in: app_observation, params: {}}
        resolve: {built_in: tool_calling, params: {}}
        update: {built_in: disabled, params: {}}
        action_prompt: {built_in: default, params: {}}

  flow_bindings:
    flow_to_gms:
      social: [social_gm]
      market: [market_gm]
```

**Behavior:**
- agents with flow `social` act through `social_gm`;
- agents with flow `market` act through `market_gm`;
- each GM initializes and updates its own backend.

Within each GM:
```yaml
engine:
  step:
    built_in: multi_gm
    params:
      flow_order: [pre_analysis, default, post_analysis]
      agent_to_flow:
        alice: "pre_analysis"
        bob: "default"
        charlie: "post_analysis"
```

### Per-Flow Component Configuration

Configure different component behavior per flow:

```yaml
gm:
  components:
    observe:
      instances:
        active_feed:
          built_in: timeline_every_turn
          params:
            timeline_mode: pure_recsys
        default_feed:
          built_in: timeline_every_turn
          params:
            timeline_mode: follower_chronological
        fixed_pre_episode:
          built_in: episode_only
      flow_map:
        active: active_feed
        default: default_feed
        fixed_pre: fixed_pre_episode
```

**Implementation:**
1. Each component instance is a normal slot component.
2. The GM parses `flow_map` and chooses the component instance for the agent's flow.
3. Custom components do not need flow-aware code.

Example component:

```python
from silisocs.environments.gm.components.base import ObservationComponent

class CustomObserve(ObservationComponent):
  def __init__(self, *, source: str):
    self.source = source

  def make_observation(self, agent_name: str) -> str:
    return f"{agent_name} sees {self.source}"
```

---

## Implementation Details

### Runtime Construction

`silisocs.runtime.construction.game_masters.build_game_masters(...)` converts
Hydra config into native `GameMasterConfig` specs. In multi-GM mode every item
under `env.gm_orchestration.gms` must define its own `backend` and
`components`; defaults are not inherited into orchestrated GMs.

### MultiGMRuntimeEngine

`MultiGMRuntimeEngine` uses `MultiGMStepStrategy`. It reads
`flow_to_gms` from the first GM's routing metadata, groups agents by flow, and
executes each flow through its configured GM chain.

### Routed Slot Components

Located: `src/silisocs/environments/gm/components/base.py`

Flow-routed GMs can route every slot: `initialize`, `next_acting`,
`action_prompt`, `observe`, `resolve`, and `update`. The component APIs remain
the same as the single-flow APIs; routing is a GM responsibility.

---

## Testing

```bash
uv run pytest tests/test_multi_gm_runtime_engine.py -v
uv run pytest tests/test_runner_processing_mode.py::test_multi_gm_specs_can_use_distinct_backends -v
```

## Performance Considerations

- **Flow chains are sequential**: a flow passes through its configured GMs in order.
- **Within-batch agent turns can run in parallel**: agents selected by the same
  GM and flow are isolated as separate turns.
- **Backend state is per GM**: this release does not share one live backend
  object across multiple GMs.

---

## FAQ

**Q: Can agents move between GMs during simulation?**
A: Flow assignment is configured before the run. Dynamic reassignment requires a
custom engine strategy.

**Q: Can GMs share state?**
A: Not as a shared backend object in this release. Use one backend per GM, or
persist shared state externally in custom components.

**Q: Can I route slots by flow without multi-GM?**
A: Yes. A single GM can use `instances + flow_map` on any typed component slot.

**Q: Do I need to modify existing components?**
A: No. Flow routing is handled by the GM; components expose the same direct slot
methods.

---

## See Also

- [Environment Layer](environment_layer.md) — GM and engine extensibility
- [Configuration Reference](configuration.md#gm-components) — Full config schema
- Test files: `tests/test_*.py` for examples
