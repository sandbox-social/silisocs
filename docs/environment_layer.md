# Environment Layer: Engine, Game Master, and Backends

For API-level extension contracts (classes, method signatures, and factory hooks), see
[Simulation Extensibility API](simulation_extensibility_api.md).

The environment layer is composed of three parts:

1. **Engine**: step loop, concurrency, probe timing, and action execution
   orchestration. The turn executor is pluggable via `sim.engine.executor`
   (`threads`, the default, or `asyncio`); see `docs/configuration.md`.
2. **Game Master (GM)**: who acts next, what agents observe, and how action text is resolved.
3. **Environment Backend**: domain state and executable actions
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

- `env.gm.class_path: silisocs.environments.gm.game_master.MultiFlowGameMaster`: enables GM-side component routing.
- `sim.engine.step.built_in: flow`: enables engine-side flow scheduling.
- `sim.engine.step.built_in: multi_gm`: enables flow scheduling across multiple
  configured Game Masters.

## Canonical Structure

Canonical modules:

- `src/silisocs/environments/gm/game_master.py`: primary component-GM runtime.
- `src/silisocs/environments/gm/components/`: native slot components.
- `src/silisocs/simulation_engines/base_engines.py`: primary runtime engine.
Import from the `environments/gm/` and `simulation_engines/` packages.

## GM Component Slots (YAML)

Configure GM behavior from `env.gm.components`:

```yaml
env:
  gm:
    components:
      next_acting:
        built_in: all_agents
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

- `all_agents`: all agents are active each step.
- `fixed_order`: one active agent in cyclic order.

This slot is the home of environment-derived selection. Config-derived activity
models (`activity_probability`, `activity_markov`) are sim-level participation
policies — `sim.engine.participation` — applied to the step roster before every
GM's next_acting runs (effective acting = participation ∩ next_acting). See
`docs/configuration.md` ("Social Setup and Participation").

### Built-in Observe Components

- `app_observation`: call `BackendApp.observe(...)`.
- `timeline_every_turn`: fetch timeline whenever observation is requested.
- `episode_only`: return episode-only observations (for fixed/pre-scripted flows).

`timeline_every_turn` is social-media-specific and lives under
`silisocs.environments.gm.components.social_media`. `app_observation` and
`episode_only` are generic baselines for backends that do not use timeline components.

### Built-in Resolve Components

- `parsed_action`: parse `ACTION TYPE / TARGET ID / CONTENT / REASONING` output.
- `generic_action`: parse `ACTION: <name>` and `param: value` lines.
- `tool_calling`: use model tool-calling directly over backend action schemas.

Fixed-action directive handling:

- Fixed-action agents should emit standard action text directly (for example,
  the existing `ACTION TYPE / TARGET ID / CONTENT / REASONING` format), so
  no resolve-component modification is required.
- GM observe components can branch by agent flow type and return specialized
  observations (for example `EPISODE: <n>`).

### Initialization Components

Engine startup runs agent initialization, Game Master initialization, then
simulation initialization. Backend setup is owned by each Game Master through
its `components.initialize` slot. Seed content is simulation initialization and
is posted later through normal `resolve_action(...)` calls.

Built-in Game Master initialize components:

- `social_media`: create users, wire follow/subreddit graphs, and bind social
  action metadata.
- `app_initialize`: call `BackendApp.initialize(...)`.
- `none`: skip backend setup.

### Built-in Recommendation Components

- `app_update`: call `BackendApp.update(...)` for backend-owned world ticks.
- `social_recommendation`: update recommendation state through the backend
  capability methods.
- `disabled` / `none`: explicit no-op update component.

Use `app_update` when world state should advance before actor selection.
Use `disabled`/`none` when no per-step update is needed. Components such as
`social_recommendation` may call backend-specific capability methods instead of
the generic update hook.

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
    components:
      initialize:
        class_path: my_world.gm.CustomInitializer
        params:
          graph_strategy: role_weighted
```

### 3. Force all agents active each step

```sh
uv run silisocs env.gm.components.next_acting.built_in=all_agents
```

## Writing Custom GM Components

Each component can be swapped via `class_path`. Component constructors should
use explicit keyword parameters. The factory injects common runtime values
such as `backend`, `model`, `agent_names`, `sim_roles`, and `agent_flow_tags`
when the constructor accepts them.

- Initialize component: subclass `InitializeComponent` and implement
  `initialize(*, agents, gm_context, context) -> None`.
- Next-acting component: subclass `NextActingComponent` and implement
  `acting_agent_names() -> list[str]`.
- Action-prompt component: subclass `ActionPromptComponent` and implement
  `action_prompt(agent_name) -> ActionSpec`.
