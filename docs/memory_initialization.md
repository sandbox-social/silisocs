# Agent Initialization

The Engine runs agent initialization before Game Master and simulation
initialization. This phase prepares agent-owned state such as configured raw
memories or formative memories, then calls each agent's `initialize(...)` hook
with a typed `AgentInitializationContext`.

There is no initializer Game Master and no GM memory bank in the native runtime.

## Built-Ins

| Built-in | Config | Behavior |
|----------|--------|----------|
| `default` / `raw_memory` | `sim.initialization.agents.built_in: raw_memory` | Inject configured shared and per-agent memories. |
| `formative_memory` | `sim.initialization.agents.built_in: formative_memory` | Generate per-agent formative memories with the configured model, then inject them. |
| `none` | `sim.initialization.agents.built_in: none` | Skip agent initialization. |

```yaml
sim:
  initialization:
    agents:
      built_in: raw_memory
      class_path: null
      params: {}
```

## Custom Initializers

Subclass `AgentInitializer` and implement `initialize(...)`:

```python
from silisocs.initialization.agents import AgentInitializer


class MyAgentInitializer(AgentInitializer):
    def initialize(self, *, agents, model, context):
        for agent in agents:
            agent.initialize({"memories": [f"Welcome, {agent.name}."]})
```

Then configure it:

```yaml
sim:
  initialization:
    agents:
      class_path: my_package.my_module.MyAgentInitializer
      params: {}
```

## Related

- [Building Agents](building_agents.md)
- [Configuration Reference](configuration.md)
- [Usage Overview](usage.md#memory-initialization)
