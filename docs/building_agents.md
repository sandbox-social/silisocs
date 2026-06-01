# Building Agents

For API-level contracts for runtime agents and builder hooks, see
[Simulation Extensibility API](simulation_extensibility_api.md).

There are two ways to produce agent specs for your simulation:

1. **YAML Pipeline** (recommended for most cases) — define agent classes
   declaratively in your scenario YAML
2. **Custom Builder** — write a Python class for full programmatic control

The default `PersonaPipelineAgentBuilder` reads the YAML pipeline config. If a
scenario needs programmatic logic, set `agents.builder.class_path` explicitly.
Builders return `AgentConfig` records; the runtime still owns live agent
construction and model injection.

Builder output contract:

- return `list[AgentConfig]`;
- set `class_path` to the runtime agent class;
- put constructor kwargs under `params`;
- do not construct live `Agent` instances;
- do not attach a `LanguageModel`; runtime assembly injects it.

---

## Method 1: YAML Pipeline (Declarative)

Define a `persona_pipeline` section in your scenario YAML. The builder
reads class definitions, loads data from various sources, and maps fields
to agent parameters — no Python code needed.

### Minimal Example

```yaml
# scenarios/my_scenario/conf/agents/default.yaml
builder:
  class_path: null
  params: {}

persona_pipeline:
  defaults:
    params:
      scenario_context: "A community discussion platform."
    shared_memories:
      - "Users are active on a social media platform."
  classes:
    user:
      count: 2
      class_path: silisocs.agents.native.NativeAgent
      data:
        source: inline
        records:
          - name: Alex
            persona: Alex follows local policy and posts practical updates.
          - name: Blair
            persona: Blair follows technology news and likes concise debates.
      field_map:
        name: name
        context: persona
```

### Data Sources

| Source | Config keys | Description |
|--------|------------|-------------|
| `local_json` | `path` | Local JSON file (array of objects) |
| `inline` | `records` | Records defined directly in YAML |
| `config_path` | `path` | Dot-path into another config section (e.g. `candidates`) |
| `hf_dataset` | `dataset`, `split`, `subset` | Hugging Face dataset; requires `silisocs[hf]` |

### Field Mapping

Map data record fields to agent parameters:

```yaml
field_map:
  name: full_name              # Simple dot-path into the record
  context: persona             # Maps "persona" field → agent "context"
  bio: "{role}\n{interests}"   # Template combining multiple fields
```

`context` is required. The final `AgentConfig` records must also contain a
unique `name`; Silisocs uses agent names as runtime identities for observations,
backend state, flows, probes, logs, and checkpoints. Most data sources should map
`field_map.name` explicitly. The default builder also derives names for the
known `nvidia/Nemotron-Personas-USA` persona dataset, and classes can opt into
the same behavior with `derive_name_from_context: true`. Custom builders may
derive names however they need, but runtime construction rejects unnamed or
duplicate specs.

Supported target fields: `name`, `context`, `style`, `goal`, `bio`, `seed_post`.

### Defaults and Overrides

Pipeline-level defaults apply to all classes. Per-class settings override:

```yaml
persona_pipeline:
  defaults:
    params:
      goal: "Have a productive discussion."
    field_map:
      context: persona
    shared_memories:
      - "A shared memory for all agents."
  classes:
    admin:
      count: 2
      params:
        goal: "Moderate the discussion."  # overrides default
      shared_memories:
        - "Admins have moderation powers."  # appended to defaults
```

---

## Method 2: Custom Builder (Programmatic)

For scenarios that need logic beyond what YAML can express, create an importable
Python builder class and point `agents.builder.class_path` at it.

### Config Slot

```yaml
agents:
  builder:
    class_path: scenarios.my_scenario.builders.MyScenarioAgentBuilder
    params:
      cohort: pilot
```

`class_path: null` uses `PersonaPipelineAgentBuilder`.

### Example

