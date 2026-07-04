# Silisocs Architecture: Flows, Game Masters, and Component Routing

**This guide is for LLM agents helping understand or extend the framework's architecture.**

**For designing experiments via configuration:** See [world_design.md](world_design.md)

**For code extension points:** See [AGENTS.md](../AGENTS.md)

---

## Overview

Silisocs is a configurable runtime for social simulations. The core runtime has
four major pillars:

1. **Agents**: simulated actors that observe context and return typed actions.
2. **Environment**: game masters and backends that expose the world agents act in.
3. **Evaluations**: probes and post-run measurements.
4. **Engine**: initialization, scheduling, turn execution, checkpoints, and logs.

Within the environment pillar, the **Game Master** is the coordinator and the
**Backend** owns domain state. A Game Master does not act like an agent. It
routes each native method to explicit component slots:

- `initialize`: prepare backend/environment state.
- `next_acting`: choose which candidate agents act.
- `action_prompt`: produce the typed `ActionSpec` for one agent.
- `observe`: build the observation for one agent.
- `resolve`: apply one agent's typed action to the backend.
- `update`: refresh environment state before actor selection, such as social recommendations.

This document focuses on two related but independent ideas:

- **Engine flows**: how the Engine schedules groups of agents during a step.
- **Game Master flow routing**: how a Game Master selects different component
  instances for agents in different flows.

---

## Part 1: Core Execution Model

### Two Independent Flow Choices

Silisocs separates Engine scheduling from Game Master component routing.

| Choice | Config location | What it controls |
|---|---|---|
| Engine step policy | `sim.engine.step.built_in` | Whether the Engine runs one group, one-agent batches, or named flow groups in order |
| Game Master class/components | `env.gm.class_path` and `env.gm.components` | Whether one GM slot instance serves all agents or per-flow slot instances are routed |

You can use either choice independently:

| GM routing | Engine flow scheduling | Effect |
|---|---|---|
| single-slot GM | base step | simplest mode: one GM and one global turn policy |
| routed GM | base step | one Engine group, but observations/resolution/prompts can differ by agent flow |
| single-slot GM | flow step | Engine runs flow groups in order, but all flows share the same GM slots |
| routed GM | flow step | full flow mode: ordered Engine phases plus per-flow GM behavior |

### Simple Runtime Path

In the default path, the Engine asks one Game Master to update, select actors,
prompt each actor, observe, and resolve:

```text
Engine startup
  -> agent initialization
  -> game-master initialization
  -> simulation initialization

Each step
  -> gm.update(step, agents, context)
  -> gm.acting_agents(candidate_agents)
  -> for each selected agent:
       observation = gm.make_observation(agent.name)
       action_spec = gm.action_prompt(agent.name)
       action = agent.act(action_spec)
       gm.resolve_action(agent.name, action)
```

No string-prefix dispatch is involved in the native path. Agent names are passed
as names, prompts are typed `ActionSpec` objects, and actions are typed
`ActionOutput` objects.

In the `multi_gm*` step modes, this same shape is applied across several Game
Masters. How flow chains traverse their GMs is selected by
`sim.engine.step.built_in`, which offers three multi-GM strategies — `multi_gm`
(concurrent, default), `multi_gm_serial` (legacy row-major), and
`multi_gm_staged` (column-major with a global per-stage barrier). The former
`sim.engine.step.params.chain_execution` knob has been removed (a config that
still sets it raises a `ValueError` with a migration hint):

```text
Each step
  -> for each configured GM in sequence order:
       gm.update(step, agents, context)
  -> group agents by flow

  built_in: multi_gm (DEFAULT, concurrent)
    -> flows in flow_order run first as a strict serial prefix
       (preserves declared precedence, e.g. fixed_pre before default)
    -> every other flow runs as the concurrent group:
         distinct flows advance as independent pipelines through their
         GM chains; a flow's next hop starts as soon as its own previous
         hop completes. Turns serialize ONLY when two flows touch the same
         GM at the same time (the engine's per-GM lock). A single agent's
         own chain hops always stay serial, since each hop observes the
         prior hop's resolution.

  built_in: multi_gm_serial (legacy row-major)
    -> for each flow in flow-by-flow ("row-major") order:
         each flow runs its full GM chain to completion before the next
         flow, one batch at a time:
           for each GM in the flow's configured chain:
             gm.acting_agents(flow_agents)
             selected agents observe, act, and resolve through that GM

  built_in: multi_gm_staged (column-major, global per-stage barrier)
    -> flows in flow_order run first as a strict serial prefix
       (same as multi_gm)
    -> advance every remaining flow ONE STAGE AT A TIME:
         for stage N = 0, 1, 2, ...:
           all flows' stage-N hops run concurrently (an empty/null chain
           slot means the flow idles this stage, resuming at its next
           non-null hop, so differently-shaped chains stay aligned)
           GLOBAL BARRIER: stage N+1 does not begin until ALL of stage N
           finishes (trades throughput for stage alignment: a fast flow
           waits for slow flows at the barrier)
```

