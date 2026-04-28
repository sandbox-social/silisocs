# Simulation Extensibility API Reference

This page is a **developer-facing API reference** for extending simulation
structure in code. It is intentionally separate from workflow guides such as
[Environment Layer](environment_layer.md) and [Building Agents](building_agents.md).

Use this reference when you want to add new:

- Agent runtime implementations
- Agent prefab modules
- Game master prefabs
- GM components (`observe`, `resolve`, `next_acting`, etc.)
- Engine implementations
- Engine policies (action loop and probe schedule)

---

## Reference Map

| Layer | Primary modules |
|---|---|
| Agents | `src/mastodon_sim/agents/base_agent.py`, `agents/entity.py`, `agents/fixed_entity.py`, `agents/builders.py` |
| GMs | `src/mastodon_sim/environments/gm/base_game_master.py`, `gm/game_master.py`, `gm/shared_flow_game_master.py`, `gm/act.py` |
| GM components | `src/mastodon_sim/environments/gm/components/` |
| Engines | `src/mastodon_sim/engines/base.py`, `engines/base_engines.py`, `engines/multi_gm.py` |
| Engine policies | `src/mastodon_sim/engines/policies/` |

---

## 1) Agents API

### 1.1 Required runtime interface

All agent runtimes must implement `mastodon_sim.agents.base_agent.Agent`.

Module: `src/mastodon_sim/agents/base_agent.py`

```python
class Agent(ABC):
    @property
    def name(self) -> str: ...

    def observe(self, observation: str) -> None: ...

    def act(self, action_spec: Any) -> str: ...
```

Contract notes:

- `observe()` is called before an action chunk and should update local state.
- `act()` returns a string. Parsing/execution is performed by GM resolve components.
- Output format must match the configured resolve mode (`parsed_action`, `generic_action`, `tool_calling`), but that policy lives outside the agent.

### 1.2 Optional checkpoint interface

If your runtime is not a Concordia entity-with-components but has internal state,
implement:

- `get_state() -> dict[str, Any]`
- `set_state(state: dict[str, Any]) -> None`

The simulation runner restores these during resume operations.

### 1.3 Prefab contract (what the builder loads)

Prefabs must expose a class named `Entity` that subclasses `concordia.typing.prefab.Prefab`
and implements `build(model, memory_bank)`.

Reference implementations:

- `src/mastodon_sim/agents/entity.py` (LLM-driven base entity)
- `src/mastodon_sim/agents/fixed_entity.py` (deterministic fixed-action runtime)

Required behavior:

- Accepts config parameters via `params`
- Returns an agent runtime implementing the interface above

### 1.4 Builder contract

Default builder: `BaseAgentBuilder` in `src/mastodon_sim/agents/builders.py`

Public API:

- `build_agents(roles: dict[str, int] | None = None) -> list[AgentConfig]`

Expected config structure:

- `persona_pipeline.defaults`
- `persona_pipeline.classes.<class_name>`

Extension points:

- Subclass `BaseAgentBuilder` for scenario-specific record loading, naming, or class composition
- Keep return type as `list[AgentConfig]` for runner compatibility

---

## 2) Game Master API

### 2.1 GM prefab base class

Base class: `BaseSocialMediaGameMaster`
Module: `src/mastodon_sim/environments/gm/base_game_master.py`

Primary API:

- `build(model, memory_bank) -> EntityAgentWithLogging`
- `_is_shared_flow_mode() -> bool`
- `build_generic_prompt(...) -> str`

Concrete presets:

- `GameMaster` (`gm/game_master.py`): single-flow default
- `MultiFlowSocialMediaGameMaster` (`gm/shared_flow_game_master.py`): multi-flow routing

### 2.2 Act-layer contract (SwitchAct specializations)

Module: `src/mastodon_sim/environments/gm/act.py`

Classes:

- `SMAct`: default routing behavior
- `MultiFlowSMAct`: routes `observe`/`resolve` by flow tag

Important integration fields used by engines/components:

- `sm_app`
- `entity_flow_tags`
- `gm_orchestration`
- `flow_to_component_map` (multi-flow mode)

If you write a custom `SwitchAct` variant, keep these semantics (or update all dependent code paths that currently rely on them).

### 2.3 Component slot factory API

Module: `src/mastodon_sim/environments/gm/components/factory.py`

Public builders:

- `build_observe_component(...)`
- `build_observe_components(...)`
- `build_resolve_component(...)`
- `build_next_acting_component(...)`
- `build_recommendation_component(...)`
- `build_backend_initializer(...)`
- `initialize_component_flow_fields(component, component_config)`

Config schema pattern (per slot):

```yaml
<slot_name>:
  built_in: <registered_name>
  class_path: <optional python path>
  params: {}
```

### 2.4 GM subcomponent interfaces

Module: `src/mastodon_sim/environments/gm/components/base.py`

`BackendInitializer` interface:

```python
class BackendInitializer(ABC):
    def initialize(
        *,
        sm_app: Any,
        agent_names: Sequence[str],
        init_kwargs: Mapping[str, Any],
    ) -> None: ...
```

`FlowComponent` mixin:

- Declare flow-tunable fields via `FLOW_FIELDS`
- Initialize per-flow values via `set_flow_field_values(...)`
- Read current value via `get_flow_field(field_name, flow_tag, default=...)`

