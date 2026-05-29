# Simulation Extensibility API Reference

This page is a developer-facing API reference for extending simulation
structure in code. It is separate from workflow guides such as
[Environment Layer](environment_layer.md) and [Building Agents](building_agents.md).

Use this reference when adding new agent runtimes, backend apps, game masters,
GM components, engines, engine policies, or probe/evaluator code.

## Reference Map

| Layer | Primary modules |
|---|---|
| Agents | `src/silisocs/agents/base_agent.py`, `agents/native.py`, `agents/fixed.py` |
| Agent builders | `src/silisocs/runtime/construction/agent_builders/` |
| Environment apps | `src/silisocs/environments/backends/base.py`, `environments/backends/factory.py` |
| GMs | `src/silisocs/environments/gm/base_game_master.py`, `gm/game_master.py` |
| GM components | `src/silisocs/environments/gm/components/` |
| Engines | `src/silisocs/simulation_engines/base.py`, `simulation_engines/base_engines.py`, `simulation_engines/multi_gm.py` |
| Engine policies | `src/silisocs/simulation_engines/policies/` |

## 1) Agents API

All agent runtimes must implement `silisocs.agents.base_agent.Agent`:

```python
class Agent(ABC):
    @property
    def name(self) -> str: ...

    def observe(self, observation: str) -> None: ...

    def act(self, action_spec: Any) -> str: ...
```

`act()` returns an `ActionOutput`. Native agents usually assemble their context
inside `act()` and call `self._call_model(context, action_spec)`, which routes
the typed `ActionSpec` to the configured `LanguageModel`. Custom runtimes with
internal state should also implement
`get_state()` and `set_state(state)` for checkpoint resume.

Native configs instantiate runtime classes directly with `class_path` and
`params`. Reference implementations live in `src/silisocs/agents/native.py`
and `src/silisocs/agents/fixed.py`. Concordia-style prefab agents are supported
only behind `compat: concordia` and the optional Concordia adapter.

## 2) Environment App API

The core backend contract is `BackendApp` in
`src/silisocs/environments/backends/base.py`.

Required method:

```python
def initialize(self, agent_names: list[str], **kwargs: Any) -> None: ...
```

Common optional methods:

- `observe(actor_name: str, **kwargs) -> str`
- `get_timeline(user_name: str, limit: int = 10) -> list[dict]`
- `get_timeline_mode(...) -> list[dict]`
- `format_timeline_for_observation(timeline: list[dict]) -> str`
- `parse_and_resolve_action(user_name: str, action_data: dict) -> str`

Expose executable actions with `@app_action`. `generic_action` and
`tool_calling` resolve modes discover those actions automatically.

`SocialBackendApp` subclasses `BackendApp` for backends with social timelines,
feeds, social action parsing, or recommendation updates. Generic environments
should subclass `BackendApp` directly.

Custom app config:

```yaml
env:
  gm:
    backend:
      type: custom
      class_path: my_pkg.apps.MyBackendApp
      params:
        initial_cash: 20
```

`params` are strict constructor arguments. Unknown keys fail before simulation
startup unless the target class accepts `**kwargs`.

## 3) Game Master API

Base class: `BaseGameMaster`
Module: `src/silisocs/environments/gm/base_game_master.py`

Direct GM methods live on `ComponentGameMaster`. `MultiFlowGameMaster` keeps
the same public methods and routes component slots by agent flow:

- `acting_agents(...)`: chooses active actors.
- `action_prompt(agent_name)`: emits typed `ActionSpec`.
- `make_observation(agent_name)`: computes per-agent observation text.
- `resolve_action(agent_name, action)`: dispatches typed `ActionOutput`.
- `update(step, agents, context)`: runs pre-turn environment updates.
- `initialize(agents, context)`: initializes backend/app state through the
  Game Master's `components.initialize` slot.

Important integration fields used by engines/components:

- `backend`
- `agent_flow_tags`
- `flow_to_component_map`

## 4) GM Component API

Factory module: `src/silisocs/environments/gm/components/factory.py`

Public builders:

- `build_initialize_component(...)`
- `build_initialize_components(...)`
- `build_action_prompt_component(...)`
- `build_observe_component(...)`
- `build_observe_components(...)`
- `build_resolve_component(...)`
- `build_next_acting_component(...)`
- `build_update_component(...)`

Config schema pattern:

```yaml
<slot_name>:
  built_in: <registered_name>
  class_path: <optional.module.Class>
  params: {}
  instances: {}   # optional, for flow-routed GMs
  flow_map: {}    # optional, maps flow names to instance keys
```

