# Core And Adapter Separation

## Purpose

Silisocs now treats its own runtime contracts as the primary framework surface.
The Concordia-shaped API is isolated behind an adapter boundary so default
installs, imports, and new extensions do not need to understand Concordia.

The core rule is:

- New code should import from `silisocs.core`, `silisocs.agents`,
  `silisocs.environments`, and `silisocs.simulation_engines`.
- Legacy Concordia-shaped code should go through `silisocs.adapters.concordia`.
- Direct `concordia.*` imports should not appear in runtime code, tests, or
  scenario helpers outside the adapter package.

## What Lives In Core

The native core package contains small, explicit runtime contracts:

- `silisocs.core.types`: `ActionSpec`, `OutputType`, action-spec parsing, and
  skip-step helpers.
- `silisocs.core.prefab`: `Prefab`, `Config`, `InstanceConfig`, and `Role`.
- `silisocs.core.components`: context components, component logging, native
  phase state, and component-owning entities.
- `silisocs.core.models`: native language-model interface, no-op model,
  sampling helpers, and measurement helpers.

These are intentionally direct. A newcomer should be able to understand the
runtime lifecycle by reading Silisocs modules rather than tracing through
Concordia package internals.

## What Lives In The Adapter

`silisocs.adapters.concordia` is the compatibility boundary. It provides the
small Concordia-shaped surface that historical Silisocs code used, but backed by
native Silisocs contracts.

Examples include:

- `entity_lib.ActionSpec` and `entity_lib.OutputType`
- `prefab_lib.Prefab`, `prefab_lib.Config`, and `prefab_lib.InstanceConfig`
- `entity_component.ContextComponent` and `ComponentWithLogging`
- `EntityAgentWithLogging`-compatible component entity behavior
- Switch-act, next-acting, observation, resolve, memory, and prompt helper
  facades

This adapter exists to keep old Silisocs modules and Concordia-designed
components portable while we simplify internal naming over time. It should not
be treated as the recommended extension API for new code.

## Did We Move Away From Concordia Language Model Objects?

Yes for the default runtime dependency and extension surface.

The default package no longer depends on `gdm-concordia`, and the native model
contract now lives in `silisocs.core.models.LanguageModel`. `GptLanguageModel`
implements the Silisocs-facing interface through the native adapter surface
rather than requiring the external Concordia language-model class at import
time.

There is still a compatibility facade named `language_model` in
`silisocs.adapters.concordia`. That is deliberate. It lets older code that
expected `language_model.LanguageModel` continue to type-check and run while the
runtime is migrated toward clearer native names.

So the practical answer is:

- We moved away from external Concordia language-model objects as a required
  runtime dependency.
- We have not yet removed every Concordia-shaped name from internal code.
- The remaining names are compatibility shims, not hidden imports of the
  external Concordia package.

## Did We Move Away From The Obfuscated Structure?

Partially, and in the most important direction.

Before this migration, many extension points looked like Silisocs APIs but were
actually Concordia APIs underneath. A custom component, prefab, model, or GM
often needed to know Concordia inheritance and lifecycle rules even when the
user was trying to extend Silisocs.

Now the hidden dependency is surfaced as a boundary:

- Native contracts are explicit in `silisocs.core`.
- Concordia-shaped compatibility is isolated in `silisocs.adapters.concordia`.
- An import-boundary test prevents new direct `concordia.*` imports from
  creeping back into the codebase.
- Public docs now describe native extension contracts first.

The remaining cleanup is mostly naming and internal ergonomics. Some files still
import `silisocs.adapters.concordia` because the branch preserves behavior while
removing the hard external dependency. Over time those compatibility-shaped
imports should be replaced by direct native imports where it simplifies the
code.

## Old Extension Surfaces That Were Hard To Understand

### 1. Agent Prefabs

Old behavior:

- Custom agents were expected to expose an `Entity` prefab class.
- That prefab usually had to subclass `concordia.typing.prefab.Prefab`.
- The user-facing agent contract was simple (`name`, `observe`, `act`), but the
  construction path was Concordia-shaped.

Why this was confusing:

- Newcomers had to learn Concordia prefab conventions before writing a Silisocs
  agent.
- The minimal runtime interface was obscured by a heavier build protocol.
- Fixed-action and custom agents looked more Concordia-specific than they
  actually were.

Native direction:

- New prefabs should target `silisocs.core.prefab.Prefab`.
- Runtime agents should implement `silisocs.agents.base_agent.Agent`.

### 2. Default LLM Agent Composition

Old behavior:

- The default LLM agent was assembled from Concordia context components.
- Acting was routed through Concordia concat-act machinery.
- Tool-calling and structured output behavior lived inside a component that
  inherited from Concordia classes.

Why this was confusing:

- The user wanted to change agent behavior, but the relevant control flow was
  split across Silisocs files and Concordia component lifecycle assumptions.
- It was not obvious which parts were platform policy, prompt assembly, memory,
  or Concordia act plumbing.

Native direction:

- Keep the `observe`/`act` surface as the agent API.
- Move prompt, memory, and tool-calling behavior behind Silisocs-native
  components and helpers.

### 3. Game-Master Components

Old behavior:

- Observe, resolve, next-acting, recommendation, and initializer components
  inherited Concordia component classes.
- Components were selected through Silisocs YAML, but implementation depended on
  Concordia `pre_act`, state, and entity binding semantics.

Why this was confusing:

- YAML made the system look fully Silisocs-native, while component authors still
  needed Concordia lifecycle knowledge.
- Errors in `pre_act`, component keys, or state recovery often required knowing
  how Concordia component entities were wired.

Native direction:

- Component slots stay YAML-configurable.
- New components should follow native component lifecycle contracts.
- The adapter remains only for legacy Concordia-shaped components.

### 4. Action Specs And Output Types

Old behavior:

- Engines, GMs, probes, and agents passed Concordia `ActionSpec` objects and
  Concordia output-type constants.
- Some action specs were serialized as strings and parsed through Concordia
  helpers.

Why this was confusing:

- Action specs are central to Silisocs behavior, but the source of truth lived
  outside Silisocs.
- Adding a new action flow or debugging skip/resolve behavior required
  following external constants and parser behavior.

Native direction:

- `silisocs.core.types.ActionSpec` and `OutputType` are the source of truth.
- The adapter provides aliases for compatibility.

### 5. Engine And Simulation Lifecycle

Old behavior:

- The runtime engine subclassed Concordia engine classes.
- `SimulationRunner` inherited Concordia simulation behavior.
- The Silisocs engine already contained most of the real orchestration logic,
  but the base class made the dependency look deeper than it needed to be.

Why this was confusing:

- It was hard to tell which behavior came from Silisocs and which came from
  Concordia.
- Engine extension work appeared to require understanding Concordia execution
  classes even though most scheduling, probe, flow, and telemetry behavior was
  Silisocs-owned.

Native direction:

- Silisocs engines own the action loop, probe scheduling, flow routing,
  checkpoints, worker throttling, and multi-GM orchestration.
- External engine inheritance should not be required for new runtime features.

### 6. Memory And Formative Initialization

Old behavior:

- The list memory backend was mostly Silisocs-owned.
- The associative memory and default LLM-agent memory wrappers were
  Concordia-shaped.
- Formative initialization used Concordia-style interactive documents and GM
  components.

Why this was confusing:

- It was unclear whether memory was a Silisocs data structure, a Concordia
  component, or both.
- Custom initialization required touching runtime registration plus inherited GM
  wiring.

Native direction:

- Memory banks and formative prompt helpers should be native contracts.
- Initializers should expose direct Silisocs hooks and only use the adapter for
  legacy compatibility.

### 7. Probes And Checkpoint State

Old behavior:

- Probes created Concordia-shaped action specs.
- Some error recovery and checkpoint logic inspected Concordia entity/component
  phase state.

Why this was confusing:

- Evaluations looked independent, but they depended on Concordia runtime
  objects.
- Checkpoint behavior was harder to reason about for non-Concordia custom
  agents.

Native direction:

- Probes should create native `ActionSpec` values.
- Stateful custom agents should implement `get_state()` and `set_state()`.
- Legacy phase compatibility should stay in the adapter or disappear.

### 8. Documentation And Tests

Old behavior:

- Docs described Silisocs as Concordia-based.
- Tests imported Concordia-shaped libraries directly.
- Some examples taught users to inherit Concordia classes for normal extension.

Why this was confusing:

- The public story and the desired extension path were mixed together.
- New contributors could not tell whether they were extending Silisocs or
  Concordia.

Native direction:

- Docs should teach native Silisocs APIs first.
- Bridge docs should be explicit that Concordia interoperability is optional.
- Tests should enforce the adapter boundary.

## Practical Guidance For New Extensions

Use native APIs for new work:

```python
from silisocs.agents.base_agent import Agent
from silisocs.core.prefab import Prefab
from silisocs.core.types import ActionSpec


class MyAgent(Agent):
    @property
    def name(self) -> str:
        return "Alice"

    def observe(self, observation: str) -> None:
        self._last_observation = observation

    def act(self, action_spec: ActionSpec) -> str:
        return "ACTION TYPE: post\nTARGET ID: \nCONTENT: hello"


class Entity(Prefab):
    def build(self, model, memory_bank):
        return MyAgent()
```

Use the adapter only when porting older code that already assumes
Concordia-shaped names:

```python
from silisocs.adapters.concordia import prefab_lib
```

That import should be a temporary bridge, not the default way to write new
Silisocs extensions.

## Bottom Line

The migration does not just rename imports. It changes ownership.

Before, Silisocs behavior was spread across Silisocs modules, hidden Concordia
base classes, Concordia action specs, Concordia model helpers, and inherited
component lifecycle rules.

Now, Silisocs owns the runtime contracts. Concordia portability remains
available through a deliberate adapter boundary, but newcomers can extend the
framework by learning Silisocs-native agents, prefabs, components, models, and
action specs first.
