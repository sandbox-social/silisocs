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

In `multi_gm` step mode, this same shape is applied across several Game
Masters:

```text
Each step
  -> for each configured GM in sequence order:
       gm.update(step, agents, context)
  -> group agents by flow
  -> for each flow in flow_order:
       for each GM in the flow's configured chain:
         gm.acting_agents(flow_agents)
         selected agents observe, act, and resolve through that GM
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
references known GMs, contains at least one GM, avoids duplicate names, and
uses increasing GM `sequence` values. Flows without a binding fall back to the
earliest-sequence GM.

Checkpoint replay uses that same flow metadata. In multi-GM replay, exactly one
GM in the Agent's flow chain must expose the replayed action; otherwise restore
fails instead of falling back to another GM.

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
        built_in: activity_probability
        params:
          min_active_agents: 1
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
