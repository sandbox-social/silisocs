# Building Agents

For API-level contracts for runtime agents and builder hooks, see
[Simulation Extensibility API](simulation_extensibility_api.md).

!!! agent "The agent contract"
    Every agent (native, fixed, or custom) implements the same small interface:
    `name`, `observe(observation)`, and `act(action_spec)`. The platform decides
    *what* an agent perceives and *how* its output is resolved; the agent just
    reasons and responds.

There are two ways to produce agent specs for your simulation:

1. **YAML Pipeline** (recommended for most cases): define agent classes
   declaratively in your scenario YAML
2. **Custom Builder**: write a Python class for full programmatic control

The default `PersonaPipelineAgentBuilder` reads the YAML pipeline config. If a
world needs programmatic logic, set `agents.builder.class_path` explicitly.
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
to agent parameters: no Python code needed.

### Minimal Example

```yaml
# scenarios/my_world/conf/agents/default.yaml
builder:
  class_path: null
  params: {}

persona_pipeline:
  defaults:
    params:
      world_context: "A community discussion platform."
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
| `csv` | `path` | Local CSV file (one record per row) |
| `jsonl` | `path` | Local JSONL file (one record per line) |
| `hf_dataset` | `dataset`, `split`, `subset` | Hugging Face dataset; requires `silisocs[hf]` |
| a dotted path | (yours) | Custom source: a callable `(data_cfg, max_records=None) -> records` — e.g. `source: mypkg.sources.load_cohort` |

An unrecognized bare source name is a config error (it is never treated as a
file path).

### Field Mapping

Map data record fields to agent parameters:

```yaml
field_map:
  name: full_name              # Simple dot-path into the record
  context: persona             # Maps "persona" field → agent "context"
  bio: "{role}\n{interests}"   # Template combining multiple fields
```

`context` is required. The final `AgentConfig` records must also contain a
unique `name`; SiliSocS uses agent names as runtime identities for observations,
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

For worlds that need logic beyond what YAML can express, create an importable
Python builder class and point `agents.builder.class_path` at it.

### Config Slot

```yaml
agents:
  builder:
    class_path: scenarios.my_world.builders.MyWorldAgentBuilder
    params:
      cohort: pilot
```

`class_path: null` uses `PersonaPipelineAgentBuilder`. The runner puts the
project root on `sys.path`, so a builder living at
`scenarios/my_world/builders.py` resolves as
`scenarios.my_world.builders.<Class>` (implicit namespace packages — no
`__init__.py` needed) from a plain `silisocs` invocation and from Studio
alike. Any other importable module works too (e.g. an installed package,
optionally imported via the top-level `plugins:` config list).

### Example

```python
# scenarios/my_world/builders.py
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
world-name auto-detection.

### Available Helpers in PersonaPipelineAgentBuilder

These helpers are useful when a custom builder wants to reuse the default
persona-pipeline behavior. If your builder only needs ordinary records and field
mapping, prefer instantiating `PersonaPipelineAgentBuilder` and appending to its
result.

| Method | Description |
|--------|-------------|
| `self._resolve_file_path(path)` | Resolve path relative to world dir |
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

The native 0.x runtime exposes the concrete `Agent` base class as the custom
agent contract. Older aliases such as `AgentLike` and formative-initializer
shim names are intentionally removed rather than kept as compatibility paths.

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

### Optional async fast path

Sync `act()` is the required contract. When the engine runs with
`sim.engine.executor: asyncio` (see the configuration reference), each turn is a
coroutine on one event loop instead of a pool thread. An agent that only
implements `act()` still works — the base class runs it on a helper thread — but
an agent can act loop-native (letting thousands of model calls overlap on a
handful of threads) by overriding `act_async()` and routing through
`_call_model_async()`, the async twin of `_call_model()`:

```python
    def act(self, action_spec: ActionSpec) -> ActionOutput:        # required
        return self._call_model(self._context(), action_spec)

    async def act_async(self, action_spec: ActionSpec) -> ActionOutput:  # optional
        return await self._call_model_async(self._context(), action_spec)
```

`_call_model_async` awaits the model's `sample_*_async` methods (native on the
OpenAI-compatible providers, a thread-wrapped default elsewhere). Async and sync
agents mix freely in the same step, so overriding `act_async` is purely additive.

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

`model` accepts either a scalar model name (above) or a full LLM block that
overrides `sim.llm` per-field:

```yaml
classes:
  candidate:
    count: 2
    model:
      name: gpt-4o
      temperature: 0.2       # Each present field overrides the matching
      provider: openai       # sim.llm field; unset fields fall back to global.
      api_base: null
      api_key: null
      extra_kwargs: {}       # REPLACE (not deep-merged) over sim.llm.extra_kwargs
      disabled: false
```

The seven block fields are `name`, `temperature`, `provider`, `api_base`,
`api_key`, `extra_kwargs`, and `disabled`. Models are deduped by **effective**
config: two classes sharing a name but differing in (say) temperature get
distinct model objects, while a run with no overrides still builds one shared
model.

Or per-agent via field mapping (your data source must include a model field):

```yaml
field_map:
  name: name
  context: persona
  model: model_name          # Maps data field → per-agent model
```

