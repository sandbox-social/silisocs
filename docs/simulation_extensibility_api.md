# Simulation Extensibility API Reference

This page is a developer-facing API reference for extending simulation
structure in code. It is separate from workflow guides such as
[Environment Layer](environment_layer.md) and [Building Agents](building_agents.md).

Use this reference when adding new agent runtimes, backend apps, game masters,
GM components, engines, engine policies, or probe/evaluator code.

## Reference Map

| Layer | Primary modules |
|---|---|
| Agents | `src/silisocs/agents/base_agent.py`, `agents/entity.py`, `agents/fixed_entity.py`, `agents/builders.py` |
| Environment apps | `src/silisocs/environments/backends/base.py`, `environments/backends/factory.py` |
| GMs | `src/silisocs/environments/gm/base_game_master.py`, `gm/game_master.py`, `gm/shared_flow_game_master.py`, `gm/act.py` |
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

`act()` returns a string. Parsing and execution are performed by GM resolve
components, so the expected output format is determined by the configured
resolve mode. Non-Concordia runtimes with internal state should also implement
`get_state()` and `set_state(state)` for checkpoint resume.

Prefabs must expose a class named `Entity` that subclasses
`concordia.typing.prefab.Prefab` and implements `build(model, memory_bank)`.
Reference implementations live in `src/silisocs/agents/entity.py` and
`src/silisocs/agents/fixed_entity.py`.

## 2) Environment App API

The core backend contract is `EnvironmentApp` in
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

`SocialMediaApp` subclasses `EnvironmentApp` for backends with social timelines,
feeds, social action parsing, or recommendation updates. Generic environments
should subclass `EnvironmentApp` directly.

Custom app config:

```yaml
env:
  platform_type: custom
  app:
    class_path: my_pkg.apps.MyEnvironmentApp
    params:
      initial_cash: 20
```

`params` are strict constructor arguments. Unknown keys fail before simulation
startup unless the target class accepts `**kwargs`.

## 3) Game Master API

Base class: `BaseEnvironmentGameMaster`
Module: `src/silisocs/environments/gm/base_game_master.py`

Primary API:

- `build(model, memory_bank) -> EntityAgentWithLogging`
- `_is_shared_flow_mode() -> bool`
- `build_generic_prompt(...) -> str`

Compatibility names such as `BaseSocialMediaGameMaster` remain available for
social backend call sites.

Act-layer classes live in `src/silisocs/environments/gm/act.py`:

- `SMAct`: default routing behavior.
- `MultiFlowSMAct`: routes observe/resolve outputs by flow tag.

Important integration fields used by engines/components:

- `sm_app`
- `entity_flow_tags`
- `gm_orchestration`
- `flow_to_component_map`

## 4) GM Component API

Factory module: `src/silisocs/environments/gm/components/factory.py`

Public builders:

- `build_observe_component(...)`
- `build_observe_components(...)`
- `build_resolve_component(...)`
- `build_next_acting_component(...)`
- `build_recommendation_component(...)`
- `build_backend_initializer(...)`
- `initialize_component_flow_fields(component, component_config)`

Config schema pattern:

```yaml
<slot_name>:
  built_in: <registered_name>
  class_path: <optional.module.Class>
  params: {}
  flows: {}
```

`params` are strict constructor arguments. Unknown keys fail early unless the
target component accepts `**kwargs`. Runtime-injected values such as `model`,
`player_names`, and app handles may still be filtered when a constructor does
not accept them. Observe components that explicitly accept `observation_params`
may use `params` as a forwarded observation-settings bag.

Built-in component names:

- `observe`: `app_observation`, `timeline_every_turn`, `episode_only`
- `resolve`: `parsed_action`, `generic_action`, `tool_calling`
- `next_acting`: `activity_markov`, `activity_probability`, `all_entities`, `fixed_order`
- `initializer`: `backend_default`
- `recommend`: `recommendation_component`, `disabled`, `none`

Subcomponent interfaces in `components/base.py`:

- `BackendInitializer.initialize(...)`
- `FlowComponent.FLOW_FIELDS`
- `FlowComponent.set_flow_field_values(...)`
- `FlowComponent.get_flow_field(...)`

## 5) Engine API

Modules:

- `src/silisocs/simulation_engines/base.py`: `BaseEnvironmentEngine` marker.
- `src/silisocs/simulation_engines/base_engines.py`: `BaseRuntimeEngine`,
  `FlowRuntimeEngine`.
- `src/silisocs/simulation_engines/multi_gm.py`: `MultiGMRuntimeEngine`.

Factory entrypoint: `build_engine(cfg)` in `src/silisocs/runtime/factories.py`.

`BaseRuntimeEngine` provides the standard episode loop, actor concurrency, and
probe phase orchestration. `FlowRuntimeEngine` adds flow grouping and per-flow
action-loop policy selection. `MultiGMRuntimeEngine` adds explicit GM
orchestration.

## 6) Engine Policy API

Action-loop policies live in
`src/silisocs/simulation_engines/policies/action_chunk.py` and implement:

```python
def run(
    *,
    engine: Any,
    game_master: Any,
    entity: Any,
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

- Action loop: `single_action`, `fixed_count`, `open_ended`
- Probe schedule: `step_schedule`, `fixed_interval`, `disabled`

Policy factories support `built_in`, optional `class_path`, and strict
constructor validation for configured `params`.

## 7) Extension Recipes

Add a custom backend app:

1. Subclass `EnvironmentApp`.
2. Implement `initialize(...)`.
3. Add `@app_action` methods.
4. Configure `env.app.class_path` and `env.app.params`.

Add a custom GM component:

1. Implement a Concordia-compatible context component or initializer hook.
2. Point `env.gm.components.<slot>.class_path` to the class.
3. Put public constructor settings under `params`.

Add a custom action-loop policy:

```yaml
sim:
  engine:
    action_loop:
      class_path: my_pkg.policies.MyActionLoopPolicy
      params:
        max_actions: 5
```

Add a custom probe schedule policy by implementing `should_run_probe_phase(...)`
and configuring `sim.engine.probe_schedule.class_path`.

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
