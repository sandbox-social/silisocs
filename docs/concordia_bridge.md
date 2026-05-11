# Concordia Bridge

Silisocs builds on [Concordia](https://github.com/google-deepmind/concordia)
rather than replacing it. Concordia provides the agent substrate and action
loop concepts. Silisocs adds social-media-specific configuration, game-master
components, platform backends, probe scheduling, and run artifacts.

## Mental Model

| Concordia concept | Silisocs layer |
|-------------------|----------------|
| Entity agent | `silisocs.agents.entity` or any custom `Agent` implementation |
| Prefab | Scenario-selected prefab module/class in `persona_pipeline` |
| Observation | GM observe component output from a social-media backend |
| Action spec | GM act component prompt, optionally with tool-calling markers |
| Game master | Component-slotted social-media coordinator |
| Simulation loop | Engine policy plus GM/backend/probe orchestration |

## Agent Boundary

Every runtime agent must satisfy `silisocs.agents.base_agent.Agent`:

```python
class Agent:
    @property
    def name(self) -> str: ...
    def observe(self, observation: str) -> None: ...
    def act(self, action_spec) -> str: ...
```

Concordia-compatible entities satisfy this through their component orchestration.
Simpler custom agents can implement the three-method interface directly. This is
why deterministic fixed-action agents and LLM-backed Concordia entities can run
through the same game-master and engine code.

## Prefabs and Scenario YAML

Silisocs keeps Concordia prefab construction but moves the selection into YAML:

```yaml
persona_pipeline:
  classes:
    user:
      count: 10
      prefab_module: silisocs.agents.entity
      data:
        source: inline
        records:
          - persona: Alex follows local policy and posts practical updates.
      field_map:
        context: persona
```

The builder loads records, maps fields into prefab parameters, and instantiates
the configured prefab. Custom prefabs should expose an `Entity` class whose
`build(model, memory_bank)` method returns an object implementing the agent
interface.

## Game Master Boundary

Concordia asks agents to observe and act. Silisocs decides what those social
observations and actions mean:

- `next_acting` chooses which agents act.
- `observe` turns backend state into timeline or episode observations.
- `act` creates Concordia action specs and routes flow-specific prompts.
- `resolve` parses the agent response or executes selected tools.
- `recommend` schedules recommender updates.
- `initializer` creates backend users, follow networks, seed posts, and memory.

This split keeps Concordia-facing agent code small while platform behavior
lives in configurable GM components and backends.

The baseline GM and the shared-flow GM use the same backend-facing contract:
they create backend apps through the factory, initialize users/follow graphs and
seed posts through the initializer component, and write action events through
the same logger. Shared flow changes component routing; it does not define a
separate platform lifecycle.

## Where Silisocs Adds Structure

Concordia components are still the unit that observes, acts, and stores state,
but Silisocs adds a few conventions around them:

- Scenario YAML chooses prefabs and passes structured persona data.
- GM slots give names to platform-specific responsibilities such as timeline
  observation, action parsing, backend initialization, and recommendation
  updates.
- Backend actions are Python methods decorated with `@app_action`, which lets
  prompt-based parsing and tool-calling share one action catalog.
- Runtime artifacts such as SQLite databases, checkpoints, probe outputs, and
  action logs are owned by Silisocs so experiments are reproducible outside a
  Concordia-only script.

## Tool Calling

When `sim.tool_calling.mode` is `single` or `multi`, the GM adds a tool-calling
marker to the action spec. `SocialConcatActComponent` detects that marker, asks
the model to choose backend action tools, and returns structured JSON. The
resolve component then executes the selected backend actions.

The agent still only implements `act(action_spec) -> str`; action execution
remains a platform concern.

## Extension Guidance

- Add new agent behavior by creating a prefab module or custom `Agent`, then
  select it from `persona_pipeline`.
- Add new platform behavior by implementing backend actions with
  `@app_action(...)`, then configure the backend and resolve component.
- Add new scheduling behavior through engine policies or GM components instead
  of branching inside the runner.
- Prefer YAML composition for experiment-specific choices; reserve Python for
  reusable components.
