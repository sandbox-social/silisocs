# Environment Layer: Engine, Game Master, and Backends

The environment layer is composed of three parts:

1. **Engine**: step loop, concurrency, probe timing, and action execution orchestration.
2. **Game Master (GM)**: who acts next, what agents observe, and how action text is resolved.
3. **Social Media Backend**: platform state and executable actions (`@app_action` methods).

This page focuses on end-user and developer configurability for Engine/GM/backends.

## Design Goals

- Strong defaults that run out of the box.
- YAML-first component selection for common customization.
- Class-path extension for advanced custom components.
- Backward compatibility with existing `sim.action_mode` workflows.

## Current Runtime Model

- Engine remains policy-oriented (loop and scheduling concerns).
- GM follows a **Concordia-native component-routing** style around SwitchAct.
- Backend actions are exposed as callable tools through `@app_action` methods.
- Specialized entity classes can define distinct runtime behavior (for example,
  fixed-action entities) while still using the same GM resolve components.

## Canonical Structure

Canonical modules:

- `src/mastodon_sim/environments/gm/game_master.py`: primary GM prefab.
- `src/mastodon_sim/environments/gm/act.py`: primary SwitchAct specialization.
- `src/mastodon_sim/environments/gm/components/`: Concordia-native slot components.
- `src/mastodon_sim/environments/engines/social_media.py`: primary social media engine.

Import from the `environments/gm/` and `environments/engines/` packages.

## GM Component Slots (YAML)

Configure GM behavior from `sim.gm.components`:

```yaml
sim:
  gm:
    preset: social_media_default
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

      initializer:
        built_in: backend_default
        class_path: null
        params: {}
```

### Built-in Next-Acting Components

- `activity_markov`: role-conditioned active/inactive transitions (baseline behavior).
- `all_entities`: all entities are active each step.
- `fixed_order`: one active entity in cyclic order.

### Built-in Observe Components

- `timeline_every_turn`: fetch timeline whenever observation is requested.
- `chunk_start_only`: fetch on chunk start token only (forward-compatible with multi-action chunks).

### Built-in Resolve Components

- `parsed_action`: parse `ACTION TYPE / TARGET ID / CONTENT / REASONING` output.
- `generic_action`: parse `ACTION: <name>` and `param: value` lines.
- `tool_calling`: use model tool-calling directly over backend action schemas.

Fixed-action directive handling:

- Fixed-action entities should emit standard action text directly (for example,
  the existing `ACTION TYPE / TARGET ID / CONTENT / REASONING` format), so
  no resolve-component modification is required.
- GM observe components can branch by entity flow type and return specialized
  observations (for example `EPISODE: <n>`).

### Built-in Initializer Components

- `backend_default`: standard `sm_app.initialize(...)` call.

## Tool-Calling Resolve Mode

`tool_calling` is a full resolve pathway, not a parser branch.

It uses backend tools generated from `@app_action` and can include both:

- YAML action guidance (`social_media.action_call_to_action`)
- auto-generated backend action catalog

This supports both styles:

- custom YAML guidance and action constraints
- automatic action API discovery from backend code

## Quick Customization Recipes

### 1. Switch to tool-calling without Python changes

```sh
uv run mastodon-sim sim.gm.components.resolve.built_in=tool_calling
```

### 2. Keep defaults but override only initializer

```yaml
sim:
  gm:
    components:
      initializer:
        class_path: my_scenario.gm.CustomInitializer
        params:
          seed_strategy: role_weighted
```

### 3. Force all entities active each step

```sh
uv run mastodon-sim sim.gm.components.next_acting.built_in=all_entities
```

## Writing Custom GM Components

Each component can be swapped via `class_path`.

- Observe component: subclass Concordia `MakeObservation` and implement `pre_act(...)`.
- Resolve component: subclass Concordia `ContextComponent` (and usually `ComponentWithLogging`) and implement `pre_act(...)`.
- Next-acting component: subclass Concordia next-acting components (for example `NextActingAllEntities` or `NextActingInFixedOrder`) and implement/override `pre_act(...)`.
- Backend initializer: implement `initialize(...)` (non-Concordia hook, used before simulation loop).

Use built-ins under `src/mastodon_sim/environments/gm/components/` as templates.

### Why Concordia-native inheritance?

Pros:

- Components fit directly into SwitchAct routing via context keys.
- You get Concordia lifecycle/state behavior (`pre_act`, `get_state`, `set_state`).
- Easier interoperability with other Concordia components.

Tradeoffs:

- Slightly more boilerplate than plain strategy objects.
- You need to follow Concordia component contracts carefully.

This is not a meaningful runtime-overhead concern; the main tradeoff is API discipline and code structure.

## Building a Full Custom GM

YAML slot switching is ideal for most use cases, but you can still replace the entire GM prefab.

1. Implement a new prefab class (typically by inheriting from `gm.game_master.GameMaster` and overriding `build(...)`, or by implementing a fresh Concordia prefab).
2. Point scenario/social config to your module path and prefab name.

Typical configuration path:

- `social_media.gamemaster.sim_role.module_path`

This allows full control over custom inputs/outputs, component graph wiring, and orchestration logic beyond slot-level swaps.

## Pre-Built GM Defaults

The baseline game master is pre-wired with default components. Users do not need
slot-level selection for normal runs.

For incremental customization, override only one component slot in YAML and keep
other slots at baseline defaults.

## Engine Extensibility Layout

Engine extensibility lives under:

- `src/mastodon_sim/environments/engines/base.py`
- `src/mastodon_sim/environments/engines/social_media.py`
- `src/mastodon_sim/environments/engines/policies/action_chunk.py`
- `src/mastodon_sim/environments/engines/policies/probe_schedule.py`
- `src/mastodon_sim/environments/engines/policies/factory.py`

Configure engine policies from `sim.engine`:

```yaml
sim:
  engine:
    action_loop:
      built_in: single_action   # single_action | fixed_count | open_ended
      class_path: null
      params: {}

    probe_schedule:
      built_in: step_schedule   # step_schedule | fixed_interval | disabled
      class_path: null
      params: {}

    flow_routing:
      flow_order: [fixed_pre, default]
      entity_to_flow: {}
```

Built-in action loop policies:

- `single_action`: one observe/act/resolve cycle per active entity.
- `fixed_count`: exactly `count` cycles per active entity.
- `open_ended`: keep acting until `done_token` or `max_actions` is reached.

Built-in probe schedule policies:

- `step_schedule`: defer to probe orchestrator schedule.
- `fixed_interval`: trigger on `start_step` + every `every_n_steps`.
- `disabled`: never run probe phase.

Engine remains separate from GM component routing by design.

Flow routing notes:

- `flow_order` defines execution buckets per episode.
- Agents in each flow bucket still execute in parallel.
- Flow buckets execute sequentially, enabling deterministic pre/post phases
  for specialized entity classes without sacrificing per-bucket parallelism.
- Assign classes to buckets using `persona_pipeline.classes.<name>.params.action_flow`.
- Add one-off overrides with `sim.engine.flow_routing.entity_to_flow` when a
  specific entity should move to a different phase.
- Use `sim.gm.components.observe.params.episode_observation_flows` for flows
  that should receive episode-index observations instead of timeline content.

## Recommended Boundary

- Put **phase scheduling and chunk semantics** in Engine policies.
- Put **next actor, observe, resolve, initializer behavior** in GM components.
- Put **platform action semantics** in backend `@app_action` methods.