The update phase is per GM and per step, not per flow-chain hop. Flow chains
route selected agent turns only.

### Engine Flow Scheduling

Engine flows are agent groups. Agents with the same `flow_tag` can act together;
flows are then run in a configured sequence. This belongs to the Engine step
strategy, not the base Engine.

```yaml
sim:
  engine:
    step:
      built_in: flow
      params:
        flow_order: [fixed_pre, default]
        agent_to_flow:
          News Bot: fixed_pre
          Alice: default
          Bob: default
```

Scenario builders usually put each agent's flow in params:

```yaml
agents:
  persona_pipeline:
    classes:
      regular_users:
        class_path: silisocs.agents.native.NativeAgent
        flow_tag: default
      news_bot:
        class_path: silisocs.agents.fixed.FixedAgent
        flow_tag: fixed_pre
```

The runner can derive `agent_to_flow` from those agent params. Explicit Engine
config remains useful for unusual studies where the scheduling flow should not
match the persona class flow.

`agent_to_flow` is resolved before the Engine starts. The finalized
`agent_flow_tags` map is passed to every Game Master and is the only flow source
used during turn scheduling.

In multi-GM runs, `flow_to_gms` chains must be explicit and valid: each chain
references known GMs, contains at least one real GM, avoids duplicate names, and
uses increasing GM `sequence` values across its real GMs. Flows without a binding
fall back to the earliest-sequence GM. The traversal mode is selected by
`sim.engine.step.built_in`: by default (`multi_gm`) these chains execute
concurrently — distinct flows advance as independent pipelines, serializing only
when they touch the same GM at once; set `multi_gm_serial` for the legacy
flow-by-flow traversal, or `multi_gm_staged` for column-major traversal behind a
global per-stage barrier (where a `null` chain entry idles a flow at that stage so
differently-shaped chains stay aligned).

Checkpoint replay is per-agent-flow-chain regardless of the traversal mode. The
replay router receives the resolved `flow_chains` routing topology from config
(not read off any Game Master), so in multi-GM replay exactly one GM in the
Agent's flow chain must expose the replayed action; otherwise restore fails
instead of falling back to another GM.

### Branch Routing (dynamic GM selection)

A `flow_to_gms` chain entry may be a **branch node** —
`{branch: {router, choices}}` — that routes each of the flow's agents to one of
`choices` (alternative GM names) at that one stage (the GMs before and after the
branch still run once on every agent). The `router` is a
`{built_in|class_path, params}` slot built by `build_router`
(`simulation_engines/policies/routers.py`, `policies/factory.py`) into a **plain
callable** — there is no base class:

```python
def route(agents, gms, ctx):
    # agents: the flow's agents (call agent.act(...) freely)
    # gms:    {gm name: game master} (one per choice; read gm.backend freely)
    # ctx:    RouteInfo(flow, step, seed)
    return {agent.name: <chosen gm name> for agent in agents}
```

The engine resolves the branch when the flow's chain reaches the branch stage —
after the flow's earlier hops drain, so the router sees their backend effects and
may call the agents — in all three traversals: on the flow's driver thread under
`multi_gm`, at the stage column (after the barrier) under `multi_gm_staged`, and
when the row-major traversal reaches it under `multi_gm_serial`. In flight the
unresolved branch is a `BranchHop` (`simulation_engines/runtime_base.py`), resolved
in `scheduling.py::_resolve_branch`. The engine owns only *when* the router runs,
*locking* (the router call runs unlocked; only the follow-up per-chosen-GM
`selected_turns` is locked per-GM via `_gm_lock`), and *validation* of the returned
assignment (every agent covered, every GM a real choice). Everything else — reading
`gms[...].backend`, asking an agent via a self-built `ActionSpec` — the router does
itself; there are no capability flags or context facades. A `class_path` may be a
function (config `params` bound as kwargs) or a class (built with `params`).
Built-ins: `random` (weighted, deterministic per `(seed, flow, step, agent)`) and
`agent_choice` (asks each agent via a CHOICE probe; `prompt` / `on_invalid` params;
uses the shared, importable `match_choice` helper). The full config schema and
rules (≤1 branch per chain, ≥2 choices, `multi_gm*` only) live in
[configuration.md](../docs/configuration.md); the custom-router authoring recipe
is in [simulation_extensibility_api.md](../docs/simulation_extensibility_api.md).

