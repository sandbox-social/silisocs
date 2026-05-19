# Native Runtime Simplification Plan

## Summary

Silisocs should keep the responsibility boundaries that make the simulation easy
to reason about:

- agents observe text and return action text;
- game masters decide who acts, what each agent sees, and how raw actions are
  resolved;
- backends own platform or environment state plus executable actions;
- engines own step order, concurrency, probes, checkpoints, and flow scheduling.

The recent migration already moved the default package away from an external
Concordia dependency. The remaining cleanup is to stop routing ordinary runtime
code through Concordia-shaped compatibility names. Existing scenario YAML and
run artifacts should remain stable while the internals become native,
discoverable, and smaller.

Chosen defaults:

- Keep a tiny native `ActionSpec` as the agent request contract.
- Keep existing YAML config keys stable.
- Quarantine optional Concordia compatibility behind `silisocs.adapters.concordia`.
- Make native modules the only default extension surface.

## Target Shape

### Agents

Agents implement `silisocs.agents.base_agent.Agent`:

- `name -> str`
- `observe(observation: str) -> None`
- `act(action_spec: ActionSpec) -> str`

Stateful agents optionally implement `get_state()` and `set_state(state)`.
Default LLM agents should be composed from native context components and a
native concat-act component. Fixed agents should remain plain deterministic
agents with no component-system dependency.

### Game Masters

The default GM should be understandable as a direct coordinator around slots:

- next-acting component chooses active agents;
- observe component builds the observation for an agent;
- resolve component executes raw action text against the backend;
- recommendation and initializer components perform passive backend updates.

Short term, the existing component lifecycle can stay, but its classes and
constants must live in native modules. Longer term, GM methods should call slot
objects directly instead of passing internal control-plane `ActionSpec` values
through a switch-act compatibility layer.

### Engines

Engines should not inherit from any compatibility facade. `BaseRuntimeEngine`
should directly implement:

- `make_observation(game_master, entity)`;
- `next_game_master(game_master, game_masters)`;
- `next_acting(game_master, entities)`;
- `agent_resolve(game_master, action)`;
- `run_loop(...)`.

`FlowRuntimeEngine` and `MultiGMRuntimeEngine` should remain thin
specializations for flow grouping and GM sequencing.

### Backends

Backends are already mostly native and should stay clean:

- subclass `EnvironmentApp` or `SocialMediaApp`;
- implement `initialize(...)` and optional `observe(...)`;
- expose executable actions with `@app_action`;
- keep platform-specific action parsing inside backend or resolve components.

### Adapter Boundary

`silisocs.adapters.concordia` should become a bridge-only package. Normal
runtime modules must import from `silisocs.core`, `silisocs.agents`,
`silisocs.environments`, `silisocs.simulation_engines`, or `silisocs.utils`.
Legacy examples may use the adapter while being ported.

## Implementation Phases

1. **Save and enforce the boundary**
   - Add an import-boundary test that fails when normal runtime code imports
     `silisocs.adapters.concordia`.
   - Allow only adapter code, bridge docs/tests, and quarantined legacy examples.

2. **Move native code out of the adapter**
   - Create native modules for prompt documents, HTML helpers, generic helper
     functions, concurrency helpers, memory bank compatibility, agent context
     components, and GM routing components/constants.
   - Keep the adapter as aliases to those native modules for old code.

3. **Migrate runtime imports**
   - Update agents, initializers, GM components, GMs, engines, probes, model
     utilities, memory utilities, runtime construction, and telemetry to import
     native contracts directly.
   - Preserve public config keys and output artifacts.

4. **Simplify the control flow**
   - Remove engine inheritance from adapter `simultaneous`.
   - Collapse adapter `engine_lib.action_spec_parser` usage to
     `silisocs.core.types.action_spec_parser`.
   - Prefer `.name` and helper accessors over direct `_agent_name` access.

5. **Future direct-GM cleanup**
   - Once adapter imports are gone, replace internal GM control-plane
     `ActionSpec` dispatch with explicit GM methods and slot protocols.
   - Convert memory initialization from a GM-as-agent flow into a direct setup
     stage.

## Test Plan

- Boundary tests for direct `concordia.*` imports and default-runtime adapter
  imports.
- Unit tests for native `ActionSpec`, native context components, GM routing
  components, memory bank, prompt document, and helper utilities.
- Regression tests for tool calling, structured probes, parsed/generic resolve,
  fixed agents, recommendation flow selection, shared-flow mapping, engine flow
  policy routing, checkpoint save/resume, and runner processing modes.
- Smoke runs with disabled LLM for base engine, flow engine, shared-flow GM, and
  fixed-action scenarios.

## Assumptions

- Current scenario YAML remains valid.
- Current output file names remain valid.
- Concordia interoperability is optional and bridge-scoped.
- Old checkpoints may require adapter compatibility during migration.
- The current uncommitted migration branch is the implementation baseline and
  must not be reverted.
