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
| Engines | `src/silisocs/simulation_engines/base_engines.py`, `simulation_engines/runtime_base.py` |
| Engine policies | `src/silisocs/simulation_engines/policies/loops.py`, `steps.py`, `turns.py`, `routers.py`, `probe_schedule.py` |

## 1) Agents API

All agent runtimes must implement `silisocs.agents.base_agent.Agent`:

```python
class Agent(ABC):
    @property
    def name(self) -> str: ...

    def observe(self, observation: str) -> None: ...

    def act(self, action_spec: ActionSpec) -> ActionOutput: ...
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

### Language Model API

All model providers implement `silisocs.runtime.language_models.LanguageModel`.
Agents do not call provider-specific clients directly; they use `_call_model(...)`
or, for custom low-level behavior, the typed model methods:

```python
sample_text(prompt, **kwargs) -> str
sample_choice(prompt, responses, **kwargs) -> tuple[int, str, dict[str, float]]
sample_tool_calls(prompt, tools, *, mode="single", **kwargs) -> list[ToolCall]
sample_structured(prompt, schema, **kwargs) -> dict[str, Any]
```

Use `ScriptedLanguageModel` for deterministic tests, `OpenAILanguageModel` for
the OpenAI API, `OpenAICompatibleLanguageModel` for compatible local or hosted
endpoints, and `NoLanguageModel` only when an agent never needs model output.
Unsupported typed outputs should raise rather than silently returning dummy data.

### Agent Builder API

Agent builders translate config into construction specs. They do not instantiate
live agents and they do not receive the model. Subclass
`silisocs.runtime.construction.agent_builders.AgentBuilder` and implement:

```python
def build_agent_configs(self) -> list[AgentConfig]: ...
```

The default `PersonaPipelineAgentBuilder` handles inline/config/file/Hugging Face
records, field mapping, shared/specific memories, and fixed-action plan rendering.
Custom builders may call it internally, then append additional `AgentConfig`
records. Runtime assembly remains responsible for loading classes, injecting the
`LanguageModel`, and wrapping explicit Concordia-compatible agents.

## 2) Environment App API

The core backend contract is `BackendApp` in
`src/silisocs/environments/backends/base.py`.

Common backend setup hook:

```python
def initialize(self, agent_names: list[str], **kwargs: Any) -> None: ...
```

Common optional hooks:

- `observe(actor_name: str, **kwargs) -> str`
- `update(step: int, agent_names: Sequence[str], context=None) -> None`

Timeline/recommendation capability methods live on `SocialBackendApp`, not on
plain `BackendApp`:

- `get_timeline(user_name: str, limit: int = 10) -> list[dict]`
- `get_timeline_mode(...) -> list[dict]`
- `format_timeline_for_observation(timeline: list[dict]) -> str`
- `parse_and_resolve_action(user_name: str, action_data: dict) -> str`

Expose executable actions with `@app_action`. `generic_action` and
`tool_calling` resolve modes discover those actions automatically.
If an action needs the acting identity, include an `agent_name: str` parameter;
the resolver injects it from the active Agent Name and omits it from
agent-facing prompts/tool schemas. Target parameters such as `target_user`
remain agent-visible.

`SocialBackendApp` subclasses `BackendApp` for backends that use the
timeline/recommendation GM components. Backends that do not need those
components should subclass `BackendApp` directly.

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

GMs should keep state minimal: the backend, typed component slots, routing
metadata, and optional `get_state`/`set_state` hooks. They should not read the
Hydra config directly; construction code passes normalized params into backend
and component constructors.

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
- `next_acting`: `all_agents`, `fixed_order` (activity models are sim-level: `sim.engine.participation`)
- `update`: `social_recommendation`, `disabled`, `none`

Generic component bases and defaults live directly under
`silisocs.environments.gm.components`. Social-media-only components live under
`silisocs.environments.gm.components.social_media` but keep the same built-in
YAML names.

Subcomponent interfaces in `components/base.py`:

- `BaseComponent.get_state()` and `BaseComponent.set_state(...)`
- `InitializeComponent.initialize(...)`
- `NextActingComponent.acting_agent_names()`
- `ActionPromptComponent.action_prompt(agent_name)`
- `ObservationComponent.make_observation(agent_name)`
- `ResolveComponent.resolve_action(agent_name, action)`
- `UpdateComponent.update(step, agents, context)`

Default baselines are available for backends that only need `BackendApp`:

- `AppInitializeComponent` calls a backend's `initialize(...)`.
- `AllAgentsNextActing` and `FixedOrderNextActing` select agents without social
  timeline logic; activity models live in
  `silisocs.simulation_engines.policies.participation`.
- `AppObservationComponent` delegates to `BackendApp.observe(...)`.
- `EpisodeObservation` returns episode-index observations for scripted flows.
- generic action and tool-calling resolvers dispatch backend actions discovered
  through `@app_action`.
- `AppUpdateComponent` delegates to `BackendApp.update(...)`.
- `NoOpUpdateComponent` is the explicit update slot for environments that do
  not need pre-turn state refresh.

Social-media-specific defaults are intentionally isolated under
`components.social_media`: `TimelineMakeObservation` and
`SocialRecommendationUpdateComponent`. They require a `SocialBackendApp`.

Use `BaseComponent.get_state()` / `set_state(...)` only for component-owned
state that must survive checkpoint resume, such as a fixed-order index. Backend
domain state should live in the backend.

Runtime bootstrap extensions live under `silisocs.initialization`:

- `initialization.agents.AgentInitializer`
- `initialization.game_masters.GameMasterInitializerStrategy`
- `initialization.game_masters.GameMasterInitializer`
- `initialization.simulation.SimulationInitializer`

The Engine runs those phases in order: agents, Game Masters, then simulation.
Seed posts are simulation initialization and are posted through
`GameMaster.resolve_action(...)`.

Initializer contracts:

- Agent initializers receive agents plus `AgentInitializationContext`, mutate
  agent memory/observations by calling `agent.initialize(...)`, and return
  `None`.
- Game Master phase strategies receive GMs, agents, and the initialization
  context, then call each GM's `initialize(...)`. The GM delegates backend setup
  to its initialize component.
- Simulation initializers receive agents, GMs, and context after backend setup.
  Seed-post initializers generate or load seed text and post it through normal
  GM resolution, so backend action logs keep the same shape as in-loop actions.

## 5) Engine API

Modules:

- `src/silisocs/simulation_engines/base_engines.py`: concrete `RuntimeEngine`
  plus preset wrappers.
- `src/silisocs/simulation_engines/runtime_base.py`: shared dataclasses and
  protocols, including `AgentStepResult`, `StepBatch`, and `StepResult`.
- `src/silisocs/simulation_engines/policies/loops.py`: loop policies.
- `src/silisocs/simulation_engines/policies/steps.py`: step policies.
- `src/silisocs/simulation_engines/policies/turns.py`: turn policies.

Factory entrypoint: `build_engine(cfg)` in
`src/silisocs/runtime/construction/engines.py`.

`RuntimeEngine` provides startup initialization, per-step GM updates,
per-agent observe/act/resolve, action concurrency, retry telemetry, and probe
phase orchestration. There is a single engine class; the scheduling behavior is
chosen entirely by its step strategy, which `build_engine` selects from
`sim.engine.step.built_in` (`base`, `sequential`, `flow`, or the `multi_gm*`
traversals). To add a new traversal, write a step strategy and register it — no
new engine class is needed.

## 6) Engine Policy API

Loop policies own the outer episode lifecycle and implement:

```python
def run(
    *,
    engine: Any,
    game_masters: list[Any],
    agents: list[Any],
    max_steps: int,
    start_step: int,
    verbose: bool,
    checkpoint_callback: Any | None,
) -> None: ...
```

Step policies own one episode step and implement:

```python
def run(
    *,
    engine: Any,
    step_index: int,
    game_masters: list[Any],
    agents: list[Any],
    verbose: bool,
) -> StepResult: ...
```

Most custom step policies should call `engine._execute_batches(...)` with a
sequence of `StepBatch(flow_name, game_master, turns)`. Each `turns` entry is
`(agent, action_spec)`. Calling `_execute_batches(...)` keeps standard action
logging, retry telemetry, concurrency limits, and `StepResult` shape. A custom
step policy may bypass it, but then it owns those responsibilities.

The `multi_gm` step policy emits its chain batches for concurrent execution:
flows run as independent pipelines through their GM chains, gated only by
shared-GM overlap (the engine's per-GM lock serializes turns when two flows touch
the same GM at once). Two sibling step policies selected via
`sim.engine.step.built_in` offer the other traversal modes: `multi_gm_serial`
(legacy row-major — each flow runs its full chain to completion before the next)
and `multi_gm_staged` (column-major with a global per-stage barrier — all flows
advance one stage at a time, with `null` chain entries idling a flow at a stage).

Turn policies own one selected agent's action cadence and implement:

```python
def run(
    *,
    engine: Any,
    game_master: Any,
    agent: Any,
    action_spec: Any,
    verbose: bool,
) -> str: ...
```

Most custom turn policies should call
`engine.run_agent_step(game_master=..., agent=..., action_spec=..., verbose=...)`.
That helper performs observation, `agent.act(...)`, GM resolution, and result
observation. A custom turn policy may bypass it only when it intentionally owns
the full observe/act/resolve cycle.

Probe-schedule policies live in
`src/silisocs/simulation_engines/policies/probe_schedule.py` and implement:

```python
def should_run_probe_phase(self, *, step: int, orchestrator: Any) -> bool: ...
```

### Branch router policies (dynamic GM selection)

Branch routers decide which GM each of a flow's agents acts on at a
`{branch: {router, choices}}` node in a `flow_to_gms` chain (see
[Multi-GM Architecture](multi_gm_architecture.md#branch-routing)). The built-ins
live in `src/silisocs/simulation_engines/policies/routers.py` and are built by
`build_router` from a `{built_in|class_path, params}` slot. A router is **any
callable** — no base class, no registration; the signature is formalized by the
structural `Router` Protocol in `routers.py` (its parameters are positional-only,
so your function may name them freely):

```python
def route(agents, gms, ctx):
    # agents: the flow's agent objects  -> call agent.act(...) freely
    # gms:    {gm name: game master}     -> one per choice, config order; read gm.backend freely
    # ctx:    RouteInfo(flow, step, seed) -> stable inputs for a replay-friendly decision
    return {agent.name: <chosen gm name> for agent in agents}   # cover every agent