```python
# scenarios/my_scenario/builders.py
from silisocs.runtime.construction.agent_builders import AgentBuilder
from silisocs.runtime.construction.specs import AgentConfig

class MyScenarioAgentBuilder(AgentBuilder):
    def build_agent_configs(self) -> list[AgentConfig]:
        agents = []
        for i in range(3):
            agents.append(AgentConfig(
                class_path="silisocs.agents.native.NativeAgent",
                params={
                    "name": f"Participant {i}",
                    "context": "A participant in the simulation.",
                    "sim_role_name": "participant",
                    "style": "",
                    "seed_post": "",
                    "bio": "",
                    "goal": None,
                },
            ))
        return agents
```

### Mixing Both Methods

A custom builder can call `PersonaPipelineAgentBuilder` internally for the
ordinary YAML-defined cohorts, then append custom `AgentConfig` records for
special cases. That keeps bespoke logic explicit without hiding it behind
scenario-name auto-detection.

### Available Helpers in PersonaPipelineAgentBuilder

These helpers are useful when a custom builder wants to reuse the default
persona-pipeline behavior. If your builder only needs ordinary records and field
mapping, prefer instantiating `PersonaPipelineAgentBuilder` and appending to its
result.

| Method | Description |
|--------|-------------|
| `self._resolve_file_path(path)` | Resolve path relative to scenario dir |
| `self.load_news_data(news_file)` | Load news headlines from JSON |
| `self._load_memories(value)` | Load memories from string, file path, or list |
| `self._coerce_text(value)` | Normalize any value to a trimmed string |
| `self._normalize_memories(value)` | Normalize to `list[str]` |
| `self._extract_path(record, "a.b.c")` | Extract nested value from dict |
| `self._resolve_source(record, spec)` | Resolve dot-path or `{template}` |
| `self._derive_name(context, words=2)` | Derive a compact name when a source intentionally has persona text but no name field |

---

## Custom Agent Runtime Shape

All runtime agents are constructed with a `LanguageModel`. Custom agents should
keep `act()` responsible for deciding what context the agent needs, then use the
protected `_call_model(context, action_spec)` helper to route the requested
output type to the correct model method.

```python
from silisocs.agents.base_agent import Agent
from silisocs.runtime.language_models import LanguageModel
from silisocs.runtime.types import ActionOutput, ActionSpec


class JournalAgent(Agent):
    def __init__(self, *, name: str, model: LanguageModel, persona: str) -> None:
        super().__init__(model)
        self._name = name
        self._persona = persona
        self._observations: list[str] = []

    @property
    def name(self) -> str:
        return self._name

    def observe(self, observation: str) -> None:
        if observation.strip():
            self._observations.append(observation.strip())

    def act(self, action_spec: ActionSpec) -> ActionOutput:
        context = "\n\n".join(
            [
                f"Persona: {self._persona}",
                "Recent observations:",
                "\n".join(self._observations[-5:]),
            ]
        )
        return self._call_model(context, action_spec)
```

`_call_model()` handles text, choices, floats, tool calls, structured outputs,
and skip actions. It fails loudly when a spec is missing required typed data,
such as `extra_args["tools"]` for tool calls or `extra_args["schema"]` for
structured outputs.

## Per-Class LLM Models

Assign different LLM models per agent class:

```yaml
classes:
  voter:
    count: 100
    model: gpt-4o-mini      # Cheaper model for background agents
  candidate:
    count: 2
    model: gpt-4o            # Better model for key agents
```

Or per-agent via field mapping (your data source must include a model field):

```yaml
    field_map:
      name: name
      context: persona
      model: model_name          # Maps data field → per-agent model
```

See [Usage Overview](usage.md#per-agent-llm-models) for the full priority chain.

---

## Related

- [Memory Initialization](memory_initialization.md) — How agents get their starting knowledge
- [Configuration Reference](configuration.md) — Full persona_pipeline config options
- [Election Walkthrough](tutorials/election.md) — Real-world multi-class scenario
- [Usage Overview](usage.md#developer-customization-guide) — Engine/GM/backend customization map