See [Usage Overview](usage.md#per-agent-llm-models) for the full priority chain.

---

## Registering a Custom LLM Provider

`sim.llm.provider` selects which language-model backend agents call. There are
three ways to point it at a model, none of which require editing core code:

1. **Registry decorator.** Decorate a `LanguageModel` subclass (or a factory
   returning one) and import the module before the runner builds models:

    ```python
    from silisocs.runtime.language_models.registry import register_llm_provider
    from silisocs.runtime.language_models import LanguageModel

    @register_llm_provider("my_provider")
    class MyModel(LanguageModel):
        ...
    ```

    ```yaml
    sim:
      llm:
        provider: my_provider
    ```

2. **Fully-qualified class path.** Skip registration and name the class
   directly; the factory imports and instantiates it:

    ```yaml
    sim:
      llm:
        provider: mypkg.models.MyModel
    ```

3. **Built-in OpenAI-compatible presets.** Common providers ship as named
   presets — `anthropic`, `gemini`, `openrouter`, `groq`, `together`,
   `deepseek`, `mistral`, `fireworks`, `xai`, `ollama`. Set the provider to the
   preset name and supply the key via the provider's env var (for example
   `ANTHROPIC_API_KEY`, `OPENROUTER_API_KEY`; `ollama` is local and needs none):

    ```yaml
    sim:
      llm:
        provider: openrouter
        name: anthropic/claude-3.5-sonnet
    ```

Providers that speak an OpenAI-compatible HTTP API should subclass
`OpenAICompatibleLanguageModel` to inherit retry/backoff and telemetry support.
The factory calls a provider with the standard kwargs (`model_name`, `log_file`,
`debug`, `api_base`, `api_key`, `temperature`, `extra_kwargs`). Kwargs the
provider does not declare are dropped — *except* `api_base`, `api_key`, and
`extra_kwargs`: if one of those is actually set in `sim.llm` (their defaults are
unset) and the provider cannot accept it, the run fails at build naming the
provider and the field, instead of quietly ignoring the configured endpoint,
credential, or request body (see [Configuration](configuration.md#llm)).
`model_name` always carries a default (`sim.llm.name`), so a fixed-model provider
that does not declare it still builds. Declare the ones your provider honors;
declare `**kwargs` to opt out of the check entirely.

---

## Runtime Memory (`sim.memory`)

How a NativeAgent renders its prompt's Memory section at runtime is a swappable
policy: `window` (default, last-N), `retrieval` (a recent-window floor plus the
most relevant older memories), or `summarizing` (rolling summaries + retrieved
older memories + a recent window). This is
distinct from *seeding* memories at step 0 (`sim.initialization.agents`). A
custom policy is a `MemoryPolicy` subclass (`record` / `render` / `all_memories`
/ `get_state` / `set_state`) referenced via `sim.memory.class_path`; the engine
injects it only into agents whose constructor accepts a `memory_policy` param, so
non-NativeAgent agents are unaffected. See
[Configuration → Agent Memory](configuration.md#agent-memory-simmemory).

## Wrapping your own harness (experimental)

A **harness agent** embeds a real agent harness (its own model→tool loop) as a silisocs
agent. The framework side is fully generic — you only implement a thin `HarnessAdapter`
that runs one harness turn; `HarnessAgent` owns observe-buffering, ActionSpec dispatch,
probes, checkpoint state, and telemetry. See [Harness Agents](harness_agents.md) for the
full picture; the seam is:

```python
from silisocs.agents.harness import HarnessAgent, HarnessTurnResult
from silisocs.agents.harness.adapter import HarnessTurnRequest

class MyHarnessAdapter:
    def run_turn(self, request: HarnessTurnRequest) -> HarnessTurnResult:
        # Drive your harness. Execute each tool it picks through the Tool Bridge:
        #   call = request.surface.execute(action_name, arguments)   # -> real backend result
        # request.surface.schemas() lists the actions this agent may call (already
        # filtered to the backend catalog + the agent's flow).
        final_text = ...  # the harness's closing message
        return HarnessTurnResult(final_text=final_text, finished=True)

    # Optional: run_turn_async (loop-native), run_probe, snapshot/restore,
    # bind_model_proxy(base_url, api_key) to route model calls through the Model Proxy.

class MyHarnessAgent(HarnessAgent):
    def __init__(self, model, *, name, persona="", probe_mode="model", **persona_fields):
        from silisocs.agents.harness.base import compose_persona
        super().__init__(
            model, name=name, adapter=MyHarnessAdapter(),
            persona=compose_persona(persona, **persona_fields), probe_mode=probe_mode,
        )
```

Reference it via `persona_pipeline.classes.<class>.class_path` — no game-master config is
needed (the default GM binds the Tool Bridge and records the self-describing harness
turn). `FakeHarnessAdapter` / `FakeHarnessAgent` are the dependency-free reference
implementation and the subject of the contract tests
(`tests/agents/test_harness_agent_contract.py`) — the tests are the public spec.

## Related

- [Memory Initialization](memory_initialization.md): How agents get their starting knowledge
- [Configuration Reference](configuration.md): Full persona_pipeline config options
- [Election Walkthrough](tutorials/election.md): Real-world multi-class world
- [Usage Overview](usage.md#developer-customization-guide): Engine/GM/backend customization map