```

The engine runs the router when the flow's chain reaches the branch stage — after
the flow's earlier hops have drained, so the router sees live backend state and
may call the agents — in all three `multi_gm*` traversals. It owns only three
things, all invisible to the author: *when* the router runs, *locking* (the router
call runs unlocked; the follow-up per-chosen-GM turn selection runs under that GM's
lock), and *validation* of the returned assignment (every agent covered, every GM
a real choice). Everything else the router does itself — read `gms[...].backend`,
build an `ActionSpec` and call `agent.act(...)`, decide from `ctx.seed`. There are
no capability flags, no context facade, and no engine-provided ask helper. The
shared `match_choice(answer, choices)` helper (exact → case-insensitive →
contained-once) is importable for routers that ask agents free-text questions.

Built-ins:

- Loop policy: `fixed_steps`
- Step policy: `base`, `sequential`, `flow`, `multi_gm` (concurrent chain
  batches), `multi_gm_serial` (legacy row-major chain traversal), `multi_gm_staged`
  (column-major chain traversal behind a global per-stage barrier)
- Turn policy: `single_action`, `fixed_count`, `open_ended`
- Branch router: `random` (`RandomChoiceRouter`, weighted pick, deterministic per
  `(seed, flow, step, agent)`), `agent_choice` (`AgentChoiceRouter` — asks each
  agent via a CHOICE probe; `prompt` / `on_invalid` params)
- Probe schedule: `step_schedule`, `fixed_interval`, `disabled`

Turn policy params include `observe_before_act: first | always | never`.
`first` is the default and preserves existing behavior for repeated actions.
Policy factories support `built_in`, optional `class_path`, and strict
constructor validation for configured `params`.

## 7) Extension Recipes

Add a custom backend app:

1. Subclass `BackendApp`.
2. Implement `initialize(...)`.
3. Implement `observe(...)` and optionally `update(...)`.
4. Add `@app_action` methods.
5. Configure `env.gm.backend.class_path` and `env.gm.backend.params`.

Add a custom GM component:

1. Implement a native context component or initializer hook.
2. Point `env.gm.components.<slot>.class_path` to the class.
3. Put public constructor settings under `params`.

Add a custom turn policy:

```yaml
sim:
  engine:
    turn_policy:
      class_path: my_pkg.policies.MyTurnPolicy
      params:
        max_actions: 5
