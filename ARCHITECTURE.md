# Mastodon-Sim Architecture: Multi-Flow & Multi-GM Support

## Overview

The mastodon-sim architecture has been redesigned to support flexible, composable game master orchestration with explicit component field routing. This document describes the core abstractions and how they work together.

**Key Concepts:**
1. **FlowComponent**: Base class for components that route fields per-entity
2. **Multi-Field Routing**: Decorator-based field declarations for per-entity configuration
3. **Agent Flow Sequencing**: Order in which agents act within a GM (existing feature, unchanged)
4. **GM Sequencing**: Order in which multiple GMs execute (new, optional, advanced)
5. **Many-to-Many Routing**: Flexible agent → classes → GMs mapping

---

## 1. Component Layer: FlowComponent & Multi-Fields

### FlowComponent Mixin

`FlowComponent` is a mixin base class that adds optional per-entity field routing to any component.

```python
from mastodon_sim.environments.gm.components.base import FlowComponent
from mastodon_sim.environments.gm.components.decorators import multi_field

class MyObserveComponent(FlowComponent, MakeObservation):
    def __init__(self, model, player_names, sm_app, **kwargs):
        super().__init__()
        self._sm_app = sm_app

    @property
    @multi_field(str)
    def timeline_filter(self) -> str:
        """Get timeline filter - can vary per entity."""
        return self.get_field_for_entity('timeline_filter', default='all')
```

### Key Methods

- `set_multi_field_values(entity_field_map)`: Initialize entity-specific field values
  - Called at GM initialization time
  - Maps entity_name → {field_name: field_value}

- `get_field_for_entity(field_name, entity_name, default=None)`: Retrieve field value
  - Used within component methods
  - Returns per-entity value or default

- `has_multi_fields()`: Check if component has declared multi-fields
- `get_multi_fields()`: Retrieve metadata about multi-fields

### Multi-Field Declaration

Fields are declared with the `@multi_field(type)` decorator:

```python
@property
@multi_field(str)  # Applied AFTER @property
def my_field(self) -> str:
    """This field can vary per entity."""
    return self.get_field_for_entity('my_field', default='default_value')
```

**Key Points:**
- Decorator is purely declarative
- Zero overhead for non-multi-field components
- Backward compatible: existing components unchanged
- Decorator order: `@property` then `@multi_field`

### Backward Compatibility

- Existing components **do not inherit from FlowComponent** - no changes needed
- Multi-field system is **opt-in** via explicit `@multi_field` decorator
- If `set_multi_field_values()` not called, components work normally
- Default GM behavior unchanged when `gm.gm_configs` not specified

---

## 2. GM Initialization Layer: Multi-Field Setup

### Configuration Format

Entity-specific component fields are configured in YAML under `entities`:

```yaml
gm:
  components:
    observe:
      built_in: timeline_every_turn
      entities:
        alice:
          timeline_filter: "trusted"
        bob:
          timeline_filter: "all"
    resolve:
      built_in: parsed_action
      entities:
        alice:
          action_parser: "strict"
```

### Initialization Flow

1. **Factory parses config** from `gm.components[component_type].entities`
2. **Builds entity→field mapping** as dict[entity_name][field_name] = field_value
3. **Calls component.set_multi_field_values(mapping)**
4. **Component stores mapping** for runtime retrieval

### No Workflow Change

The existing component calling convention is **unchanged**:
- Components still receive entity_name via action_spec
- Existing code flow remains identical
- Multi-field values accessed via `get_field_for_entity()` when needed

---

## 3. Agent Flow Sequencing (Existing Feature)

### Purpose

Controls the order in which agents act **within a single GM** during an episode.

### Configuration

```yaml
engine:
  flow_routing:
    flow_order: [fixed_pre, default, analysis]  # Sequence of flows
    entity_to_flow:
      alice: "fixed_pre"          # Per-entity override
      bob: "default"
```

### Execution Model

