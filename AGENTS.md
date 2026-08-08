# AGENTS.md

This file is a contributor guide for LLM coding agents working in this repository.

## 1) What This Repository Is

Silisocs is a native social simulation framework with an optional Concordia
compatibility bridge for legacy scenarios. It has:

- YAML-first scenario and runtime configuration (Hydra + OmegaConf)
- A social-media game-master/environment layer
- Multiple platform backends (Twitter-like, Reddit-like, Mastodon)
- Declarative persona pipeline plus custom builder extension path
- Probe-based evaluation and rich runtime telemetry
- Studio visual workspace for scenario creation, launch, inspection, and analysis

The runtime entrypoint is:

- `src/silisocs/runtime/execution/session.py` (`runner.py` is a thin re-export
  shim kept for the `python -m silisocs.runtime.runner` invocation)

## 2) High-Level Architecture

Core runtime layers:

### 1. Agent Construction Layer
- `src/silisocs/runtime/construction/agent_builders/`
- Builds agent construction specs from `agents.persona_pipeline` and class data sources
- Supports fixed-action set loading and template rendering
- Entry point: `AgentBuilder.build_agent_configs()`

### 2. Agent Runtime Layer
- `src/silisocs/agents/base_agent.py` — Abstract Agent interface
- `src/silisocs/agents/native.py` — default LLM-backed native agent
- `src/silisocs/agents/fixed.py` — deterministic fixed-action agent
- Custom agents subclass `Agent`, accept a `LanguageModel`, implement
  `name`, `observe(str)`, and `act(ActionSpec) -> ActionOutput`
- To add a custom agent, point `persona_pipeline.classes.*.class_path` at the
  runtime class and provide strict constructor `params`

### 3. Game Master Layer (Component-Slotted Architecture)
- `src/silisocs/environments/gm/base_game_master.py` — Base coordinator
- `src/silisocs/environments/gm/game_master.py` — ComponentGameMaster and MultiFlowGameMaster
- `src/silisocs/environments/gm/components/` — Pluggable components:
  - `next_acting.py` — Determine which agent acts next
  - `observe.py` — Generate timeline/episode observations
  - `resolve.py` — Parse agent output into backend actions
  - `app_update.py` — Schedule backend/recommendation updates
- To add custom component: implement `Component` interface, set in `env.gm.components.{role}.class_path`

### 4. Engine Layer (Execution Policies)
- `src/silisocs/simulation_engines/base_engines.py` — `RuntimeEngine`, the single
  strategy-driven engine (startup, per-step GM updates, action concurrency, retry
  telemetry, probe phases). Scheduling/traversal is owned entirely by the step
  strategy; `build_engine` selects it from `sim.engine.step.built_in`. Adding a new
  traversal means writing a step strategy and registering it — no new engine class.
- `src/silisocs/simulation_engines/policies/` — loop, step, and turn policies:
  - Turn policy: `single_action`, `fixed_count`, `open_ended`
  - Step policy: `base`, `sequential`, `flow`, `multi_gm`, `multi_gm_serial`, `multi_gm_staged`
  - Loop policy: default episode loop
- To add custom policy: implement the relevant policy ABC and reference it via `class_path`

### 5. Backend Action Layer
- `src/silisocs/environments/backends/base.py` — ActionCatalog, base app interface
- `src/silisocs/environments/backends/round_game.py` — SimultaneousRoundGame, the
  reusable referee base for simultaneous-move repeated games (hidden choice
  buffering, resolve-at-the-round-boundary, payoffs, checkpoint round-trip)
- `src/silisocs/environments/backends/twitter_like/` — TwitterLikeApp with SQL backend
- `src/silisocs/environments/backends/reddit_like/` — RedditLikeApp
- `src/silisocs/environments/backends/mastodon/` — Real Mastodon server integration
- `src/silisocs/environments/backends/public_goods/` — PublicGoodsApp, the
  reference game-theoretic backend (subclasses SimultaneousRoundGame)
- `src/silisocs/environments/backends/messaging/` — MessagingApp, the default
  agent-to-agent direct-message/broadcast channel (`env=messaging`)
- Actions discovered via `@app_action(selectable_name=..., description=...)`
- To add custom backend: subclass `SocialBackendApp`, implement action methods, register in app factory

### 6. Runtime Orchestration
- `src/silisocs/runtime/execution/session.py` — CLI entrypoint, Hydra config
  composition, runtime assembly, model creation, initialization, simulation
  execution, checkpoint save/resume, and artifact writing
- `src/silisocs/runtime/configuration/` — Config projection, external config
  loading, and validation
- **Failure policy**: prefer failing loudly. A condition the run cannot
  legitimately continue past raises (config errors raise at build/first use, not
  behind an invented default); what a run genuinely survives — one isolated agent
  turn, one failed harness tool call, one routing call that fell back — is COUNTED
  instead, never silently swallowed. Those counters are registered once in
  `evaluations/vocabulary.py::HEALTH_COUNTERS`, which drives the run-end degraded
  warning, `run_manifest.json`'s `health` block, and `RunArtifact.health` together
  (see `docs/usage.md` → "Run Health"). Add a new counter there, and it surfaces
  everywhere; emit it with `SimMetricsCollector.get().increment_counter(...)`.
