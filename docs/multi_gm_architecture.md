# Multi-Flow & Multi-GM Architecture

Advanced composition model for routing agents to multiple game masters with explicit flow sequencing and per-entity component field customization.

**For**: Developers extending the framework with complex orchestration needs.
**See also**: [Environment Layer](environment_layer.md), [Configuration Reference](configuration.md#engine-and-gm-configuration)

---

## Overview

The multi-GM architecture provides:

1. **Many-to-Many Agent-to-GM Routing**: Agents belong to multiple classes; classes route to multiple GMs
2. **Separate Flow Layers**: Agent flow sequencing (within each GM) distinct from GM sequencing (orchestration)
3. **Per-Entity Component Configuration**: Component field values customized per agent via YAML
4. **Sequential GM Execution**: GMs execute one at a time (safe for shared agents); flows within each GM still run in parallel

### When to Use

- Simulating different decision-making processes for different agent cohorts
- Bot/human detection where agents route differently based on classification
- Multi-stage analysis pipelines (main decision → deep analysis → audit)
- Complex multi-level ecosystems with different rule sets per subgroup

### When NOT to Use

- Simple single-platform scenario: use default GM with flow routing only
- Agents don't need different logic: use agent classes + flow sequencing

---

## Architecture Model

### Layer Stack

```
┌─────────────────────────────────────────────────────┐
│ GM Sequencing (gm_sequence)                         │ ← Which GMs execute, in order
│ Controls external orchestration of multiple GMs     │
├─────────────────────────────────────────────────────┤
│ Agent → Classes → GMs (agent_classes, class_to_gms)│ ← Flexible many-to-many routing
│ Agents assigned to one or more GMs via classes     │
├─────────────────────────────────────────────────────┤
│ Agent Flow Sequencing (flow_order, entity_to_flow)  │ ← Agent grouping within GM
│ Groups agents by flow, executes sequentially        │
├─────────────────────────────────────────────────────┤
│ Component Field Values (entities.*.components.*.*)  │ ← Per-entity component config
│ Custom values for component behavior                │
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
  → for each GM in gm_sequence:
    → get agents assigned to this GM
    → next_acting (within GM) selects agents
    → group by flow
    → execute flows sequentially (within each, agents parallel)
  → next GM in sequence
  → repeat
```

**Key property**: GMs execute sequentially (no parallelism between GMs); flows within each GM run sequentially; agents within each flow run in parallel.

---

## Configuration

### Simplest: Single GM (Backward Compatible)

```yaml
gm:
  name: "default_gm"
  components:
    observe:
      built_in: timeline_every_turn
    resolve:
      built_in: parsed_action
```

All agents → one GM. Existing behavior unchanged.

### Multi-Class: Different GMs for Different Agent Types

```yaml
gm:
  agent_classes:
    alice: "human"
    bob: "bot"
    charlie: "bot"

  class_to_gms:
    human: "gm_social"
    bot: "gm_detection"

  gm_sequence: ["gm_social", "gm_detection"]

  gm_configs:
    gm_social:
      name: "human_decision_gm"
      components:
        observe: {built_in: timeline_every_turn}
        resolve: {built_in: parsed_action}

    gm_detection:
      name: "bot_detection_gm"
      components:
        observe: {built_in: timeline_every_turn}
        resolve: {built_in: parsed_action}
```

**Behavior:**
- alice uses gm_social
- bob and charlie use gm_detection
- gm_social executes first, then gm_detection

### Advanced: Multi-Class Per Agent + Multi-GM Per Class

```yaml
gm:
  agent_classes:
    alice: ["human", "verified"]        # alice gets 2 classes
    bob: ["bot", "suspicious"]          # bob gets 2 classes
    charlie: "bot"                      # charlie gets 1 class

  class_to_gms:
    human: ["gm_social"]
    verified: ["gm_analysis"]           # separate GM
    bot: ["gm_detection"]
    suspicious: ["gm_audit"]            # additional GM

  gm_sequence: ["gm_social", "gm_detection", "gm_analysis", "gm_audit"]

  gm_configs:
    gm_social: {...}
    gm_detection: {...}
    gm_analysis: {...}
    gm_audit: {...}
```

**Behavior:**
- alice (human + verified) → gm_social, then gm_analysis
- bob (bot + suspicious) → gm_detection, then gm_audit
- charlie (bot) → gm_detection

Within each GM:
```yaml
engine:
  flow_routing:
    flow_order: [pre_analysis, default, post_analysis]
    entity_to_flow:
      alice: "pre_analysis"
      bob: "default"
      charlie: "post_analysis"
```

### Per-Entity Component Configuration

Configure different component behavior per agent:

```yaml
gm:
  components:
    observe:
      built_in: timeline_every_turn
      entities:                          # ← NEW: per-entity config
        alice:
          timeline_filter: "trusted_only"
          timeline_depth: 10
        bob:
          timeline_filter: "all"
          timeline_depth: 20
        charlie:
          timeline_filter: "trending"
          timeline_depth: 15
```

**Implementation:**
1. Component declares field as `@multi_field(type)`
2. GM initialization parses `entities.*.components.*.*` config
3. At runtime, component calls `get_field_for_entity(field_name, entity_name)`

Example component:

```python
from mastodon_sim.environments.gm.components.base import FlowComponent
from mastodon_sim.environments.gm.components.decorators import multi_field

class CustomObserve(FlowComponent, MakeObservation):
    def __init__(self, ...):
        super().__init__()
        super().__init__(...)  # parent class init

    @property
    @multi_field(str)  # decorator AFTER @property
    def timeline_filter(self) -> str:
        """Get timeline filter - varies per entity."""
        return self.get_field_for_entity('timeline_filter', default='all')

    def pre_act(self, action_spec):
        # Use the field
        filter_value = self.timeline_filter
        # ... rest of logic
```

---

## Implementation Details

### GameMasterFactory

Located: `src/mastodon_sim/environments/gm/gm_factory.py`

Routes agents to GM instances:

```python
factory = GameMasterFactory(
    gm_config=config_dict,
    agent_names=["alice", "bob"],
    agent_to_classes={"alice": ["human"], "bob": ["bot"]},
    class_to_gms={"human": ["gm_social"], "bot": ["gm_detection"]}
)

gm_instances = factory.build(model, memory_bank, entities)
```

**Methods:**
- `build(model, memory_bank, entities)` → tuple of GM instances
- `get_agent_gms(agent_name)` → list of GM names assigned to agent
- `get_gm_instance(name)` → specific GM instance

### MultiGMSocialMediaEngine

Located: `src/mastodon_sim/environments/engines/multi_gm_social_media.py`

Orchestrates multiple GMs:

```python
engine = MultiGMSocialMediaEngine(...)

# Reads gm_sequence + gm_configs from config
# Validates configuration
# Logs agent-to-GM mapping
# Executes GMs sequentially
```

**Methods:**
- `run_episode(environment, agents, game_masters, ...)` → episode transcript
- `get_agent_gms(agent_name)` → GMs for this agent
- `detect_gm_conflicts()` → agents in multiple GMs
- `validate_gm_sequence()` → check config validity

### FlowComponent Base Class

Located: `src/mastodon_sim/environments/gm/components/base.py`

Mixin for per-entity field routing:

```python
class FlowComponent:
    def set_multi_field_values(self, entity_field_map: dict):
        """Initialize entity→field→value mapping."""

    def get_field_for_entity(self, field_name: str,
                             entity_name: str = None,
                             default=None):
        """Retrieve field value for entity."""

    def has_multi_fields(self) -> bool:
        """Check if component declares any multi-fields."""

    def get_multi_fields(self) -> dict:
        """Return metadata about multi-field declarations."""
```

---

## Real-World Examples

### Example 1: Bot Detection + Analysis

Scenario: Detect bots during social interaction, then audit suspicious behavior.

```yaml
gm:
  agent_classes:
    user_0: "human"
    user_1: "human"
    bot_0: ["bot", "suspicious"]
    bot_1: "bot"

  class_to_gms:
    human: "gm_social"
    bot: "gm_detection"
    suspicious: "gm_audit"

  gm_sequence: ["gm_social", "gm_detection", "gm_audit"]

  gm_configs:
    gm_social:
      components:
        observe: {built_in: timeline_every_turn}
        resolve: {built_in: parsed_action}

    gm_detection:
      components:
        observe:
          built_in: timeline_every_turn
          params:
            check_bot_signals: true

    gm_audit:
      components:
        resolve:
          class_path: my_scenario.audit.AuditResolve
```

**Execution:**
1. gm_social: human users interact
2. gm_detection: all bots evaluated, suspicious ones flagged
3. gm_audit: suspicious bots undergo deeper investigation

### Example 2: Role-Based Decision Making

Scenario: Employees, managers, executives with different decision strategies.

```yaml
gm:
  agent_classes:
    emp_0: "employee"
    emp_1: "employee"
    mgr_0: ["manager", "decision_maker"]
    exec_0: ["executive", "decision_maker"]

  class_to_gms:
    employee: "gm_ops"
    manager: "gm_strategy"
    executive: "gm_board"
    decision_maker: "gm_governance"

  gm_sequence: ["gm_ops", "gm_strategy", "gm_board", "gm_governance"]
```

**Who acts where:**
- emp_0, emp_1 → gm_ops (operational decisions)
- mgr_0 → gm_strategy + gm_governance (strategy + oversight)
- exec_0 → gm_board + gm_governance (executive + oversight)

---

## Testing

Located: `tests/test_gm_factory.py`, `tests/test_multi_gm_social_media_engine.py`, `tests/test_e2e_multi_gm.py`

### Unit Tests

```bash
uv run pytest tests/test_gm_factory.py -v
uv run pytest tests/test_multi_gm_social_media_engine.py -v
```

### Integration Tests

```bash
uv run pytest tests/test_e2e_multi_gm.py -v
```

### End-to-End with LLM Reasoning (Optional)

```bash
export LLM_SERVER_URL=http://localhost:30000/v1
uv run pytest tests/test_e2e_multi_gm_llm.py -v -s
```

Requires Qwen 3.5-4B server on port 30000.

---

## Backward Compatibility

- ✅ Single-GM mode unchanged
- ✅ Existing components work without modification
- ✅ Multi-GM/multi-field features are opt-in
- ✅ Legacy `class_mapping` format still supported

### Migration from Legacy class_mapping

**Before:**
```yaml
gm:
  class_mapping:
    human: {name: "human_gm", ...}
    bot: {name: "bot_gm", ...}
  class_order: [human, bot]
```

**After:**
```yaml
gm:
  agent_classes: {alice: "human", bob: "bot"}
  class_to_gms: {human: "gm_human", bot: "gm_bot"}
  gm_sequence: ["gm_human", "gm_bot"]
  gm_configs:
    gm_human: {name: "human_gm", ...}
    gm_bot: {name: "bot_gm", ...}
```

---

## Performance Considerations

- **Sequential GM execution**: No parallelism between GMs (trades parallelism for safety)
- **Within-GM parallelism**: Flows sequence; agents within each flow run parallel (unchanged)
- **Multi-field lookup**: O(1) hash lookup, negligible overhead
- **Memory**: Minimal additional memory for routing metadata

---

## FAQ

**Q: Can agents move between GMs during simulation?**
A: No, assignment is fixed at initialization based on config. Consider using different scenarios for dynamic assignment.

**Q: Can GMs share state?**
A: No, each GM has independent state. Agents in multiple GMs don't share memories across GMs. Consider storing shared state in agent persistent storage or database.

**Q: What if an agent's classes don't route to any GM?**
A: Runtime error during GM initialization. Ensure all classes declared in `agent_classes` have entries in `class_to_gms`.

**Q: Can I use multi-fields without multi-GM?**
A: Yes. Multi-fields work in single-GM mode. Declare `@multi_field` on component, configure `entities.*.components.*.*` in YAML.

**Q: Do I need to modify existing components?**
A: No. Multi-GM and multi-field systems are purely opt-in. Existing components work unchanged.

---

## See Also

- [Environment Layer](environment_layer.md) — GM and engine extensibility
- [Configuration Reference](configuration.md#engine-and-gm-configuration) — Full config schema
- Test files: `tests/test_*.py` for examples