1. **Next acting component** selects which agents act this step
2. **Engine groups actors** by their assigned flow (via entity_to_flow or entity_action_flows)
3. **Flows execute sequentially**:
   - All agents in flow_order[0] act in parallel
   - Then all agents in flow_order[1] act in parallel
   - And so on...
4. **Within each flow**, agents act in parallel (thread pool)

### Properties

- **Per-entity assignment**: Each agent has `entity_to_flow: agent_class → flow_name`
- **Flow groups**: All agents in same flow act together in parallel
- **Sequential flows**: Different flow groups never run simultaneously
- **Scope**: This is **within a single GM**

---

## 4. GM Sequencing (New, Optional, Advanced)

### Purpose

Controls the order in which **multiple GMs execute** when using advanced multi-GM mode.

### When Enabled

Advanced multi-GM mode is activated by specifying both:
1. `gm.gm_configs: {...}` - Per-GM configurations
2. `gm.gm_sequence: [...]` - Order of GM execution

### Configuration

```yaml
gm:
  agent_classes:  # Maps agents to class(es)
    alice: ["human", "verified"]
    bob: ["bot"]

  class_to_gms:   # Maps classes to GM(s) - many-to-many
    human: ["gm_human"]
    verified: ["gm_analysis"]
    bot: ["gm_bot"]

  gm_sequence: ["gm_human", "gm_bot", "gm_analysis"]  # Execution order

  gm_configs:     # Per-GM configuration
    gm_human:
      name: "human_gm"
      components:
        observe: {...}
        resolve: {...}
    gm_bot:
      name: "bot_gm"
      components: {...}
    gm_analysis:
      name: "analysis_gm"
      components: {...}
```

### Execution Model

1. **Engine reads gm_sequence** (e.g., [gm_human, gm_bot, gm_analysis])
2. **For each GM in sequence**:
   a. Determine agents assigned to this GM (via agent_classes → class_to_gms)
   b. Call `gm.get_active_agents()` to find which agents act this step
   c. Within this GM, execute normal agent flow sequencing (flow_order)
   d. Wait for all agents to complete
3. **Move to next GM** and repeat

### Properties

- **Sequential execution**: GMs never run in parallel
- **Per-GM agent isolation**: Each GM works only with its assigned agents
- **Composite routing**: Agent → Classes → GMs (many-to-many)
- **Flow within GMs**: Each GM still uses its own flow_order
- **Conflict avoidance**: If agents belong to multiple GMs, GMs serialize

### Advanced Flexibility

```yaml
# Example: Same agent class goes to multiple GMs
class_to_gms:
  human:
    - "gm_main"       # Main decision-making
    - "gm_analysis"   # Parallel analysis

gm_sequence:
  - "gm_main"         # Humans make decisions first
  - "gm_analysis"     # Then evaluation GMs run
```

---

## 5. Orchestration: How It All Works Together

### Simple Mode (Default)

```
Config: No gm.gm_configs or gm.gm_sequence
Result: One GM handles all agents

flow:
  Engine step loop:
    → Call next_acting component (selects agents)
    → Group agents by flow (entity_to_flow)
    → Execute flow_pre agents (parallel)
    → Execute default agents (parallel)
    → Execute analysis agents (parallel)
    → Repeat
```

### Advanced Mode

```
Config: Has gm.gm_configs AND gm.gm_sequence
Result: Multiple GMs execute in sequence, each with flow groups

flow:
  Engine step loop:
    → For gm in gm_sequence:
      → Get agents for this GM (via agent_classes → class_to_gms)
      → Call gm.next_acting (select agents within this GM)
      → Group by flow (entity_to_flow)
      → Execute flow_pre agents (parallel within GM)
      → Execute default agents (parallel within GM)
      → Execute analysis agents (parallel within GM)
    → Repeat for next GM
```

### Configuration Layers