- `effective_config.yaml` (both copies) is written with every `api_key` masked, so a
  run directory stays shareable; nothing reads credentials back from it.

## 3) Configuration Model

**[docs/configuration.md](docs/configuration.md) is the canonical reference for
every config key, its default, and its semantics — do not restate its tables
here.** A generated key-by-key dump of the packaged defaults (drift-tested in CI)
lives in [docs/config_reference.md](docs/config_reference.md). This section
records only what a contributor needs that the reference does not cover.

Top-level composition (`src/silisocs/conf/experiment.yaml`) defaults to
`world: default`, `agents: default`, `sim: base`, `env: twitter_like`,
`eval: base`. Each group's base file and `@package` directive:

| Group | Base file | `@package` |
|-------|-----------|------------|
| `world` | `world/default.yaml` | `_global_` (keys land at config root) |
| `agents` | `agents/default.yaml` | `agents` |
| `sim` | `sim/base.yaml` | `sim` |
| `env` | `env/twitter_like.yaml` | `env` |
| `eval` | `eval/base.yaml` | `eval` |

**The slot idiom.** Every pluggable piece — engines, loop/step/turn/participation
policies, GM components, backends, memory, checkpoint save/restore, routers,
intervention handlers — is configured as
`{built_in: <shipped> | class_path: <yours>, params: {...}}`. `class_path` wins
over `built_in`; `params` are strict constructor arguments that fail at build on
an unknown key; `params: null` clears an inherited block. The rules are written
once in [docs/configuration.md → Slots](docs/configuration.md#slots), and adding
a slot means documenting it in that table. Anything you add that a user selects
by name should be a slot, not a new config shape.

Note: scenario `world/default.yaml` files REPLACE the base world group (Hydra
searchpath shadowing), so every scenario must re-declare the universal run
params (`num_steps`, `seed`, `output_dir`, ...). Flat scenario
`sim.yaml`/`env.yaml` files are MERGED into the composed config instead.
`tests/test_bundled_scenarios_compose.py` enforces this for the repo's example
scenarios. (Example scenarios live in `scenarios/` and are repository content,
not packaged into the wheel; a pip install ships only the engine + base config.)

GM component routing is enabled with
`env.gm.class_path=silisocs.environments.gm.game_master.MultiFlowGameMaster`.

Scenario content lives under:

- `scenarios/<name>/conf/world/default.yaml` (`@package _global_`) — run params + setting/event/data
- `scenarios/<name>/conf/agents/default.yaml` (`@package agents`) — persona pipeline
- **Optional**: `scenarios/<name>/conf/sim.yaml` — partial sim overrides (merged, not replaced)
- **Optional**: `scenarios/<name>/conf/env.yaml` — partial env overrides
- **Optional**: `scenarios/<name>/conf/agents/thin.yaml` — alternate agents variant (select with `agents=thin`)

**For designing experiments via config (no code changes):** See [agent_docs/scenario_design.md](agent_docs/scenario_design.md)

**For understanding config structure deeply:** See [docs/configuration.md](docs/configuration.md)

**For extending Studio analysis panels and views:** See
[docs/analysis_panels.md](docs/analysis_panels.md)

## 4) Defining New Agent Behaviors Cleanly

Use class-level behavior **flows** plus component/policy slots instead of adding
custom branches to the game master or engine. The knobs, in the order you
usually reach for them:

| # | Knob | Purpose |
|---|------|---------|
| 1 | `agents.persona_pipeline.classes.<class>.flow_tag` | Assign a class to a flow |
| 2 | `sim.engine.step.params.flow_order` | Flows that run as a strict serial prefix |
| 3 | `sim.engine.step.params.agent_to_flow` | Per-entity override of the class mapping |
| 4 | `env.gm.components.observe.params.episode_observation_flows` | Observe specialization per flow |
| 5 | `sim.engine.step.params.flow_turn_policies` | Actions per step, per flow (`flow`/`multi_gm` only) |
| 5b | `sim.engine.step.params.gm_turn_policies` | Actions per step, per GM (any step mode) |
| 5c | `sim.engine.step.params.gm_concurrency_caps` | How many of a GM's turns run at once |
| 6 | `env.gm.components.resolve.params.flow_action_filters` | Which catalog actions a flow may execute |
| 7 | `eval.probes.deployment.include_flows` / `exclude_flows` | Probe only certain flows |
| 8 | `env.gm_orchestration.gms` + `flow_bindings.flow_to_gms` | Multi-GM chains, empty slots, branch routers |

Semantics for all of the above — precedence rules, which step modes each takes
effect under, validation errors, and the `multi_gm` / `multi_gm_serial` /
`multi_gm_staged` traversal differences — live in
[docs/configuration.md](docs/configuration.md) (§Engine Turn Policies,
§Advanced: Multi-GM Orchestration) and
[docs/multi_gm_architecture.md](docs/multi_gm_architecture.md). Do not duplicate
them here.

What a contributor needs to know beyond the config reference:

- **Every one of these keys is additive.** Each falls back to the global/default
  behavior when omitted; a change that makes one of them load-bearing for
  existing configs is a breaking change and needs a migration error.
- **The agent→flow mapping is materialized once.** Scheduling, component routing,
  probe targeting, and checkpoint reconciliation all read the same
  `agent_flow_tags`; derive from it rather than re-deriving flows.
- **New traversal = new step strategy.** Adding a scheduling shape means writing
  a `StepStrategy` and registering it, not adding a branch to an engine class.
- **A branch router is a plain callable**, not a subclass:
  `route(agents, gms, ctx) -> {agent name: chosen gm name}` (structural `Router`
  Protocol in `simulation_engines/policies/routers.py`, positional-only
  parameters, so your function may name them freely). The engine owns only *when*
  it runs, locking (the router call runs UNLOCKED; the follow-up per-chosen-GM
  turn selection runs under that GM's lock), and validating the returned
  assignment. Everything else — reading `gms[...].backend`, calling
  `agent.act(...)`, deciding from `ctx.seed` — the router does itself.
- **Router fallbacks are counted, never silent.** `agent_choice`'s `on_invalid`
  path (including a routing call that RAISES) increments the `routing_fallbacks`
  run-health counter, so it surfaces in the degraded-run warning,
  `sim_metrics.json`, and the manifest. Any new "we recovered" path must do the
  same (see §2 failure policy).
- **Replay caveat to preserve:** under concurrent `multi_gm`, branch hops into a
  shared GM with a STATEFUL `next_acting` (e.g. `fixed_order`) are not
  replay-stable — driver timing orders them. `multi_gm_serial` /
  `multi_gm_staged` are the exact-replay answers.

Fixed agents (`silisocs.agents.fixed.FixedAgent`) are the reference example.

Default UX rule:
- Keep users on the default `ComponentGameMaster`, with advanced Studio controls off.
- Only expose flow tags and multi-GM controls behind advanced mode.

## 4.5) Mid-Run Interventions

An optional top-level `interventions` schedule fires actions at step boundaries,
turning a controlled experiment (swap the recommender at the midpoint, ban an
agent, inject breaking news) into config instead of a checkpoint / edit / resume
cycle. The kinds, their fields, and validation rules are documented in
[docs/configuration.md](docs/configuration.md) → "Mid-Run Interventions".

The invariants a contributor must preserve:

- **Dispatch runs at the single-threaded loop boundary** (`simulation_engines/
  interventions.py`, called from `FixedStepsLoopStrategy`), so handlers mutate
  engine/GM/backend state without locks. Do not move dispatch into a concurrent
  phase.
- **Fired-ness is a pure function of `(schedule, step)`** — no checkpoint-schema
  change, and persistent state-setters replay on resume for
  `at_step < start_step`. One-shot event kinds (`inject_*`,
  `broadcast_observation`) are never replayed.
- **Bans are a participation wrapper, not a roster mutation.**
  `BanFilterParticipation` wraps the active policy (soft ban), so a banned agent
  still exists in the world.
- **Generic languages, not per-feature kinds.** `inject_action` is the injection
  language (any catalog action as a typed tool call through the GM's resolve
  component) and `inject_post` is sugar over it; `set_component_params` is the
  retuning language (a component opts a parameter in via its class-level
  `runtime_tunable` frozenset, and `BaseComponent.set_params` routes each name
  through `set_<name>()` or the same-named attribute) and `set_recsys` is sugar
  over it. Prefer sugar over a new kind.
- **`swap_component` only accepts stateless components** — refused when the
  outgoing OR incoming component has non-empty `get_state()`. `resolve` /
  `action_prompt` / `initialize` are out of scope.
- **Adding a kind:** subclass `InterventionHandler` (declare `kind`,
  `persistent`, `validate`, `apply`) and reference it via `kind: custom`.
- **Stamp the episode before logging.** Interventions fire BEFORE `run_step`
  stamps the episode index, so a custom handler that logs backend events must use
  the `InterventionContext` helpers first: `ctx.stamp_episode(gm)` (stamps the
  GM's action/exposure/harness loggers with `ctx.step`) or
  `ctx.resolve_action(gm, agent_name, output)` (stamps, then resolves an injected
  action through the GM's resolve pipeline — the seam the built-in
  `inject_action` / `inject_post` handlers use).

## 5) Agent Interface: Concordia vs Custom

All agents in silisocs implement a common interface defined by `silisocs.agents.base_agent.Agent` (ABC).

### Minimum Required Interface

Every agent (whether Concordia-based or custom) must implement:

```python
from silisocs.agents.base_agent import Agent

class MyAgent(Agent):
    @property
    def name(self) -> str:
        """Return agent's display name."""
        return "Alice"
    
    def observe(self, observation: str) -> None:
        """Receive environment observation from the social app."""
        self._last_observation = observation
    
    def act(self, action_spec) -> str:
        """Generate an action response given the action specification."""
        # action_spec provides context and constraints
        # Return format is determined by the resolve component (via YAML config)
        return "some action response"
```

The resolve component and agent's configuration determine the output format expected.
Agents should not be concerned with prescribing action format—that is a platform concern.

### Optional Async Path (`sim.engine.executor: asyncio`)

Sync `act()` is and remains the required contract; the async path is purely
additive. Every agent MAY override `act_async(action_spec)` (base default: the
sync `act` on a helper thread via `asyncio.to_thread`) and route through
`_call_model_async(context, action_spec)`; every `LanguageModel` has
`sample_{text,choice,tool_calls,structured,float}_async` twins (default: the sync
method on a helper thread; OpenAI-compatible providers implement
text/choice/tool-calls natively). Custom `TurnPolicy` classes may likewise
provide an optional `run_async` (awaiting `engine.run_agent_step_async`).

Invariants to preserve when touching this path:

- Loop-native and sync-only agents/models/policies **mix freely in one step** — a
  sync-only implementation must never be able to stall the event loop, which is
  why it runs on a bounded helper thread.
- Everything that calls `agent.act(...)` synchronously (probes, seed-post
  initialization, the `agent_choice` router) stays synchronous.
- Scheduling semantics (flow chains, barriers, per-GM caps/locks, failure
  isolation, retry telemetry, `max_concurrent_actions`) are **identical across
  executors**. A change that makes them diverge is a bug.

Worked example: [docs/building_agents.md](docs/building_agents.md) →
"Optional async fast path".

### Reference Implementation: FixedAgent

`silisocs.agents.fixed.FixedAgent` is the concrete non-LLM example: it extracts
the episode index in `observe()` and returns the episode's pre-defined action
from `act()`. Read it before writing a new agent runtime.

### How Custom Agents Are Loaded

1. Write an `Agent` subclass whose `__init__` takes `model` plus keyword-only
   params (see the walkthrough in
   [docs/building_agents.md](docs/building_agents.md) → "Custom Agent Runtime
   Shape").
2. Reference it from the persona pipeline:
   ```yaml
   persona_pipeline:
     classes:
       influencer:
         count: 1
         class_path: my_agents.MyCustomAgent
         params:
           name: Alice
           context: Initial persona text.
   ```
3. The runner imports the class path directly and instantiates it with `model`
   plus the configured `params` (a standard slot — unknown `params` fail at
   build).

### Concordia Integration Points

If building a **Concordia-compatible** agent (using EntityAgentWithLogging):

- Agents are context components (observe/act participate in component orchestration)
- Extend `EntityAgentWithLogging` to get logging + checkpoint support automatically
- Opt in with `compat: concordia`; the adapter calls the upstream Concordia
  prefab's `build()` method

If building a **custom (non-Concordia)** agent:

- Implement only the `Agent` interface
- Concordia integration still works (engine calls `agent.observe()` and `agent.act()`)
- Checkpoint support optional (implement `get_state()`/`set_state()` if needed)

No special ABC requirement for Concordia agents—they naturally implement the interface via activity slots.

### Tool-Calling Implementation for Entities

When **tool-calling is enabled** (`tool_calling.mode: single|multi`), the platform uses backend
actions as tools and the language model selects which action(s) to invoke.

**Architecture for tool-calling:**

1. **Detect tool-calling mode**: The GM's action-prompt component checks the
   `enable_tool_calling` flag and, when the backend provides
   `generate_tool_schemas()`, builds an `ActionSpec` with
   `output_type=OutputType.TOOL_CALLS` and the tool schemas in `extra_args["tools"]`
   (see `environments/gm/components/action_prompt.py`)
2. **Agent act layer**: `Agent._call_model()` routes `OutputType.TOOL_CALLS`
   specs to the model's `sample_tool_calls()` with the provided schemas
3. **Typed result**: The agent returns an `ActionOutput` carrying typed
   `ToolCall` entries (no string marker or JSON parsing involved)
4. **Resolve execution**: `ToolCallingResolveComponent` validates the tool calls
   and executes them via `backend.invoke_action_with_kwargs()`

### Enabling Tool-Calling

Prompt generation mode (`sim.action_mode`: `custom` | `generic`) and parsing mode
(`sim.tool_calling.mode`: `none` | `single` | `multi`) are independent. Enabling
tool-calling means pairing the mode with the `tool_calling` resolve component:

```yaml
sim:
  action_mode: custom          # custom prompt text still used
  tool_calling:
    mode: single
env:
  gm:
    components:
      resolve:
        built_in: tool_calling
```

Design contract to preserve: the **action format is a platform concern, never an
agent concern**. Agents return strings (or typed `ActionOutput`); the resolve
component and world config decide how that is interpreted. Tool-calling dispatch
lives in the entity/act layer — `Agent._call_model()` routes a
`TOOL_CALLS` spec to `sample_tool_calls` — not in resolve, which only validates
and executes what came back. Per-GM `action_mode` / `tool_calling` overrides and
their per-GM compatibility validation are in
[docs/configuration.md](docs/configuration.md).

### Adding a Custom LLM Provider

Three routes, **none of which require a core edit**: a named preset
(`OPENAI_COMPATIBLE_PRESETS` in `runtime/language_models/factory.py`), the
`@register_llm_provider("name")` registry decorator on a `LanguageModel`
subclass (import the module before the run starts — see §3 `plugins`), or a
fully-qualified `sim.llm.provider: mypkg.models.MyModel`. Providers speaking an
OpenAI-compatible HTTP API should subclass `OpenAICompatibleLanguageModel` to
inherit retry/backoff and telemetry. Adding a preset must not change behavior for
an existing `provider` value. Full walkthrough:
[docs/building_agents.md](docs/building_agents.md) → "Registering a Custom LLM
Provider".

### Validation & Error Handling

Agent construction (`src/silisocs/runtime/construction/assembly.py`) validates
every built agent: a `class_path` that does not produce a native
`silisocs.agents.base_agent.Agent` raises a `TypeError` at build time (naming
the class and pointing at `compat: concordia` for Concordia-shaped agents), so
**missing methods fail fast** — before any LLM call.

### Multi-Action Support (Open-Ended Policy)

When using `sim.engine.turn_policy.built_in: open_ended`:

- Agent's `act()` method is called repeatedly within one step
- Agent should output valid actions OR the special "Finished action episode" signal
- Resolve components recognize "FINISHED" and stop iteration
- Allows agents to decide how many actions to take per step

Example:
```python
def act(self, action_spec) -> str:
    if self._done_for_this_step():
        return "Finished action episode"
    return self._next_action()
```

This mode works with any agent (Concordia or custom) that implements the basic interface.

### Harness Agents (real agent harnesses as silisocs agents) — EXPERIMENTAL

> **Experimental.** The harness integration is new and evolving; the deterministic core
> (Tool Bridge, agent/probe/checkpoint contract, Model Proxy) is tested with the
> dependency-free `FakeHarnessAgent`, but live Hermes/OpenClaw runs are opt-in and less
> exercised. Expect the config surface and internals to change.

A **harness agent** (`silisocs.agents.harness`) embeds a real agent harness — Hermes
(in-process) or OpenClaw (out-of-process) — as an `Agent`. The harness runs its own
model→tool loop inside ONE `act()` call (bounded by its `max_iterations`), so for a
harness class the effective turn policy is `single_action` around one complete run. The
design is deliberately thin at the edges:

- **Harness agents are just `Agent` subclasses** loaded via `class_path`. No engine,
  scheduler, participation, intervention, or checkpoint changes — those layers are
  duck-typed. `HarnessAgent` owns observe-buffering, ActionSpec dispatch, probes,
  checkpoint state, and telemetry.
- **One thin module — the Tool Bridge (`ToolSurface`).** Built per turn from the acting
  GM's backend. It adds no filtering of its own — the backend already owns that:
  `schemas()` returns `backend.generate_tool_schemas()` (already restricted to the
  backend's enabled/excluded actions) and `execute()` forwards to
  `backend.invoke_action_with_kwargs()` (which validates the call and logs the
  `action_events.jsonl` row, just like a native turn). The surface only adds the harness
  concerns: injecting the actor argument (via `backend.action_accepts_param`, shared with
  the resolve components), turning exceptions into results the loop reacts to, and
  recording `harness_events.jsonl`. It is the single seam every adapter consumes, so
  harness agents are backend-agnostic by construction.
- **One adapter seam — `HarnessAdapter` (Protocol).** Concrete harnesses implement only
  `run_turn` (optionally `run_turn_async`/`run_probe`/`snapshot`/`restore`/
  `bind_model_proxy`). `FakeHarnessAdapter`/`FakeHarnessAgent` are the deterministic,
  dependency-free reference (and the contract-test subject).
- **Zero GM config (self-describing).** No `harness` GM built-ins exist. The default
  action-prompt component binds the Tool Bridge into `ActionSpec.extra_args['tool_surface']`
  for any acting agent that declares `wants_tool_surface` (harness agents do; native
  agents don't, so non-harness runs are unchanged). A harness turn's `ActionOutput`
  carries a `harness_turn` structured payload, so the shared `_BaseResolveComponent.resolve_action`
  records it uniformly — ANY resolve (`parsed_action`/`tool_calling`/…) handles harness
  output, and one GM hosts mixed native+harness populations with no special config or
  validation.
- **One model plane — the Model Proxy (`agents/harness/proxy.py`).** A loopback
  OpenAI-compatible server the runtime starts when any harness class is configured
  (`setup_harness_proxy` in `agents/harness/runtime.py`, stopped in the session
  `finally`). Harness model calls forward through it byte-for-byte; provider `usage`
  folds into the SAME `llm_usage` as native agents via a duck-typed `UsageAccumulator`
  added to the run's `models`. Real keys live only in the proxy; per-agent routing
  tokens are the harness-side "api key".
- **Determinism:** harness agents are snapshot-restored, never replay-restored. Per-call
  detail is written to `harness_events.jsonl` (per-GM in multi-GM runs), indexed in the
  run manifest and exposed on `RunArtifact.iter_harness_events()`.

To add a new harness: implement a `HarnessAdapter` and a thin `HarnessAgent` subclass.
See `docs/harness_agents.md`. Non-goals of the current pass: no event-driven engine, no
MoltBook facade, no live-internet tools (surfaces expose only backend catalogs).

## 6) Checkpoints and Replay

- Checkpoints are saved as JSON under run output `checkpoints/step_{N}_checkpoint.json`.
- Runtime resume uses:
- `sim.checkpoint.source_run` (explicit prior output directory)
- `sim.checkpoint.auto_resume` (default `true`): when `source_run` is unset, resume
  from this run's own output directory if it already contains checkpoints; a fresh
  output directory still starts from scratch. Set `false` to force a fresh start.
- `sim.checkpoint.restore`
- Resume restores game-master and entity component state plus raw log.

**Custom backend contract** (the enforced parts; full checklist in
`docs/backends.md`): implement `name()`/`description()` (abstract); expose actions
with `@app_action` and name the actor param `agent_name` for injection; return
`ActionResult(committed=False)` for rejected or idempotent calls (committed
calls are logged automatically); accept the factory's ctor kwargs or not
— `action_logger` is wired post-construction either way, so it can no longer be
silently swallowed. Validations that now fail at build rather than mid-run: an
action filter leaving no callable action (`enabled_actions: []` is an empty
ALLOW-LIST, not "no filter"; FINISHED doesn't count); `provides_checkpoint_state=True`
with an inherited no-op `get_state`/`set_state`; and a social-only component
(`social_media`/`timeline_every_turn`/`social_recommendation`, or any component
declaring `requires_social_backend = True`) on a non-`SocialBackendApp` backend.
An omitted `observe` slot picks `timeline_every_turn` for a social backend and
`app_observation` otherwise. Backends need NO database (`db_path` belongs to the
SQLite backends; `resource_market`/`virtual_space` are in-memory references).
Open `tags` and semantic `fields` declared on `@app_action` are derived for
custom backend types and persisted in the run manifest. A class-level
`event_semantics` declaration remains available for aggregate definitions (an
`EventSemantics` or the portable `{roles, fields, labels}` mapping — the shipped
social backends declare theirs this way); resolution MERGES the declaration with
decorator-derived tags (declaration wins per entry), so decorating a new action
always reaches analysis, and a malformed class declaration raises. Event
semantics live in `environments/backends/event_semantics.py` (the declaring
layer); `evaluations/vocabulary.py` re-exports them for analysis-side readers
over the same registry. Layering is machine-enforced: import-linter contracts in
`pyproject.toml` (pre-commit hook `lint-imports`) keep the runtime kernel
(`runtime/{types,io,telemetry,class_loading,language_models}`) free of
upper-layer imports and the package layers one-directional.

**Committed-only action log** (backend authors): `action_events.jsonl` is the
canonical log of actions that committed a state change or performed a deliberate
logged read. The invocation layer logs plain returns and
`ActionResult(committed=True)` automatically; return
`ActionResult(committed=False)` for rejected or idempotent calls. `log=False`
disables logging for a deliberate read, `log_as` selects a stable label, and
`data` supplies derived logged fields. `_log_action_event` remains for
non-action commit points and direct dispatchers; calling it during an invoked
action suppresses the automatic row. `invoke_action_detailed(name, kwargs) ->
(committed, result)` reports the outcome to committed-counting policies.
Every committed row also appends to an in-memory **committed-events mirror**
on `BackendApp` — the supported runtime read path so scenario code (routers,
intervention conditions, state-dependent policies) can ask "how many X committed
so far" without scraping the log file: `count_committed_events(labels=..., agent=...,
since_episode=..., before_episode=..., text_contains_any=...)` and the
`iter_committed_events(...)` generator behind it (per-backend / per-GM; snapshot
backends round-trip it via the `_committed_events_state()`/`_restore_committed_events()`
helpers, replay-restored ones rebuild it for free).

**Backend restore contract** (backend authors): a backend supports either (or both)
of two restore paths, and **a backend implements only `get_state`/`set_state`** —
there is no backend-level replay method.

1. *Authoritative snapshot.* Set the class flag `provides_checkpoint_state = True`
   and make `get_state`/`set_state` round-trip the full state; restore applies it
   directly. Every shipped backend does this.
2. *Action-event replay.* A **mechanism owned by the pluggable
   `sim.checkpoint.restore` strategy**, not by the backend. The built-in
   `social_action_event_replay` strategy keeps per-backend event→action mappings in
   a registry keyed by `backend_type` (`runtime/checkpointing/replay_mappers.py`);
   a backend "supports replay" exactly when a mapper is registered for its
   `backend_type`. Opt in with `register_replay_mapper(backend_type, mapper)`
   (mirrors `register_llm_provider`) — no core edit. A bespoke restore that a
   stateless mapper cannot express is a custom
   `sim.checkpoint.restore.class_path` strategy instead.

The authoritative-vs-replay decision is **per game master**: snapshot GMs restore
from their own block, the rest go to the strategy (which requires a registered
mapper). Multi-GM runs isolate each GM's backend db + `action_events.jsonl` under
`<output>/<gm_name>/`; both restore and eval discover these via
`silisocs.evaluations.action_events.resolve_action_event_files` — use that helper
rather than rediscovering the layout. Which built-ins are registered, the Mastodon
embed-and-replay exception, per-GM `restore` overrides, and restore-robustness
guarantees are documented in
[docs/configuration.md](docs/configuration.md) → "Checkpoint Restore".

**Save layout** (`sim.checkpoint.save`, mirroring the restore slot): the payload
always comes from `make_checkpoint_data` and `load_checkpoint_file` reassembles a
sharded checkpoint transparently, so **restore strategies and evaluators must
never depend on the on-disk layout**. A custom layout is a
`sim.checkpoint.save.class_path` subclassing `CheckpointSaveStrategy`
(`runtime/checkpointing/save.py`). The shipped layouts and their params are in
[docs/configuration.md](docs/configuration.md).

**Saving policy**: checkpoint saving is off by default under the `silisocs` CLI
unless `every_n_steps` or `explicit_steps` is set; `silisocs-study` injects
`every_n_steps=1` so evaluators can read the final checkpoint (tunable per study —
see [docs/study_schema.md](docs/study_schema.md)).

**For custom agents**: Checkpointing is duck-typed — every agent and game master is
checkpointed by reading its `get_state()` and applying its `set_state()` (there is no
`isinstance(EntityWithComponents)` gate). The base `Agent` provides no-op defaults, so an
agent with no episodic state needs no changes. If your custom agent *does* have episodic
state, implement BOTH `get_state()` and `set_state()`: restore now refuses to load a
checkpoint whose object saved non-empty state but only inherits the no-op `set_state()`
(it would otherwise be silently dropped). Restore also rejects a checkpoint object whose
`class_path`/`compat` no longer matches the current runtime object of the same name.

Example:
```python
class MyAgent(Agent):
    def get_state(self) -> dict[str, Any]:
        return {"episode": self._current_episode, ...}
    
    def set_state(self, state: dict[str, Any]) -> None:
        if state:
            self._current_episode = state.get("episode", 0)
```

## 7) Key Development Commands

Use uv-managed workflows (from `docs/contributing.md`):

- Sync dev env: `uv sync --group dev`
- Lint workflow: `uv run --group dev poe lint`
- Test workflow: `uv run --group dev poe test`
- Docs workflow: `uv run --group dev poe docs`
- Pre-commit hooks all files: `uv run pre-commit run --all-files --verbose`
- Commit with Commitizen: `uv run cz c`

Fast contributor workflow (coding-agent friendly):

1. `uv sync --group dev`
2. Run targeted tests for changed files first (`uv run pytest <targeted_tests>`)
3. Run full quality gate: `uv run pre-commit run --all-files --verbose`
4. Run coverage workflow: `uv run --group dev poe test`
5. Commit with Commitizen (`uv run cz c`) or a valid `cz_gitmoji` message
6. Push branch (`git push origin <branch>`)

**NEVER commit `.env` files or any file containing API keys, passwords, or secrets.**
These files (`.env`, `store.env*`, etc.) are gitignored for this reason. Staging them
accidentally (e.g. via `git add -A`) and pushing will expose credentials publicly and
trigger GitHub push protection. If you suspect a secret was staged, run
`git reset HEAD <file>` before committing.

## 8) Testing Expectations for Agents

When changing runtime behavior:

1. Run targeted tests for touched modules first.
2. Run full suite before finalizing if feasible.
3. Add tests for new config/behavior paths.
4. Avoid deleting tests unless they are obsolete due to architecture removal.

Useful tests in this repo include action parsing, worker limits, probe deployment,
backend action catalogs, and checkpoint policy tests.

## 9) Documentation Map

This guide (AGENTS.md) is for any coding agent or human contributor who is
**extending the framework** — writing new components, backends, agents, or
changing architecture. `CLAUDE.md` intentionally points here so Claude Code,
Codex, Cursor, and other repo-aware agents share one canonical map.

If instead you want to **design and run experiments via config only**:
→ See [agent_docs/scenario_design.md](agent_docs/scenario_design.md) — Scenario design guide for config-based users

**Agent-doc index**:
→ See [agent_docs/README.md](agent_docs/README.md) — discoverable map of
agent-facing guides and guided workflows

**Detailed architecture deep dive** (multi-flow, multi-GM, component routing):
→ See [agent_docs/architecture.md](agent_docs/architecture.md) — Reference for complex orchestration patterns

**Guided workflows** (interactive design workflows — readable by any coding agent):
→ [agent_docs/skills/new-scenario.md](agent_docs/skills/new-scenario.md) — Step-by-step scenario design assistant
→ [agent_docs/skills/new-study.md](agent_docs/skills/new-study.md) — Step-by-step study design assistant

**Public documentation** (for end users) — one canonical home per topic:

| Topic | Canonical page |
|---|---|
| Documentation hub | `docs/index.md` |
| Every config key, default, and semantics | `docs/configuration.md` |
| The `built_in`/`class_path`/`params` slot idiom | `docs/configuration.md` → "Slots" |
| Generated dump of packaged defaults (CI drift-tested) | `docs/config_reference.md` |
| End-to-end workflow, output files, run health | `docs/usage.md` |
| Designing a study (concepts + workflow) | `docs/study_guide.md` |
| `silisocs-study` commands, filters, presets, HPC | `docs/experiments.md` |
| `study.yaml` + generated file formats, notebooks | `docs/study_schema.md` |
| Engine/GM/component extensibility patterns | `docs/environment_layer.md` |
| Code-level extension API reference | `docs/simulation_extensibility_api.md` |
| Backend plugin patterns | `docs/backends.md` |
| Agent builder patterns | `docs/building_agents.md` |
| Studio usage / analysis panels | `docs/studio.md`, `docs/analysis_panels.md` |
| Code standards | `docs/contributing.md` |

When adding a feature, update the canonical page for it — do not restate its
tables or defaults in AGENTS.md. This file carries the architecture map,
contracts, invariants, and workflows that the user docs do not cover.

- New/changed config key → `docs/configuration.md` (+ the Slots table if it is a
  slot), and `docs/config_reference.md` regenerates
- New runtime behavior or extension seam → `docs/environment_layer.md` /
  `docs/simulation_extensibility_api.md`, plus a contract note here
- User-visible workflow change → `docs/usage.md`
- Studio behavior → `docs/studio.md`

## 10) Common Pitfalls

- Adding GM/engine bloat instead of using flow routing + component hooks
- Inventing a new config shape where a slot (§3) would do
- Breaking the action text format consumed by resolve
- Restating `docs/configuration.md` values here instead of linking (they drift)
- Forgetting to keep docs aligned with runtime defaults
- Assuming Studio run-artifact loading equals checkpoint state replay
- Relying on non-uv environment when reproducing tests
- Not understanding config composition (Hydra merges scenario-local overrides with base defaults)

## 11) PR Readiness Checklist

- Code compiles and tests pass in uv environment
- Lint/pre-commit workflow passes
- New behavior has tests
- Docs updated for config + usage + architecture
- Commit message uses the configured `cz_gitmoji` schema (see §14)

## 11.5) Branching Rules

- **Never commit code changes directly to `main`.** All code changes must go through a `dev` branch (or feature branch) and be merged via PR.
- **Documentation-only changes** (edits to `docs/`, `agent_docs/`, `AGENTS.md`, `README.md`) may be committed directly to `main`.
- When starting new work, create a branch: `git checkout -b dev` (or a descriptive feature branch name).

## 12) Entry Points for Quick Exploration

Start from these files to understand the flow:

1. **Config composition**: `src/silisocs/runtime/execution/session.py` (`cli_main`/`main`) — How Hydra merges configs
2. **Simulation orchestration**: `src/silisocs/runtime/execution/session.py` — Full workflow
3. **Engine execution**: `src/silisocs/simulation_engines/base_engines.py` — Episode loop
4. **Game master**: `src/silisocs/environments/gm/game_master.py` — Simple preset
5. **Multi-flow GM**: `src/silisocs/environments/gm/game_master.py` — Advanced component routing
6. **Component slots**: `src/silisocs/environments/gm/components/` — Pluggable behavior
7. **Backend actions**: `src/silisocs/environments/backends/twitter_like/app.py` — Example backend
8. **Run loading**: `src/silisocs/evaluations/run_artifact.py` — `load_run`/`load_study`
   typed artifact loaders (manifest-first with legacy fallback); Studio and
   analysis tools load runs through this, never by rediscovering the file layout

## 13) Session State

Use a `SESSION_STATE.md` file (gitignored) to maintain context across a work session.

- **At session start**: check if `SESSION_STATE.md` exists and read it to restore context.
- **After significant subtasks** (commits, refactors, feature completion): offer to update it.
- **Clear** when starting unrelated work or after a clean commit.

Template:
```markdown
# Session State

## Current Focus
Brief description of current task

## Modified Files
- path/to/file.py - what changed

## Decisions Made
- Chose approach X because Y

## Next Steps
- [ ] Pending task 1
- [ ] Pending task 2

## Open Questions
- Question for user about X?
```

## 14) Environment Notes

- Use `uv run` prefix for all commands.
- Run pre-commit before committing (see section 7 for workflow).
- Commit messages must use the configured `cz_gitmoji` schema:
  - `♻️ refactor(...):` — renames, restructuring
  - `🐛 fix(...):` — bug fixes
  - `✨ feat(...):` — new features
  - `📝 docs(...):` — documentation
  - `🧹 chore(...):` — maintenance
- **WSL users**: if imports are slow (1+ min), the venv is likely on `/mnt/c`. Use a WSL-native venv:
  ```bash
  export UV_PROJECT_ENVIRONMENT=~/venvs/simulator
  uv sync
  ```