- Observe component: subclass `ObservationComponent` and implement
  `make_observation(agent_name) -> str`.
- Resolve component: subclass `ResolveComponent` and implement
  `resolve_action(agent_name, action) -> str`.
- Update component: subclass `UpdateComponent` and implement
  `update(*, step, agents, context=None) -> None`.

Stateful components may override `get_state()` and `set_state(state)` for
checkpoint resume. Keep backend domain state in the backend; component state is
for component-local cursors, caches, or policy state.

Use built-ins under `src/silisocs/environments/gm/components/` as templates.

### Hot-swapping a component mid-run

`ComponentGameMaster.rebuild_component(role_key, slot_cfg)` is the seam for
replacing a whole component while a run is live (used by the `swap_component`
intervention). It rebuilds the component from its slot config with the GM's live
wiring, then re-points both the typed slot attribute and the component registry.
Only STATELESS components (empty `get_state()`) are swappable — a component that
carries state (a `fixed_order` cursor, the recsys updater) is refused, and should
be retuned via `set_params` / the `set_component_params` intervention instead.
The v1 whitelist is `observe` / `next_acting` / `update`; `resolve` and
`action_prompt` are excluded because they are validated as a pair against the
GM's tool-calling mode.

### Why native component inheritance?

Pros:

- Components fit directly into native GM routing through explicit slot methods.
- You get consistent checkpoint state behavior (`get_state`, `set_state`).
- Easier interoperability with other SiliSocS components.

Tradeoffs:

- Slightly more boilerplate than plain strategy objects.
- You need to follow the component contracts carefully.

This is not a meaningful runtime-overhead concern; the main tradeoff is API discipline and code structure.

## Building a Full Custom GM

YAML slot switching is ideal for most use cases, but you can still replace the entire GM runtime.

1. Implement a native GM class exposing `initialize`, `update`, `acting_agents`,
   `action_prompt`, `make_observation`, and `resolve_action`.
2. Point config to the GM `class_path` and constructor `params`.

Typical configuration path:

- `env.gm.class_path`

This allows full control over custom inputs/outputs, component graph wiring, and orchestration logic beyond slot-level swaps.

## Pre-Built GM Defaults

The baseline game master is pre-wired with default components. Users do not need
slot-level selection for normal runs.

For incremental customization, override only one component slot in YAML and keep
other slots at baseline defaults.

## Engine Extensibility Layout

Engine extensibility lives under:

- `src/silisocs/simulation_engines/base_engines.py`
- `src/silisocs/simulation_engines/runtime_base.py`
- `src/silisocs/simulation_engines/policies/loops.py`
- `src/silisocs/simulation_engines/policies/steps.py`
- `src/silisocs/simulation_engines/policies/turns.py`
- `src/silisocs/simulation_engines/policies/probe_schedule.py`
- `src/silisocs/simulation_engines/policies/factory.py`

Configure engine turn policy from `sim.engine` and probe timing from `eval`:

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
      params:
        observe_before_act: first  # first | always | never

eval:
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
  specific agent should move to a different phase. Override keys must match
  final Agent names.
- Use `env.gm.components.observe.params.episode_observation_flows` for flows
  that should receive episode-index observations instead of timeline content.
- In `multi_gm` mode, every configured GM runs `update(...)` once at the start
  of the episode step, before flow routing and actor selection. Flow chains only
  route selected agent turns through GMs.
- Flow chain traversal mode is selected by `sim.engine.step.built_in` via three
  multi-GM step strategies (the former `sim.engine.step.params.chain_execution`
  knob has been removed; a config that still sets it raises a `ValueError` with a
  migration hint):
  - `multi_gm` (DEFAULT, concurrent): flows run as independent pipelines through
    their GM chains. Distinct flows advance concurrently, and a flow's next chain
    hop starts as soon as its own previous hop completes; turns serialize only
    when two flows touch the same GM at the same time (enforced by the engine's
    existing per-GM lock). Flows listed in `flow_order` run first as a strict
    serial prefix, preserving declared precedence such as seed-then-act
    (`fixed_pre` before `default`); every other flow runs as the concurrent
    group. A single agent's own chain hops always stay serial, since each hop
    observes the prior hop's resolution. This is the unchanged default behavior.
  - `multi_gm_serial`: legacy row-major — each flow runs its full GM chain to
    completion before the next flow, one batch at a time, in deterministic
    flow-by-flow order.
  - `multi_gm_staged`: column-major with a global per-stage barrier — `flow_order`
    flows run first as a serial prefix, then every remaining flow advances one
    stage at a time, with all flows' stage-N hops running concurrently and stage
    N+1 blocked until all of stage N finishes. A `null` entry in a flow's
    `flow_to_gms` chain (an empty slot) idles that flow at the stage so
    differently-shaped chains stay stage-aligned.