### Sim-Level Participation

Participation is the Engine-layer half of agent selection. It filters the step's
agent roster **before** any scheduling (flow grouping, chain hops) and before
every Game Master's `next_acting` component runs. The GM slot stays the home of
environment-derived selection (turn order, backend state); participation is the
home of config-derived simulation logic (activity models). Effective acting per
hop is `participation ∩ next_acting`.

```yaml
sim:
  engine:
    participation:
      built_in: activity_probability   # all | activity_probability | activity_markov
      params:
        activity_transition_rates:
          voter: {inactive_to_active: 0.1, active_to_inactive: 0.2}
          candidate: {inactive_to_active: 0.8, active_to_inactive: 0.1}
```

- `all` — pass-through; every agent participates every step (use for
  deterministic / turn-based runs).
- `activity_probability` — independent per-step activation draw per agent, keyed
  by agent name or sim role.
- `activity_markov` — a role-conditioned active/inactive Markov chain per agent.
- Or a `class_path` to a custom `ParticipationPolicy`.

Participation policies are **pure functions of `(agent_names, step_index, seed)`**
— they hold no evolving runtime state, so there is nothing to checkpoint and a
resumed run reproduces the exact same participation draws as an uninterrupted
one (the Markov policy achieves this by re-deriving its chain from step 0 rather
than persisting per-agent state).

### Per-GM Cadence and Concurrency

Two per-GM knobs tune each Game Master's turn behavior. Both are resolved per
batch by GM name, so they apply under **any** step mode (not just multi-GM), and
both default to empty (global behavior everywhere).

- `sim.engine.step.params.gm_turn_policies` — a `gm_name -> {built_in|class_path,
  params}` map setting how MANY actions each turn takes for that GM. Precedence is
  per-flow (`flow_turn_policies`) > per-GM > global `sim.engine.turn_policy`. Lets
  a "world" GM run `single_action` while a social GM runs `open_ended`, and
  disambiguates multi-GM-chain hops that share one flow (which flow-keyed policies
  cannot).
- `sim.engine.step.params.gm_concurrency_caps` — a `gm_name -> int` map capping how
  many of that GM's agent turns run AT ONCE, via a per-GM semaphore. Effective
  per-GM concurrency is `min(cap, sim.max_concurrent_actions)`; the global stays
  the overall ceiling and per-GM default. Orthogonal to `gm_turn_policies`: turn
  policy is how many actions a turn takes, this is how many turns run at once. Use
  it to throttle a rate-limited backend below the global limit while other GMs keep
  running concurrently.

---

## Part 2: Game Master Component Routing

### Single-Slot Game Master

The default `ComponentGameMaster` uses one component per slot:

```yaml
env:
  gm:
    class_path: silisocs.environments.gm.game_master.ComponentGameMaster
    components:
      initialize:
        built_in: social_media
        params: {}
      next_acting:
        built_in: all_agents
        params: {}
      action_prompt:
        built_in: default
        params: {}
      observe:
        built_in: timeline_every_turn
        params:
          recsys_type: null
      resolve:
        built_in: tool_calling
        params: {}
      update:
        built_in: social_recommendation
        params:
          update_every_n_steps: 1
```

This is the right default for most worlds: all agents share the same
observation style, prompt style, resolver, and update behavior.

### Flow-Routed Game Master

`MultiFlowGameMaster` keeps the same public Game Master methods, but each slot
can contain multiple component instances. The GM chooses the right instance from
`flow_map` based on the acting agent's flow.

```yaml
env:
  gm:
    class_path: silisocs.environments.gm.game_master.MultiFlowGameMaster
    components:
      observe:
        instances:
          timeline:
            built_in: timeline_every_turn
            params:
              timeline_mode: follower_chronological
          episode:
            built_in: episode_only
            params: {}
        flow_map:
          default: timeline
          fixed_pre: episode

      resolve:
        instances:
          tools:
            built_in: tool_calling
            params: {}
          parsed:
            built_in: parsed_action
            params: {}
        flow_map:
          default: tools
          fixed_pre: parsed
```