```
┌─────────────────────────────────────────────────┐
│ gm_sequence                                      │ ← GM execution order
│ (controls which GMs run, in what order)         │
├─────────────────────────────────────────────────┤
│ gm [...] → agents                               │ ← Agent assignment
│ (agent_classes + class_to_gms)                  │
├─────────────────────────────────────────────────┤
│ flow_order                                      │ ← Flow sequencing within GM
│ entity_to_flow                                  │ ← Agent to flow assignment
├─────────────────────────────────────────────────┤
│ Multi-field values                              │ ← Component field values
│ (entities[name].components[type].field_name)   │
└─────────────────────────────────────────────────┘
```

---

## 6. Real-World Example

### Scenario: Bot Detection & Analysis System

```yaml
gm:
  # Step 1: Classify agents into groups
  agent_classes:
    alice: ["human"]
    bob: ["human", "active"]   # Can belong to multiple classes
    charlie: ["bot"]
    dave: ["bot", "suspicious"]

  # Step 2: Route classes to GMs
  class_to_gms:
    human: ["gm_social"]         # Humans use social GM
    active: ["gm_analysis"]      # Active users also use analysis GM
    bot: ["gm_detection"]        # Bots use detection GM
    suspicious: ["gm_audit"]     # Suspicious bots also audited

  # Step 3: Execution order
  gm_sequence:
    - "gm_social"        # Human agents act first
    - "gm_detection"     # Then bot detection
    - "gm_analysis"      # Analyze active users
    - "gm_audit"         # Finally audit suspicious bots

  # Step 4: Per-GM configuration
  gm_configs:
    gm_social:
      name: "social_gm"
      components:
        observe:
          built_in: timeline_every_turn
          entities:
            alice:
              timeline_filter: "verified_only"
            bob:
              timeline_filter: "all"

    gm_detection:
      name: "detection_gm"
      components:
        observe:
          built_in: timeline_every_turn
          entities:
            charlie:
              timeline_filter: "behavior_anomaly"

# Within each GM: agent flow sequencing still applies
engine:
  flow_routing:
    flow_order: [pre_analysis, respond, post_analysis]
    entity_to_flow:
      alice: "pre_analysis"
      bob: "respond"
      charlie: "post_analysis"
```

**Execution:**
1. GM Social runs (alice, bob) with their flow sequencing
2. GM Detection runs (charlie) with detection logic
3. GM Analysis runs (bob) for deeper inspection
4. GM Audit runs (dave) for suspicious activity

---

## 7. API Reference

### FlowComponent Methods

```python
class FlowComponent:
    def set_multi_field_values(self, entity_field_map: dict[str, dict[str, Any]]) -> None:
        """Initialize entity-specific field values from GM config."""

    def get_field_for_entity(self, field_name: str, entity_name: str | None = None,
                             default: Any = None) -> Any:
        """Retrieve field value for entity, with routing."""

    def has_multi_fields(self) -> bool:
        """Check if component has multi-field declarations."""

    def get_multi_fields(self) -> dict[str, type]:
        """Get metadata about multi-fields."""
```

### GameMasterFactory Methods

```python
class GameMasterFactory:
    def __init__(self, gm_config, agent_names,
                 agent_to_classes=None, class_to_gms=None):
        """Create factory with flexible routing configuration."""

    def build(self, model, memory_bank, entities)
             -> tuple[EntityAgentWithLogging, ...]:
        """Build GM instance(s) based on configuration."""

    def get_gm_instance(self, gm_name: str) -> EntityAgentWithLogging | None:
        """Get specific GM instance (after build)."""

    def get_all_gm_instances(self) -> dict[str, EntityAgentWithLogging]:
        """Get all built GM instances."""

    def get_default_gm(self) -> EntityAgentWithLogging | None:
        """Get single/default GM (if in single-GM mode)."""
```

---

## 8. Configuration Reference

### Top-Level GM Config

