# Multi-Flow & Multi-GM Architecture

Advanced composition model for routing agent flows through multiple native game
masters.

**For**: Developers extending the framework with complex orchestration needs.
**See also**: [Environment Layer](environment_layer.md), [Configuration Reference](configuration.md#gm-components)

---

## Overview

The multi-GM architecture provides:

1. **Flow-to-GM routing**: each configured agent flow maps to one or more GMs.
2. **Per-GM backends**: each GM owns exactly one backend for this release.
3. **Per-GM components**: each GM defines its own typed component slots.
4. **Concurrent flow chains**: a flow can pass through multiple GMs as a
   pipeline; under the default `multi_gm` step strategy distinct flows advance
   concurrently through their GM chains (see [Chain Execution
   Mode](#chain-execution-mode)), serializing only when two flows touch the
   same GM at the same time.

### When to Use

- Simulating different backend worlds in the same run.
- Routing agent cohorts through different decision environments.
- Multi-stage pipelines such as public action → audit → moderation.

### When NOT to Use

- Simple single-backend world: use one GM.
- Agents only need sequencing: use a single GM with `FlowStepStrategy`.

---

## Architecture Model

### Layer Stack

```
┌─────────────────────────────────────────────────────┐
│ Flow → GM chains (env.gm_orchestration.flow_bindings)│
│ Agents are grouped by flow, then routed through GMs   │
├─────────────────────────────────────────────────────┤
│ Agent Flow Sequencing (flow_order, agent_to_flow)   │ ← Agent grouping within GM
│ Groups agents by flow, executes sequentially        │
├─────────────────────────────────────────────────────┤
│ GM component slots                                  │ ← initialize/next/observe/resolve/update
│ Components are configured per GM                    │
└─────────────────────────────────────────────────────┘
```

### Execution Model

**Single-GM Mode (Default):**
```
Engine Step Loop:
  → next_acting selects agents
  → group by flow
  → execute flows sequentially (within each, agents parallel)
  → next step
```

**Multi-GM Mode (Advanced):**

Chain traversal is governed by `sim.engine.step.built_in`, which offers three
multi-GM step strategies: `multi_gm` (concurrent, the default),
`multi_gm_serial` (legacy row-major), and `multi_gm_staged` (column-major with a
global per-stage barrier).

*`multi_gm` (concurrent, default):*
```
Engine Step Loop:
  → for each configured GM in sequence order:
    → gm.update(...)
  → group agents by flow
  → run flows listed in flow_order first, as a strict serial prefix
    (preserves declared precedence, e.g. fixed_pre before default)
  → run every other flow as a concurrent group:
    → each flow is an independent pipeline through its GM chain;
      a flow's next hop starts as soon as its own previous hop completes;
      turns serialize only when two flows touch the same GM at the same time
      (enforced by the engine's existing per-GM lock)
    → within a flow, an agent's own chain hops stay serial:
      each hop observes the prior hop's resolution
  → next step
```

*`multi_gm_serial` (legacy row-major):*
```
Engine Step Loop:
  → for each configured GM in sequence order:
    → gm.update(...)
  → group agents by flow
  → for each flow in flow-by-flow ("row-major") order:
    → run that flow's full GM chain to completion before the next flow:
      → for each GM in that flow's configured GM chain:
        → gm.acting_agents(flow_agents)
        → each active agent observes/acts/resolves through that GM
  → next step
```

*`multi_gm_staged` (column-major with a global per-stage barrier):*
```
Engine Step Loop:
  → for each configured GM in sequence order:
    → gm.update(...)
  → group agents by flow
  → run flows listed in flow_order first, as a strict serial prefix
    (same as multi_gm)
  → advance every remaining flow ONE STAGE AT A TIME:
    → for stage N = 0, 1, 2, ...:
      → all flows' stage-N hops run concurrently (an empty/null slot means
        the flow idles this stage and resumes at its next non-null hop)
      → GLOBAL BARRIER: stage N+1 does not begin until ALL of stage N's
        turns finish
  → next step
```
The barrier keeps differently-shaped chains stage-aligned at the cost of leaving
the worker pool idle at stage tails (a fast flow waits for slow flows at the
barrier); use `multi_gm` when you do not need stage alignment.

**Key property**: a GM owns one backend. Multi-backend simulations use multiple
GMs, each with its own backend and components.

Each GM updates exactly once at the start of a step, before flow routing and
actor selection. Flow chains route agent turns through GMs; they do not trigger
additional per-chain GM update calls.

---

## Configuration

### Simplest: Single GM

```yaml
gm:
  name: default_gm
  backend:
    type: twitter_like
    class_path: null
    params: {}
    enabled_actions: null
  components:
    initialize:
      built_in: social_media
      params: {graph: {}}
    next_acting:
      built_in: all_agents
    observe:
      built_in: timeline_every_turn
    resolve:
      built_in: parsed_action
    update:
      built_in: social_recommendation
    action_prompt:
      built_in: default
```

All agents use this GM unless the engine step policy routes by flow.

### Multi-GM: Different GMs for Different Flows

```yaml
gm_orchestration:
  gms:
    - name: social_gm
      sequence: 0
      backend:
        type: twitter_like
        class_path: null
        params: {}
        enabled_actions: null
      components:
        initialize: {built_in: social_media, params: {graph: {}}}
        next_acting: {built_in: all_agents, params: {}}
        observe: {built_in: timeline_every_turn, params: {}}
        resolve: {built_in: tool_calling, params: {}}
        update: {built_in: social_recommendation, params: {}}
        action_prompt: {built_in: default, params: {}}

    - name: market_gm
      sequence: 1
      backend:
        type: resource_market
        class_path: null
        params: {}
        enabled_actions: null
      components:
        initialize: {built_in: app_initialize, params: {}}
        next_acting: {built_in: fixed_order, params: {}}
        observe: {built_in: app_observation, params: {}}
        resolve: {built_in: tool_calling, params: {}}
        update: {built_in: disabled, params: {}}
        action_prompt: {built_in: default, params: {}}

  flow_bindings:
    flow_to_gms:
      social: [social_gm]
      market: [market_gm]
```

**Behavior:**
- agents with flow `social` act through `social_gm`;
- agents with flow `market` act through `market_gm`;
- each GM initializes its own backend and updates once at the start of each step.

`env.gm_orchestration.gms[*]` is intentionally strict in 0.x. Every GM must
declare its own `backend` and `components`; orchestrated GMs do not inherit
missing backend or component slots from `env.gm`. Nested backend and component
keys use the same strict surface as the default GM.

Each GM additionally accepts two **optional** keys, `action_mode` and
`tool_calling_mode` (alias `tool_calling`, scalar or `{mode: ...}`). Unset, they
fall back to the global `sim.action_mode` / `sim.tool_calling.mode`. Resolve
compatibility is validated per GM against each GM's *effective*
`tool_calling_mode`: a `single`/`multi` GM must use the `tool_calling` resolve
component, a `none` GM must not.

```yaml
gm_orchestration:
  gms:
    - name: social_gm
      sequence: 0
      tool_calling_mode: single        # this GM uses tool-calling...
      backend: {type: twitter_like, class_path: null, params: {}, enabled_actions: null}
      components:
        resolve: {built_in: tool_calling, params: {}}   # ...so resolve must match
        # ... other slots ...

    - name: market_gm
      sequence: 1
      # no overrides -> inherits global sim.action_mode / sim.tool_calling.mode
      backend: {type: resource_market, class_path: null, params: {}, enabled_actions: null}
      components:
        resolve: {built_in: parsed_action, params: {}}
        # ... other slots ...
```

Within each GM:
```yaml
engine:
  step:
    built_in: multi_gm
    params:
      flow_order: [pre_analysis, default, post_analysis]
      agent_to_flow:
        alice: "pre_analysis"
        bob: "default"
        charlie: "post_analysis"
```

`agent_to_flow` is validated against the final Agent names during runtime
construction. Overrides are materialized into each Game Master's
`agent_flow_tags` before the Engine starts, so Engine scheduling and
Game Master component routing use the same flow assignment.

### Chain Execution Mode

How flow chains traverse their GM chains is selected by `sim.engine.step.built_in`
via three multi-GM step strategies. (This replaces the removed
`sim.engine.step.params.chain_execution` knob; a config that still sets it raises
a `ValueError` with a migration hint.)

```yaml
engine:
  step:
    built_in: multi_gm   # multi_gm (default) | multi_gm_serial | multi_gm_staged
    params:
      flow_order: [fixed_pre, default]
```

- `multi_gm` (**default**, concurrent): Flows run as independent pipelines through
  their GM chains. Distinct flows advance concurrently, and a flow's next hop
  starts as soon as its own previous hop completes; turns serialize only when two
  flows touch the same GM at the same time (enforced by the engine's existing
  per-GM lock). Flows listed in `flow_order` run first as a strict serial prefix,
  preserving declared precedence such as seed-then-act (`fixed_pre` before
  `default`); every other flow runs as the concurrent group. A single agent's own
  chain hops always stay serial, since each hop observes the prior hop's
  resolution. This is the unchanged default behavior.
- `multi_gm_serial` (legacy row-major): each flow runs its full GM chain to
  completion before the next flow, one batch at a time, in a deterministic
  flow-by-flow order.
- `multi_gm_staged` (column-major with a global per-stage barrier): `flow_order`
  flows run first as a serial prefix (same as `multi_gm`); then every remaining
  flow advances ONE STAGE AT A TIME — all flows' stage-N hops run concurrently,
  and stage N+1 does NOT begin until ALL of stage N's turns finish. The barrier
  keeps differently-shaped chains stage-aligned; its cost is an idle worker pool
  at stage tails (a fast flow waits for slow flows). Use `multi_gm` when you do
  not need stage alignment.

Regardless of mode, each GM's `update()` still runs once before any acting in a
step, checkpoint replay stays per-agent-flow-chain, and `flow_turn_policies` plus
per-flow component routing still apply at every hop.

#### Empty Slots (staged mode)

Under `multi_gm_staged`, a flow's `flow_to_gms` chain may contain a `null` entry
— an **empty slot** — meaning the flow idles at that stage and resumes at its
next non-null hop. Empty slots let flows with different chain shapes stay
stage-aligned under the global barrier:

```yaml
flow_bindings:
  flow_to_gms:
    flow_a: [world_gm, social_gm]   # acts at stage 0 (world_gm), stage 1 (social_gm)
    flow_b: [null, social_gm]       # idles stage 0, acts at stage 1 (social_gm)
```

Here both flows reach `social_gm` at the same stage (stage 1) because `flow_b`
idles through stage 0.

Rules: trailing empty slots are trimmed (no effect); a chain made of only empty
slots is rejected ("cannot be empty"); and the strictly-increasing-by-`sequence`
rule applies only to the real (non-null) GMs in the chain. Empty slots have no
effect under `multi_gm` / `multi_gm_serial` — the null hops are simply dropped —
so they only change behavior under `multi_gm_staged`.

### Branch Routing

A `flow_to_gms` chain entry may also be a **branch node** —
`{branch: {router, choices}}` — that routes each of the flow's agents to exactly
ONE of `choices` (alternative GM names) at that one stage. A branch occupies a
single chain position (one stage), so the GMs before and after it still run once
on every agent; the branch only fans agents across its choices at its own stage.

```yaml
flow_bindings:
  flow_to_gms:
    social_flow:
      - branch:
          router:
            built_in: agent_choice
            params: { prompt: "You can act on {choices} this round. Reply with one.", on_invalid: random }
          choices: [twitter_gm, reddit_gm]
```

Rules: at most one branch per chain; at least two distinct, known choices whose
`sequence` values sit strictly between the branch's chain neighbours;
`multi_gm*` step mode only; and never inside a `flow_order` prefix flow. For
restore and per-GM `owned_flows`, a branch counts as any of its choices.

**Router.** The `router` is a [slot](configuration.md#slots), built by
`build_router` (`simulation_engines/policies/routers.py`,
`simulation_engines/policies/factory.py`) into a **plain callable** — there is no
base class and nothing to subclass (the shape is formalized by the structural
`Router` Protocol in `routers.py`):

```python
def route(agents, gms, ctx):
    # agents: the flow's agent objects  -> call agent.act(...) / agent.observe(...) freely
    # gms:    {gm name: game master}     -> one per choice, config order; read gm.backend freely
    # ctx:    RouteInfo(flow, step, seed) -> stable inputs for a replay-friendly decision
    return {agent.name: <chosen gm name> for agent in agents}   # cover every agent
```

**When it runs.** The engine resolves the branch when the flow's chain reaches
the branch stage — after the flow's earlier hops have drained, so the router sees
their backend effects (and may call the agents). This is the same in all three
traversals: under `multi_gm` (concurrent) the flow's own driver thread resolves it
once its prior hops finish; under `multi_gm_staged` when its stage column runs,
after the prior barrier; under `multi_gm_serial` when the row-major traversal
reaches it. In flight the unresolved branch is a `BranchHop`
(`simulation_engines/runtime_base.py`); the engine resolves it in
`scheduling.py::_resolve_branch`.

**What the engine owns** (all invisible to the router author):

1. *When* the router runs (above).
2. *Locking* — the router call runs UNLOCKED (it may be slow: LLM calls, backend
   reads). Only the follow-up per-chosen-GM `selected_turns` runs under that GM's
   lock (`_gm_lock`), the same lock a turn takes, so mid-step selection can never
   race a concurrent turn on the same GM.
3. *Validation* of the returned mapping — every agent covered, no unknown agent
   names, every value one of the branch's GMs; otherwise a clear `ValueError`.

**What the router does itself** — everything else. To read live backend state it
just reads `gms["twitter_gm"].backend`. To let the agent decide it builds its own
`ActionSpec` and calls `agent.act(...)`, interpreting the answer however it likes.
No capability flags, no context facade, no engine-provided ask helper.

**Built-in routers:**

- `random` (`RandomChoiceRouter`) — weighted pick, deterministic per
  `(seed, flow, step, agent)`, so a run reproduces and replays identically.
  `weights` maps a GM name to a relative weight (absent choices weigh 1.0).
- `agent_choice` (`AgentChoiceRouter`) — asks each agent to pick, via a CHOICE
  `ActionSpec`. Params: a `prompt` template with placeholders `{choices}` /
  `{flow}` / `{step}` / `{agent}`, and `on_invalid` = `random` (default) | `first`
  | `raise` — the fallback when the agent's answer names no choice OR when the
  routing call itself raises (provider outage, retry exhaustion), so one agent's
  transient model failure never aborts the run unless you opt into `raise`
  (default `random` is a replay-stable pick). Continuing is tracked, not silent:
  each fallback increments the `routing_fallbacks` [run-health
  counter](usage.md#run-health). It uses the shared `match_choice`
  helper (exact, then case-insensitive, then contained-once), which custom
  routers can import too.

A custom router is a `class_path` to a callable — a plain function (config
`params` bound as keyword arguments) or a class (built with `params`, instances
callable). An LLM-driven router is only as reproducible as the model it calls. See
[Simulation Extensibility API](simulation_extensibility_api.md) for the
custom-router authoring recipe.

**Router execution constraints.** Routing calls run serially on the flow's
chain-driver thread, outside the turn pool — they are not governed by
`sim.max_concurrent_actions` or `gm_concurrency_caps` (under `multi_gm_staged`
they additionally block the stage barrier). With an LLM-backed router, budget
one sequential model call per routed agent per branch stage; keep
`agent_choice` branches to modestly-sized flows, or use the `random` router for
large ones. Additionally, under the concurrent `multi_gm` traversal, branch
hops resolve on chain-driver threads whose *ordering* is timing-dependent: if
two flows branch into the same GM and that GM's `next_acting` component is
stateful (e.g. `fixed_order`), the acting order at that GM is not replay-stable.
Use `multi_gm_serial` or `multi_gm_staged` (which resolve branches in
deterministic flow order) when exact replay matters for such a topology.

### Per-GM Concurrency Caps

`sim.engine.step.params.gm_concurrency_caps` (a `{gm_name: int}` map) caps how
many of that GM's agent turns run concurrently, via a per-GM semaphore. The
effective per-GM limit is `min(cap, worker_limit)`, where `worker_limit` is the
global `sim.max_concurrent_actions` ceiling (also the default for every
uncapped GM). An empty map means the global cap governs every GM (unchanged).
It applies under any step mode.

```yaml
engine:
  step:
    built_in: multi_gm
    params:
      gm_concurrency_caps:
        mastodon_gm: 4        # at most 4 of this GM's turns run at once
```

Use this to throttle a rate-limited backend — for example a live Mastodon
server — below the global limit. Because the cap is per GM, other GMs keep
running concurrently at the global limit while the capped GM is serialized to
its own semaphore. It is orthogonal to `gm_turn_policies` (which controls how
many actions a single turn takes, not how many turns run at once).

### Per-Flow Component Configuration

Configure different component behavior per flow:

```yaml
gm:
  components:
    observe:
      instances:
        active_feed:
          built_in: timeline_every_turn
          params:
            timeline_mode: pure_recsys
        default_feed:
          built_in: timeline_every_turn
          params:
            timeline_mode: follower_chronological
        fixed_pre_episode:
          built_in: episode_only
      flow_map:
        active: active_feed
        default: default_feed
        fixed_pre: fixed_pre_episode
```

**Implementation:**
1. Each component instance is a normal slot component.
2. The GM parses `flow_map` and chooses the component instance for the agent's flow.
3. Custom components do not need flow-aware code.

Example component:

```python
from silisocs.environments.gm.components.base import ObservationComponent

class CustomObserve(ObservationComponent):
  def __init__(self, *, source: str):
    self.source = source

  def make_observation(self, agent_name: str) -> str:
    return f"{agent_name} sees {self.source}"
```

---

## Implementation Details

### Runtime Construction

`silisocs.runtime.construction.game_masters.build_game_masters(...)` converts
Hydra config into native `GameMasterConfig` specs. In multi-GM mode every item
under `env.gm_orchestration.gms` must define its own `backend` and
`components`; defaults are not inherited into orchestrated GMs.
`flow_bindings.flow_to_gms` is validated up front: every referenced GM must be
declared, every chain must contain at least one real GM (a chain of only empty
`null` slots is rejected; trailing empty slots are trimmed), duplicate GMs in a
chain are rejected, and multi-GM chains must follow strictly increasing
`sequence` values across their real (non-null) GMs.

### Multi-GM step strategies

There is one runtime engine (`RuntimeEngine`); the multi-GM traversal is a step
strategy selected by `sim.engine.step.built_in` (`multi_gm` / `multi_gm_serial` /
`multi_gm_staged`). `MultiGMStepStrategy` owns the resolved `flow_chains` routing
topology (supplied by `build_engine` from config via `resolve_flow_chains`, not
read off a game master), groups agents by flow, and executes each flow through its
configured GM chain. A flow with no explicit binding falls back to the
earliest-sequence GM. `agent_to_flow` overrides are included in routing metadata
even when no persona class declares that flow.

Chain traversal mode is selected by `sim.engine.step.built_in`. Under the
default `multi_gm` (concurrent) mode, distinct flows run as independent pipelines
whose hops serialize only when they contend for the same GM (via the engine's
existing per-GM lock), with `flow_order` flows run first as a serial prefix;
under `multi_gm_serial` each flow runs its full chain to completion before the
next flow in deterministic flow-by-flow order; under `multi_gm_staged` flows
advance one stage at a time behind a global per-stage barrier (with empty `null`
slots idling a flow at a stage to keep chains aligned).

Checkpoint replay for multi-GM runs is strict: the replay strategy uses the
agent's materialized flow and that flow's GM chain, then requires exactly one
GM in the chain to expose the replayed backend action. Missing flow metadata,
unknown GMs, no action match, and ambiguous action matches fail loudly.

### Routed Slot Components

Located: `src/silisocs/environments/gm/components/base.py`

Flow-routed GMs can route every slot: `initialize`, `next_acting`,
`action_prompt`, `observe`, `resolve`, and `update`. The component APIs remain
the same as the single-flow APIs; routing is a GM responsibility.

---

## Testing

```bash
uv run pytest tests/test_multi_gm_runtime_engine.py -v
uv run pytest tests/test_runner_processing_mode.py::test_multi_gm_specs_can_use_distinct_backends -v
uv run pytest tests/test_initializer_bootstrap.py::test_checkpoint_restore_routes_replay_to_matching_gm_backend -v
```

## Performance Considerations

- **Flow chains are concurrent by default**: under the default `multi_gm` step
  strategy, distinct flows advance concurrently through their GM chains,
  serializing only when two flows touch the same GM at the same time (use
  `sim.engine.step.built_in: multi_gm_serial` for the legacy flow-by-flow
  traversal). Within a single flow, an agent's own chain hops stay serial.
- **Staged mode trades throughput for stage alignment**: `multi_gm_staged`
  synchronizes all flows at a global per-stage barrier, which can leave the
  worker pool idle at stage tails (a fast flow waits for slow flows). Prefer
  `multi_gm` when you do not need stage alignment.
- **Within-batch agent turns can run in parallel**: agents selected by the same
  GM and flow are isolated as separate turns.
- **Backend state is per GM**: this release does not share one live backend
  object across multiple GMs.


---

## FAQ

**Q: Can agents move between GMs during simulation?**
A: Flow assignment is configured before the run. Dynamic reassignment requires a
custom engine policy.

**Q: Can GMs share state?**
A: Not as a shared backend object in this release. Use one backend per GM, or
persist shared state externally in custom components.

**Q: Do flow chains run concurrently or sequentially?**
A: Concurrently by default. Under the default `sim.engine.step.built_in: multi_gm`,
distinct flows run as independent pipelines through their GM chains; a flow's next
hop starts as soon as its own previous hop completes, and turns serialize only
when two flows touch the same GM at the same time. Flows in `flow_order` run first
as a strict serial prefix (preserving precedence like `fixed_pre` before
`default`), and a single agent's own chain hops always stay serial. Set
`sim.engine.step.built_in: multi_gm_serial` for the legacy behavior, where each
flow runs its full GM chain to completion before the next flow in deterministic
flow-by-flow order. A third strategy, `multi_gm_staged`, advances every flow one
stage at a time behind a global per-stage barrier (using empty `null` chain slots
to keep differently-shaped chains stage-aligned).

**Q: Can I route slots by flow without multi-GM?**
A: Yes. A single GM can use `instances + flow_map` on any typed component slot.

**Q: Do I need to modify existing components?**
A: No. Flow routing is handled by the GM; components expose the same direct slot
methods.

---

## See Also

- [Environment Layer](environment_layer.md): GM and engine extensibility
- [Configuration Reference](configuration.md#gm-components): Full config schema
- Test files: `tests/test_*.py` for examples