Multi-GM orchestration is configured under `env.gm_orchestration`. Each
orchestrated GM must declare its own strict `backend` and `components` blocks;
missing nested keys are not filled from the default `env.gm`. Flow chains must
reference known GM names, must contain at least one real (non-null) GM, cannot
repeat a GM, and must move through strictly increasing GM `sequence` values
across their real GMs, which fixes each chain's stage ordering regardless of the
traversal mode.

A chain entry may also be a **branch node** — `{branch: {router, choices}}` — that
routes each of the flow's agents to one of `choices` (alternative GMs) at that
stage. The router is a `{built_in | class_path, params}` slot built by the engine
(`build_router`) into a **plain callable**
`route(agents, gms, ctx) -> {agent name: gm name}` — no base class. Built-ins are
`random` (weighted, deterministic per `(seed, flow, step, agent)`) and `agent_choice`
(asks each agent via a CHOICE probe); a `class_path` (a function or a callable class)
is the "custom function chooses" seam. The branch is a single chain stage; the engine
runs the router when the flow's chain reaches it — after the flow's earlier hops
drain, so the router sees live backend state and may call the agents — in all three
`multi_gm*` traversals, and fans the flow's agents across its choices while the shared
pre/post hops still run once on every agent. A branch requires a `multi_gm*` mode, may
not sit in a `flow_order` flow, and its choices' sequences must lie strictly between
the branch's chain neighbours. Restore and per-GM owned-flow derivation treat a branch
as "any of its choices" (`collapse_flow_chains` / `flow_chain_gm_names`).

Final Agent flow tags are materialized during construction and passed to every
Game Master. The Engine does not re-apply `agent_to_flow` at turn time.

## Shared-Flow GM Contract

`MultiFlowGameMaster` is an advanced GM class for routing multiple
component instances by flow. It uses the same runtime contracts as the baseline
GM:

- Backend apps are created through the backend factory with the configured
  `env.gm.backend.class_path` and `env.gm.backend.params`.
- Action events are written through the standard action event logger.
- Game Master initialization receives agent names, simulation roles, and
  component-owned initializer params through the configured initializer component.
- Open-ended turn policies expose `FINISHED` as an enabled backend action.

This keeps simple and shared-flow runs interchangeable from the backend and
artifact perspective. Custom shared-flow components should specialize routing,
not bypass backend initialization or logging.

## Recommended Boundary

- Put **loop, step, and turn scheduling** in Engine policies.
- Put **next actor, observe, resolve, update, and initializer behavior** in GM components.
- Put **backend action semantics** in backend `@app_action` methods.

For new environment domains, subclass `BackendApp`, expose actions with
`@app_action`, provide `observe(...)`, and use `app_observation`. Add
`update(...)` plus `app_update` when the world needs a per-step tick. Use
`SocialBackendApp` only when the backend needs the timeline, feed, parsed
social action, or recommendation capability surface.

## Action Prompt Pipeline Implementation

For detailed configuration and behavior of action prompts, see `docs/configuration.md` under **Action Prompt Additions Configuration** and **How Action Prompts Are Constructed**.

### Implementation Modules

The action prompt pipeline is implemented across three layers:

1. **Runner-time compilation** (`src/silisocs/runtime/prompts/action_prompts.py`):
   - `compile_action_prompt()` applies prompt additions such as action-count guidance
     and output-style handling.
   - Generic prompts are compiled during GM construction from the backend action catalog.

2. **Game master prompt assembly** (`src/silisocs/environments/gm/game_master.py`):
   - `ComponentGameMaster.action_prompt()` returns typed `ActionSpec`
   - If `enable_tool_calling=True`: returns `OutputType.TOOL_CALLS` with schemas in `extra_args["tools"]`
   - Keeps base prompt text unchanged

3. **Agent act layer** (`src/silisocs/agents/base_agent.py` and `src/silisocs/agents/native.py`):
    - Native agents build context in `act(...)`
    - `Agent._call_model(context, action_spec)` routes typed `ActionSpec.output_type` and `extra_args` to the language model

### Key Architectural Property

**Output format stripping:** When `tool_calling.mode != none`, the `[OUTPUT STYLE]` section is automatically stripped from the final prompt. Tool-calling uses JSON format (determined by LLM, not by text instruction). This is enforced in `compile_action_prompt()` at runner time, not downstream.

### Testing

Integration tests validate the complete prompt pipeline in `tests/test_prompt_pipeline_integration.py`.
