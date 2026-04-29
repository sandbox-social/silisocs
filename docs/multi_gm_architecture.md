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
│ Component Field Values (flows.<flow>.<field>)       │ ← Per-flow component config
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

### Per-Flow Component Configuration

Configure different component behavior per flow:

```yaml
gm:
  components:
    observe:
      built_in: timeline_every_turn
      flows:
        active:
          timeline_filter: "trusted_only"
          timeline_depth: 10
        default:
          timeline_filter: "all"
          timeline_depth: 20
        fixed_pre:
          timeline_filter: "trending"
          timeline_depth: 15
```

**Implementation:**
1. Component declares fields in `FLOW_FIELDS`
2. GM initialization parses `flows.*` config and calls `set_flow_field_values(...)`
3. At runtime, component calls `get_flow_field(field_name, flow_tag)`

Example component:

```python
from silisocs.environments.gm.components.base import FlowComponent

class CustomObserve(FlowComponent, MakeObservation):
  FLOW_FIELDS = {"timeline_filter": str}

  def __init__(self, ...):
    FlowComponent.__init__(self)
    MakeObservation.__init__(self, ...)
    self.current_flow_tag = "default"

  def pre_act(self, action_spec):
    # Use the field
    filter_value = self.get_flow_field("timeline_filter", self.current_flow_tag, "all")
    # ... rest of logic
```

---

## Implementation Details

### GameMasterFactory

Located: `src/silisocs/environments/gm/gm_factory.py`

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

### MultiGMRuntimeEngine

Located: `src/silisocs/engines/multi_gm.py`

Orchestrates multiple GMs:

```python
engine = MultiGMRuntimeEngine(...)

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

Located: `src/silisocs/environments/gm/components/base.py`

Mixin for per-flow field routing:

```python
class FlowComponent:
    def set_flow_field_values(self, flow_field_map: dict):
        """Initialize flow->field->value mapping."""

    def get_flow_field(self, field_name: str,
                       flow_tag: str | None = None,
                       default=None):
        """Retrieve field value for flow."""

    def has_flow_fields(self) -> bool:
        """Check if component declares any flow fields."""

    def get_flow_fields(self) -> dict:
        """Return metadata about flow-field declarations."""
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

Located: `tests/test_gm_factory.py`, `tests/test_multi_gm_runtime_engine.py`, `tests/test_e2e_multi_gm.py`

### Unit Tests

```bash
uv run pytest tests/test_gm_factory.py -v
uv run pytest tests/test_multi_gm_runtime_engine.py -v
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

## Migration Notes

Use the canonical multi-GM schema (`agent_classes`, `class_to_gms`, `gm_sequence`, `gm_configs`) for routing.

### Canonical Multi-GM Config

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
A: Yes. Multi-fields work in single-GM mode. Declare `FLOW_FIELDS` on the component and configure `flows.<flow_name>.<field_name>` in YAML.

**Q: Do I need to modify existing components?**
A: No. Multi-GM and multi-field systems are purely opt-in. Existing components work unchanged.

---

## See Also

- [Environment Layer](environment_layer.md) — GM and engine extensibility
- [Configuration Reference](configuration.md#engine-and-gm-configuration) — Full config schema
- Test files: `tests/test_*.py` for examples