The same `instances + flow_map` shape works for all GM slots:

- `initialize`
- `next_acting`
- `action_prompt`
- `observe`
- `resolve`
- `update`

Custom components do not need to know about flows. They implement one slot
interface, and `MultiFlowGameMaster` handles selection.

---

## Part 3: Recommendation Updates

Recommendation refresh is a social-media **update** behavior, not a special GM
lifecycle concept. The built-in social update component can initialize and
refresh one or more recommendation algorithms:

```yaml
env:
  gm:
    components:
      update:
        built_in: social_recommendation
        params:
          default_recsys_type: twitter
          update_every_n_steps: 1
          max_posts: 10
```

With a routed GM, different flows can use different update instances:

```yaml
env:
  gm:
    components:
      update:
        instances:
          twitter_ranker:
            built_in: social_recommendation
            params:
              default_recsys_type: twitter
          reddit_ranker:
            built_in: social_recommendation
            params:
              default_recsys_type: reddit
        flow_map:
          active: twitter_ranker
          lurker: reddit_ranker
```

Observation components then fetch timelines using the configured backend
timeline mode, for example:

- `follower_chronological`
- `pure_recsys`
- `hybrid_recsys_follower`
- `curated_global` where supported

---

## Part 4: Agent Builders and Agent Classes

Scenario YAML does not instantiate agents directly. An **Agent Builder** reads
`agents.persona_pipeline` and returns `AgentConfig` records. Runtime construction
then imports each configured class and passes in the language model plus params.

Common native classes:

| Class | Use |
|---|---|
| `silisocs.agents.native.NativeAgent` | Normal LLM-driven social agents |
| `silisocs.agents.fixed.FixedAgent` | Deterministic scripted agents |
| `silisocs.agents.concordia.ConcordiaAgent` with `compat: concordia` | Explicit legacy compatibility |

Native agents receive typed observations and call `Agent._call_model(context,
action_spec)` inside their `act(...)` method. Concordia compatibility is wrapped
before runtime execution and is not part of the native Engine path.

---

## Part 5: Configuration Patterns

### Simple Social Scenario

```yaml
sim:
  engine:
    step:
      built_in: base
    turn_policy:
      built_in: single_action

env:
  gm:
    class_path: silisocs.environments.gm.game_master.ComponentGameMaster
    components:
      observe:
        built_in: timeline_every_turn
      resolve:
        built_in: tool_calling
      update:
        built_in: social_recommendation
```

### Flow-Scheduled Scenario

```yaml
sim:
  engine:
    step:
      built_in: flow
      params:
        flow_order: [fixed_pre, default]
    turn_policy:
      built_in: single_action
```

### Flow-Routed GM Scenario

```yaml
env:
  gm:
    class_path: silisocs.environments.gm.game_master.MultiFlowGameMaster
    components:
      observe:
        instances:
          normal_timeline:
            built_in: timeline_every_turn
          summary_view:
            built_in: episode_only
        flow_map:
          default: normal_timeline
          fixed_pre: summary_view
```

---

## Part 6: Extension Guidance

Use the smallest extension point that matches the change:

1. Change personas, flows, probes, or run scale in YAML.
2. Add or subclass an Agent when behavior needs custom context assembly.
3. Add a GM component when environment-facing behavior changes for one slot.
4. Add a Backend when the domain state or operations change.
5. Add an Engine step policy only when scheduling semantics change.

The native public surfaces are intentionally direct. Avoid reintroducing hidden
dispatch strings, implicit global config reads, or Concordia lifecycle methods
into native code. Compatibility belongs behind `compat: concordia` and the
adapter package.

---

## Summary

The flow architecture gives world authors two independent controls:

| Capability | Engine flow scheduling | GM component routing |
|---|---|---|
| Ordered groups of agents | yes | no |
| Different observations by flow | no | yes |
| Different action prompts by flow | no | yes |
| Different resolvers by flow | no | yes |
| Different recommendation updates by flow | no | yes |
| Custom per-flow component code | no; use strategies | no; use routed instances |

Choose the simple path unless a study needs different agent groups to run in a
specific order or see/resolve the environment differently.