```

Add a custom sequential-style step policy:

```python
from silisocs.simulation_engines.runtime_base import StepBatch, StepResult


class MySequentialStepPolicy:
    name = "my_sequential"

    def run(self, *, engine, step_index, game_masters, agents, verbose) -> StepResult:
        del step_index, verbose
        gm = game_masters[0]
        turns = engine._selected_turns(game_master=gm, candidate_agents=agents)
        batches = [
            StepBatch(flow_name=f"agent:{agent.name}", game_master=gm, turns=[turn])
            for turn in turns
            for agent, _spec in [turn]
        ]
        return engine._execute_batches(step_index=0, batches=batches, verbose=False)
```

Add a custom branch router:

1. Write a callable `route(agents, gms, ctx) -> {agent name: gm name}` — a plain
   function or a class whose instances are callable. Read `gms[name].backend` for
   live state, call `agent.act(...)` to involve the agent, and use `ctx.seed` /
   `ctx.flow` / `ctx.step` for a replay-stable decision. Cover every agent and
   return only GM names from `gms`.
2. Reference it from a branch node in a flow chain (`params`, if any, are bound as
   keyword arguments for a function, or constructor args for a class):

```python
# my_pkg/routers.py
def route_by_load(agents, gms, ctx, *, threshold=5):
    busy = gms["twitter_gm"].backend.recent_post_count() > threshold
    target = "reddit_gm" if busy else "twitter_gm"
    return {agent.name: target for agent in agents}
```

```yaml
env:
  gm_orchestration:
    flow_bindings:
      flow_to_gms:
        social_flow:
          - branch:
              router:
                class_path: my_pkg.routers.route_by_load
                params: {threshold: 5}
              choices: [twitter_gm, reddit_gm]
```

Add a custom probe schedule policy by implementing `should_run_probe_phase(...)`
and configuring `eval.probes.schedule.class_path`.

Add a custom probe type:

1. Subclass `ProbeBase`.
2. Implement `form_question_for_agent(agent)` and `parse_answer(raw_response)`.
3. Register the module with `eval.probes.probe_lib_module`.

Probe prompts should ask the measurement question and any answer-format
constraint. Do not inject agent identity text into probes; agents supply their
own identity/persona context when they act.

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
- [Building Agents](building_agents.md): world and builder workflows
- [Configuration Reference](configuration.md): full config keys
- [Contributing](contributing.md): lint/test workflow and coding standards