```yaml
gm:
  # Single-GM mode (default)
  name: "my_gm"
  components: {...}

  # Advanced multi-GM mode (optional)
  agent_classes:                    # Maps agent_name → [class_names]
    agent1: ["class1"]
    agent2: ["class1", "class2"]

  class_to_gms:                     # Maps class_name → [gm_names]
    class1: ["gm1", "gm2"]
    class2: ["gm2"]

  gm_sequence:                      # Execution order
    - "gm1"
    - "gm2"

  gm_configs:                       # Per-GM configuration
    gm1:
      name: "gm1"
      components:
        observe: {...}
        resolve: {...}
```

### Component Config with Multi-Fields

```yaml
gm:
  components:
    observe:
      built_in: timeline_every_turn
      entities:                    # Entity-specific field values
        alice:
          timeline_filter: "trusted"
          timeline_count: 10
        bob:
          timeline_filter: "all"
          timeline_count: 20
```

### Agent Flow Config (Within GM)

```yaml
engine:
  flow_routing:
    flow_order: [fixed_pre, default, analysis]
    entity_to_flow:
      alice: "fixed_pre"
      bob: "default"
```

---

## 9. Implementation Notes

### No Breaking Changes

- All existing components continue to work unchanged
- Single-GM mode is default and identical to previous behavior
- Multi-GM and multi-field features are opt-in
- Backward compatible with legacy "class_mapping" config format

### Performance Considerations

- Multi-field value retrieval is O(1) via hash lookup
- No overhead for components not using multi-fields
- GM sequencing eliminates parallel bugs (trades parallelism for safety)
- Within-GM agent flow sequencing unchanged (still parallel)

### Thread Safety

- GMs execute sequentially (no multi-threading between GMs)
- Within each GM, agents in same flow run parallel (existing thread pool)
- No locks needed due to sequential GM execution

---

## 10. Migration Guide

### From Simple to Advanced Mode

**Before:**
```yaml
gm:
  name: "my_gm"
  components:
    observe:
      built_in: timeline_every_turn
```

**After:**
```yaml
gm:
  agent_classes:
    alice: ["human"]
    bob: ["bot"]

  class_to_gms:
    human: ["gm_human"]
    bot: ["gm_bot"]

  gm_sequence: ["gm_human", "gm_bot"]

  gm_configs:
    gm_human:
      name: "human_gm"
      components:
        observe:
          built_in: timeline_every_turn
    gm_bot:
      name: "bot_gm"
      components:
        observe:
          built_in: timeline_every_turn
```

### Adding Multi-Fields to Component

**Before:**
```python
class MyObserve(MakeObservation):
    def __init__(self, model, player_names, sm_app):
        super().__init__(model=model, ...)
        self._filter = "all"
```

**After:**
```python
from mastodon_sim.environments.gm.components.base import FlowComponent
from mastodon_sim.environments.gm.components.decorators import multi_field

class MyObserve(FlowComponent, MakeObservation):
    def __init__(self, model, player_names, sm_app):
        super().__init__()
        super().__init__(model=model, ...)

    @property
    @multi_field(str)
    def timeline_filter(self) -> str:
        return self.get_field_for_entity('timeline_filter', default='all')
```

**In YAML:**
```yaml
gm:
  components:
    observe:
      entities:
        alice:
          timeline_filter: "trusted"
```

---

## 11. Common Questions

**Q: Can agents belong to multiple GMs?**
A: Yes! Via `agent_classes` and `class_to_gms`. Agent can have multiple classes, each class can route to multiple GMs.

**Q: Do GMs run in parallel?**
A: No, they execute sequentially per `gm_sequence`. This avoids state conflicts.

**Q: Can I use multi-fields without multi-GM?**
A: Yes, multi-fields work in single-GM mode. Set `FlowComponent` on your component and add `entities[name]` config.

**Q: What happens if an agent doesn't have an assigned class?**
A: It defaults to "default" class, which itself defaults to "default" GM.

**Q: Do I need to change existing components?**
A: No! Existing components continue working. Multi-field system is purely opt-in.
