# Scenario Design Guide

This guide is for LLM agents helping design and create new simulation scenarios
using configuration, without modifying runtime code.

A scenario should feel like a coherent social world first and a YAML bundle
second. The config files describe the world, the cast, the platform, and the
measurement plan, but the design work starts with the social dynamics you want
to observe.

## 1. Scenario Design Workflow

A complete scenario consists of:

1. **Setting and event**: narrative context for the shared world.
2. **Agent population**: roles, counts, data sources, personas, goals, and styles.
3. **Platform configuration**: backend, available actions, timeline behavior, and
   followership structure.
4. **Initialization**: shared memories, initial observations, and optional seed posts.
5. **Probes**: evaluation questions to ask agents during or after the simulation.
6. **Run defaults**: default agent count, step count, seed, and output naming.

## 2. Directory Shape

```text
scenarios/<scenario_name>/conf/
  scenario/default.yaml   # @package _global_
  agents/default.yaml     # @package agents
  env.yaml                # optional env-group overrides
  evals.yaml              # optional evals-group overrides
  sim.yaml                # optional sim-group overrides
```

`scenario/default.yaml` and `agents/default.yaml` replace the package defaults.
The flat `env.yaml`, `evals.yaml`, and `sim.yaml` files are partial overrides
merged into their config groups.

## 3. Setting and Event

`scenario/default.yaml` defines the world and run defaults:

```yaml
# @package _global_
scenario_name: civic_forum
run_name: civic_forum
num_agents: 12
num_steps: 8
seed: 42
jobname_format: "CivicForum_N${num_agents}_T${num_steps}_${run_name}"

setting:
  name: Civic Forum
  background:
    - Residents discuss local policies on a social platform.
    - Commuters, local business owners, and organizers disagree about priorities.

event:
  name: Transit Vote
  context: A city vote on transit funding is approaching.

data: {}
```

Keep `setting.background` grounded and specific. Keep `event.context` focused on
what is happening now, because it is usually injected into agent context.

## 4. Agents

Agents are built directly from `class_path` + `params`.

```yaml
# @package agents
persona_pipeline:
  defaults:
    params:
      scenario_context: ${event.context}
      goal: Participate naturally in the discussion.
      style: ""
      bio: ""
    shared_memories:
      - ${event.context}

  classes:
    resident:
      count: 10
      class_path: silisocs.agents.native.NativeAgent
      sim_role_name: resident
      data:
        source: inline
        records:
          - name: Alice
            context: Alice commutes by bus and wants better service.
          - name: Bob
            context: Bob worries about taxes.
      field_map:
        name: name
        context: context
      flow_tag: default
```

Use:

| Need | Class path |
| --- | --- |
| Persona-driven participant | `silisocs.agents.native.NativeAgent` |
| Deterministic or scheduled account | `silisocs.agents.fixed.FixedAgent` |
| Legacy Concordia prefab | `silisocs.agents.concordia.ConcordiaAgent` with `compat: concordia` |

Data sources can be inline records, local JSON/JSONL files, HuggingFace datasets,
or config references. Use `field_map` to map source fields onto agent params.

## 5. Platform and Game Master

`env.yaml` chooses the backend and game-master components:

```yaml
platform_type: twitter_like
timeline_mode: follower_chronological

gm:
  class_path: silisocs.environments.gm.game_master.GameMaster
  components:
    initialize:
      built_in: social_media
      params: {}
    next_acting:
      built_in: all_agents
      params: {}
    action_prompt:
      built_in: tool_calling
      params: {}
    observe:
      built_in: timeline
      params: {}
    resolve:
      built_in: tool_calling
      params: {}
    update:
      built_in: disabled
      params: {}
```

`env.gm.components` are native typed slots. They are not Concordia lifecycle
components. The public GM methods are `initialize`, `update`, `acting_agents`,
`action_prompt`, `make_observation`, and `resolve_action`.

Social network settings usually live under `env.social_network`:

```yaml
social_network:
  activity_transition_rates:
    resident:
      inactive_to_active: 0.6
      active_to_inactive: 0.2
  fully_connected_targets: []
  base_followership_probability: 0.5
  network_type: barabasi_albert
  barabasi_albert_m: 2
```

## 6. Engine, Initialization, and Probes

`sim.yaml` should only contain scenario-specific runtime overrides:

```yaml
action_mode: generic
tool_calling:
  mode: multi

engine:
  loop:
    built_in: fixed_steps
    params: {}
  step:
    built_in: single_gm
    params: {}
  turn_policy:
    built_in: single_action
    params: {}

initialization:
  agents:
    built_in: default
    class_path: null
    params: {}
  game_masters:
    built_in: default
    class_path: null
    params: {}
  simulation:
    built_in: seed_posts
    class_path: null
    params:
      type: agent
      params: {}
```

Probe config uses probe language:

```yaml
probes:
  schedule:
    built_in: fixed_interval
    params:
      start_step: 1
      every_n_steps: 1
  deployment:
    enabled: true
    include_agents: []
    exclude_agents: []
  probes:
    vote_pref:
      probe_name: vote_pref
      probe_type: ChoiceProbe
      probe_data:
        name: VotePref
        question: Which option do you prefer?
        choices: [Yes, No]
```

Probe timing belongs under `evals.probes.schedule`, not the engine.

## 7. Common Patterns

### Active vs passive roles

Use `sim_role_name` plus activity transition rates:

```yaml
persona_pipeline:
  classes:
    organizers:
      count: 4
      sim_role_name: organizer
      flow_tag: active
    observers:
      count: 20
      sim_role_name: observer
      flow_tag: passive
```

```yaml
social_network:
  activity_transition_rates:
    organizer:
      inactive_to_active: 0.9
      active_to_inactive: 0.1
    observer:
      inactive_to_active: 0.2
      active_to_inactive: 0.5
```

### Flow scheduling or routed components

Agent `flow_tag` can support two separate features:

- Engine flow scheduling under `sim.engine.step`.
- Per-flow GM component routing with `FlowRoutedGameMaster` and component
  `instances + flow_map`.

Use these only when a scenario needs meaningful sequencing or different
observation/resolve/update behavior by group. Most scenarios should stay simple.

### Fixed-action accounts

Use `FixedAgent` for deterministic accounts:

```yaml
news_bot:
  count: 1
  class_path: silisocs.agents.fixed.FixedAgent
  sim_role_name: news_bot
  params:
    fixed_action_plan:
      - episode: 0
        tool_calls:
          - name: create_tweet
            arguments:
              status: Polls open next week.
```

## 8. Running and Validating

Run a scenario:

```bash
uv run silisocs --config-path scenarios/my_scenario/conf
```

Fast scripted smoke test:

```bash
uv run silisocs --config-path scenarios/my_scenario/conf \
  num_steps=1 sim.llm.provider=scripted sim.llm.name=scripted
```

Checkpoint restore is checkpoint behavior:

```yaml
sim:
  checkpoint:
    source_run: outputs/previous_run
    restore:
      built_in: social_action_event_replay
      class_path: null
      params: {}
```

Do not use removed prefab-era, processing-mode, old engine scheduling, old
probe-query, entity-filter, resume-file, or checkpoint-replay keys.
