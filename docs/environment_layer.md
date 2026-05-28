# Environment Layer: Engine, Game Master, and Backends

For API-level extension contracts (classes, method signatures, and factory hooks), see
[Simulation Extensibility API](simulation_extensibility_api.md).

The environment layer is composed of three parts:

1. **Engine**: step loop, concurrency, probe timing, and action execution orchestration.
2. **Game Master (GM)**: who acts next, what agents observe, and how action text is resolved.
3. **Environment Backend**: platform or domain state and executable actions
   (`@app_action` methods).

This page focuses on end-user and developer configurability for Engine/GM/backends.

## Design Goals

- Strong defaults that run out of the box.
- YAML-first component selection for common customization.
- Class-path extension for advanced custom components.
- Predictable `sim.action_mode` workflows (`custom`, `generic`) with explicit `sim.tool_calling.mode`.

## Current Runtime Model

- Engine remains policy-oriented (loop and scheduling concerns).
- GM follows a native component-routing style with direct typed methods.
- Backend actions are exposed as callable tools through `@app_action` methods.
- Specialized agent classes can define distinct runtime behavior (for example,
  fixed-action agents) while still using the same GM resolve components.

Flow controls use two independent settings:

- `env.gm.class_path: silisocs.environments.gm.shared_flow_game_master.FlowRoutedGameMaster`: enables GM-side component routing.
- `sim.engine.step.built_in: flow`: enables engine-side flow scheduling.

## Canonical Structure

Canonical modules:

- `src/silisocs/environments/gm/game_master.py`: primary GM builder/runtime entry.
- `src/silisocs/environments/gm/components/`: native slot components.
- `src/silisocs/simulation_engines/base_engines.py`: primary runtime engine.
Import from the `environments/gm/` and `simulation_engines/` packages.

## GM Component Slots (YAML)

Configure GM behavior from `env.gm.components`:

```yaml
env:
  gm:
    preset: base
    components:
      next_acting:
        built_in: activity_markov
        class_path: null
        params: {}

      observe:
        built_in: timeline_every_turn
        class_path: null
        params: {}

      resolve:
        built_in: parsed_action
        class_path: null
        params: {}
```

Configured `params` are strict constructor arguments. Unknown keys fail before
the simulation starts unless the target class accepts `**kwargs`. Observe
components that explicitly accept `observation_params` may use `params` as a
forwarded observation-settings bag.

### Built-in Next-Acting Components

- `activity_markov`: role-conditioned active/inactive transitions (baseline behavior).
- `all_agents`: all agents are active each step.
- `fixed_order`: one active agent in cyclic order.

### Built-in Observe Components

- `app_observation`: call `BackendApp.observe(...)` for generic backends.
- `timeline_every_turn`: fetch timeline whenever observation is requested.
- `episode_only`: return episode-only observations (for fixed/pre-scripted flows).

### Built-in Resolve Components

- `parsed_action`: parse `ACTION TYPE / TARGET ID / CONTENT / REASONING` output.
- `generic_action`: parse `ACTION: <name>` and `param: value` lines.
- `tool_calling`: use model tool-calling directly over backend action schemas.

Fixed-action directive handling:

- Fixed-action entities should emit standard action text directly (for example,
  the existing `ACTION TYPE / TARGET ID / CONTENT / REASONING` format), so
  no resolve-component modification is required.
- GM observe components can branch by agent flow type and return specialized
  observations (for example `EPISODE: <n>`).

### Runtime Initializers

Backend setup is owned by each runtime game master. The default runtime
initializer runs agent memory setup, asks each GM to initialize its backend app
and graph state, then posts seed content before the episode loop starts.

Built-in backend initializer slots:

- `social_media`: create users, wire follow/subreddit graphs, and bind social
  action metadata.
- `app_initialize`: call `app.initialize(...)` for generic non-social backends.
- `none`: skip backend setup.

### Built-in Recommendation Components

- `social_recommendation`: update social recommendation state.
- `disabled` / `none`: no-op component for generic or non-recsys environments.

## Tool-Calling Resolve Mode

`tool_calling` is a full resolve pathway, not a parser branch.

It uses backend tools generated from `@app_action` and can include both:

- YAML action guidance (`env.gm.components.action_prompt.params.action_prompt`)
- auto-generated backend action catalog

This supports both styles:

- custom YAML guidance and action constraints
- automatic action API discovery from backend code

## Quick Customization Recipes

### 1. Switch to tool-calling without Python changes

```sh
uv run silisocs \
  env.gm.components.resolve.built_in=tool_calling \
  sim.tool_calling.mode=single
```

### 2. Keep defaults but override only Game Master initialization

```yaml
env:
  gm:
    initializer:
      class_path: my_scenario.gm.CustomInitializer
      params:
        graph_strategy: role_weighted
```

### 3. Force all agents active each step

```sh
uv run silisocs env.gm.components.next_acting.built_in=all_agents
```

## Writing Custom GM Components

Each component can be swapped via `class_path`.