`params` are strict constructor arguments. Unknown keys fail early unless the
target component accepts `**kwargs`. Runtime-injected values such as `model`,
`agent_names`, and app handles may still be filtered when a constructor does
not accept them. Observe components that explicitly accept `observation_params`
may use `params` as a forwarded observation-settings bag.

Built-in component names:

- `initialize`: `social_media`, `app_initialize`, `disabled`, `none`
- `action_prompt`: `default`
- `observe`: `app_observation`, `timeline_every_turn`, `episode_only`
- `resolve`: `parsed_action`, `generic_action`, `tool_calling`
- `next_acting`: `activity_markov`, `activity_probability`, `all_agents`, `fixed_order`
- `update`: `social_recommendation`, `disabled`, `none`

Subcomponent interfaces in `components/base.py`:

- `BaseComponent.get_state()` and `BaseComponent.set_state(...)`
- `InitializeComponent.initialize(...)`
- `NextActingComponent.acting_agent_names()`
- `ActionPromptComponent.action_prompt(agent_name)`
- `ObservationComponent.make_observation(agent_name)`
- `ResolveComponent.resolve_action(agent_name, action)`
- `UpdateComponent.update(step, agents, context)`

Runtime bootstrap extensions live under `silisocs.initialization`:

- `initialization.agents.AgentInitializer`
- `initialization.game_masters.GameMasterInitializerStrategy`
- `initialization.game_masters.GameMasterInitializer`
- `initialization.simulation.SimulationInitializer`

The Engine runs those phases in order: agents, Game Masters, then simulation.
Seed posts are simulation initialization and are posted through
`GameMaster.resolve_action(...)`.

## 5) Engine API

Modules:

- `src/silisocs/simulation_engines/base.py`: `BaseRuntimeEngine` marker.
- `src/silisocs/simulation_engines/base_engines.py`: `BaseRuntimeEngine`,
  `FlowRuntimeEngine`.
- `src/silisocs/simulation_engines/multi_gm.py`: `MultiGMRuntimeEngine`.

Factory entrypoint: `build_engine(cfg)` in
`src/silisocs/runtime/construction/engines.py`.

`BaseRuntimeEngine` provides the standard episode loop, actor concurrency, and
probe phase orchestration. `FlowRuntimeEngine` adds flow grouping and per-flow
turn-policy policy selection. `MultiGMRuntimeEngine` adds explicit GM
orchestration.

## 6) Engine Policy API

Turn-policy policies live in
`src/silisocs/simulation_engines/policies/action_chunk.py` and implement:

```python
def run(
    *,
    engine: Any,
    game_master: Any,
    agent: Any,
    action_spec: Any,
    skip_actions: bool,
    verbose: bool,
) -> str: ...
```

Probe-schedule policies live in
`src/silisocs/simulation_engines/policies/probe_schedule.py` and implement:

```python
def should_run_probe_phase(self, *, step: int, orchestrator: Any) -> bool: ...
```

Built-ins:

- Turn policy: `single_action`, `fixed_count`, `open_ended`
- Probe schedule: `step_schedule`, `fixed_interval`, `disabled`

Policy factories support `built_in`, optional `class_path`, and strict
constructor validation for configured `params`.

## 7) Extension Recipes

Add a custom backend app:

1. Subclass `BackendApp`.
2. Implement `initialize(...)`.
3. Add `@app_action` methods.
4. Configure `env.gm.backend.class_path` and `env.gm.backend.params`.

Add a custom GM component:

1. Implement a native context component or initializer hook.
2. Point `env.gm.components.<slot>.class_path` to the class.
3. Put public constructor settings under `params`.

Add a custom turn policy:

```yaml
sim:
  engine:
    turn_policy:
      class_path: my_pkg.policies.MyActionLoopPolicy
      params:
        max_actions: 5
```

Add a custom probe schedule policy by implementing `should_run_probe_phase(...)`
and configuring `evals.probes.schedule.class_path`.

## 8) Compatibility Checklist

- Keep method signatures aligned with factory call sites.
- Treat `params` as strict public API; add `**kwargs` only when intentionally
  accepting arbitrary config keys.
- Preserve checkpoint state contracts (`get_state`/`set_state`) when stateful.
- Add targeted tests for factory construction and runtime behavior.
- Update this page when adding new built-ins or extension hooks.

## Related Docs

- [Documentation Coverage](documentation_coverage.md): coverage and staleness tracker
- [Framework Roadmap](framework_roadmap.md): near-term framework priorities
- [Environment Layer](environment_layer.md): architecture and design patterns
- [Building Agents](building_agents.md): scenario and builder workflows
- [Configuration Reference](configuration.md): full config keys
- [Contributing](contributing.md): lint/test workflow and coding standards