### 2.5 Built-in component registry (factory names)

From `components/factory.py`:

- `observe`: `timeline_every_turn`, `episode_only`
- `resolve`: `parsed_action`, `generic_action`, `tool_calling`
- `next_acting`: `activity_markov`, `activity_probability`, `all_entities`, `fixed_order`
- `initializer`: `backend_default`
- `recommend`: `recommendation_component`

When adding a new built-in, register it in the relevant `_..._BUILT_INS` map.

---

## 3) Engine API

### 3.1 Engine classes

Modules:

- `src/mastodon_sim/engines/base.py`: `BaseEnvironmentEngine` marker
- `src/mastodon_sim/engines/base_engines.py`: `BaseRuntimeEngine`, `FlowRuntimeEngine`
- `src/mastodon_sim/engines/multi_gm.py`: `MultiGMRuntimeEngine`

Factory entrypoint:

- `build_engine(cfg)` in `src/mastodon_sim/runtime/factories.py`

### 3.2 BaseRuntimeEngine extension surface

Primary class: `BaseRuntimeEngine`
Module: `src/mastodon_sim/engines/base_engines.py`

Key public/override methods:

- `next_acting(...)`
- `run_loop(...)`

Important protected hooks you can override in custom engines:

- `_phase_game_masters(...)`
- `_build_flow_task_groups(...)`
- `_build_flow_action_loop_policies(...)`
- `_action_loop_policy_for_group(...)`

App-coupled helpers currently used by default engine:

- `_is_app_game_master(...)`
- `_sync_app_game_master_runtime_state(...)`

If you design a non-social backend pipeline, these are usually the first methods to adapt.

### 3.3 FlowRuntimeEngine contract

`FlowRuntimeEngine` extends `BaseRuntimeEngine` and adds:

- multi-GM phase grouping by sequence
- flow-group task construction
- per-flow action-loop policy selection (`engine.flow_policies`)

### 3.4 MultiGMRuntimeEngine contract

`MultiGMRuntimeEngine` extends `FlowRuntimeEngine` with explicit GM orchestration.

Additional API:

- `get_agent_gms(agent_name)`
- `detect_gm_conflicts()`
- `validate_gm_sequence()`
- `log_orchestration_info()`

Configuration fields consumed:

- `sim.gm.gm_sequence`
- `sim.gm.agent_classes`
- `sim.gm.class_to_gms`

---

## 4) Engine Policy API

### 4.1 Action-loop policy contract

Module: `src/mastodon_sim/engines/policies/action_chunk.py`

Policy objects must provide:

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

Built-ins:

- `SingleActionChunkPolicy` (`single_action`)
- `FixedCountActionChunkPolicy` (`fixed_count`)
- `OpenEndedActionChunkPolicy` (`open_ended`)

### 4.2 Probe schedule policy contract

Module: `src/mastodon_sim/engines/policies/probe_schedule.py`

Policy objects must provide:

```python
def should_run_probe_phase(self, *, step: int, orchestrator: Any) -> bool: ...
```

Built-ins:

- `StepProbeSchedulePolicy` (`step_schedule`)
- `FixedIntervalProbeSchedulePolicy` (`fixed_interval`)
- `DisabledProbeSchedulePolicy` (`disabled`)

### 4.3 Policy factories and class-path loading

Module: `src/mastodon_sim/engines/policies/factory.py`

Public API:

- `build_action_loop_policy(slot_cfg)`
- `build_probe_schedule_policy(slot_cfg)`

Features:

- `built_in` selector
- optional `class_path`
- constructor kwarg filtering to supported `__init__` params

---

## 5) Extension Recipes

### 5.1 Add a custom agent runtime

1. Implement `Agent` interface in a runtime class.
2. Add `Entity(prefab_lib.Prefab)` in your prefab module and return runtime in `build()`.
3. Reference prefab in `persona_pipeline.classes.<class>.prefab_module`.

### 5.2 Add a custom GM resolve component

1. Implement a Concordia-compatible context component class.
2. Point `env.gm.components.resolve.class_path` to your class.
3. Keep input/output compatible with your selected `sim.action_mode` and tool-calling mode.

### 5.3 Add a custom action-loop policy

1. Implement `run(...)` contract in a policy class.
2. Configure:

```yaml
sim:
  engine:
    action_loop:
      class_path: my_pkg.policies.MyActionLoopPolicy
      params:
        ...
```

### 5.4 Add a custom probe schedule policy

1. Implement `should_run_probe_phase(...)`.
2. Configure under `sim.engine.probe_schedule.class_path`.

---

## 6) Compatibility Checklist for New Components

- Keep method signatures aligned with factory call sites.
- Accept unknown config fields defensively where possible.
- Preserve checkpoint state contracts (`get_state`/`set_state`) when stateful.
- Add targeted tests for factory construction + runtime behavior.
- Update this page when adding new built-ins or extension hooks.

---

## Related Docs

- [Environment Layer](environment_layer.md): architecture and design patterns
- [Building Agents](building_agents.md): scenario and builder workflows
- [Configuration Reference](configuration.md): full config keys
- [Contributing](contributing.md): lint/test workflow and coding standards