- Observe component: subclass the native observation component base and implement `make_observation(agent_name)`.
- Resolve component: use the native GM helper bases in `silisocs.environments.gm.components.base` and implement `resolve_action(agent_name, action)`.
- Next-acting component: subclass the native next-acting component bases and implement `acting_agent_names()`.
- Game Master initializer: implement `initialize(...)` (used during the
  Engine's Game Master initialization phase).

Use built-ins under `src/silisocs/environments/gm/components/` as templates.

### Why native component inheritance?

Pros:

- Components fit directly into native GM routing through explicit slot methods.
- You get consistent checkpoint state behavior (`get_state`, `set_state`).
- Easier interoperability with other Silisocs components.

Tradeoffs:

- Slightly more boilerplate than plain strategy objects.
- You need to follow the component contracts carefully.

This is not a meaningful runtime-overhead concern; the main tradeoff is API discipline and code structure.

## Building a Full Custom GM

YAML slot switching is ideal for most use cases, but you can still replace the entire GM runtime.

1. Implement a native GM class exposing `initialize`, `acting_agents`,
   `action_prompt`, `make_observation`, and `resolve_action`.
2. Point config to the GM `class_path` and constructor `params`.

Typical configuration path:

- `env.gamemaster.sim_role.module_path`

This allows full control over custom inputs/outputs, component graph wiring, and orchestration logic beyond slot-level swaps.

## Pre-Built GM Defaults

The baseline game master is pre-wired with default components. Users do not need
slot-level selection for normal runs.

For incremental customization, override only one component slot in YAML and keep
other slots at baseline defaults.

## Engine Extensibility Layout

Engine extensibility lives under:

- `src/silisocs/simulation_engines/base.py`
- `src/silisocs/simulation_engines/base_engines.py`
- `src/silisocs/simulation_engines/policies/action_chunk.py`
- `src/silisocs/simulation_engines/policies/probe_schedule.py`
- `src/silisocs/simulation_engines/policies/factory.py`

Configure engine turn policy from `sim.engine` and probe timing from `evals`:

```yaml
sim:
  engine:
    step:
      built_in: flow
      params:
        flow_order: [fixed_pre, default]
        agent_to_flow: {}

    turn_policy:
      built_in: single_action   # single_action | fixed_count | open_ended
      class_path: null
      params: {}

evals:
  probes:
    schedule:
      built_in: step_schedule   # step_schedule | fixed_interval | disabled
      class_path: null
      params: {}
```

Built-in turn policies:

- `single_action`: one observe/act/resolve cycle per active agent.
- `fixed_count`: exactly `count` cycles per active agent.
- `open_ended`: keep acting until `finished_action_signal` or `max_actions` is reached.

Built-in probe schedule policies:

- `step_schedule`: defer to probe orchestrator schedule.
- `fixed_interval`: trigger on `start_step` + every `every_n_steps`.
- `disabled`: never run probe phase.

Policy `params` are strict constructor arguments, matching the GM component
contract.

Engine remains separate from GM component routing by design.

Flow routing notes:

- `flow_order` defines execution buckets per episode.
- Agents in each flow bucket still execute in parallel.
- Flow buckets execute sequentially, enabling deterministic pre/post phases
  for specialized agent classes without sacrificing per-bucket parallelism.
- Assign classes to buckets using `persona_pipeline.classes.<name>.flow_tag`.
- Add one-off overrides with `sim.engine.step.params.agent_to_flow` when a
  specific agent should move to a different phase.
- Use `env.gm.components.observe.params.episode_observation_flows` for flows
  that should receive episode-index observations instead of timeline content.

## Shared-Flow GM Contract

`FlowRoutedGameMaster` is an advanced GM class for routing multiple
component instances by flow. It uses the same runtime contracts as the baseline
GM:

- Backend apps are created through the backend factory with the configured
  `env.backend.class_path` and `env.backend.params`.
- Action events are written through the standard action event logger.
- Game Master initialization receives agent names, simulation roles, and
  social-network config through the configured initializer component.
- Open-ended turn policies expose `FINISHED` as an enabled backend action.

This keeps simple and shared-flow runs interchangeable from the backend and
artifact perspective. Custom shared-flow components should specialize routing,
not bypass backend initialization or logging.

## Recommended Boundary

- Put **phase scheduling and chunk semantics** in Engine policies.
- Put **next actor, observe, resolve, update, and initializer behavior** in GM components.
- Put **platform action semantics** in backend `@app_action` methods.

For non-social domains, subclass `BackendApp`, provide generic
`observe(...)`, use `app_observation`, and disable recommendation scheduling.
Use `SocialBackendApp` only when the backend needs social timeline, feed, or
recommendation capabilities.

## Action Prompt Pipeline Implementation

For detailed configuration and behavior of action prompts, see `docs/configuration.md` under **Action Prompt Additions Configuration** and **How Action Prompts Are Constructed**.

### Implementation Modules

The action prompt pipeline is implemented across three layers:

1. **Runner-time compilation** (`src/silisocs/runtime/action_prompts.py`):
   - `build_complete_action_prompt_for_runner()` — entry point, compiles prompt from config
  - `compile_action_prompt()` — core logic that applies additions (action count guidance + output style handling)
   - All prompt building happens here before GM/app instantiation

2. **Game master prompt assembly** (`src/silisocs/environments/gm/base_game_master.py`):
   - `GameMaster.action_prompt()` returns typed `ActionSpec`
   - If `enable_tool_calling=True`: returns `OutputType.TOOL_CALLS` with schemas in `extra_args["tools"]`
   - Keeps base prompt text unchanged

3. **Agent act layer** (`src/silisocs/agents/base_agent.py` and `src/silisocs/agents/native.py`):
    - Native agents build context in `act(...)`
    - `Agent._call_model(context, action_spec)` routes typed `ActionSpec.output_type` and `extra_args` to the language model

### Key Architectural Property

**Output format stripping:** When `tool_calling.mode != none`, the `[OUTPUT STYLE]` section is automatically stripped from the final prompt. Tool-calling uses JSON format (determined by LLM, not by text instruction). This is enforced in `compile_action_prompt()` at runner time, not downstream.

### Testing

Integration tests validate the complete prompt pipeline in `tests/test_prompt_pipeline_integration.py`.
