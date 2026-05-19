# Silisocs Architecture: Native Runtime, Flows, and Component Routing

This guide is for LLM agents and contributors helping understand or extend the
framework's architecture. For designing experiments via configuration, see
[scenario_design.md](scenario_design.md). For custom code extension points, see
[AGENTS.md](../AGENTS.md).

Silisocs is designed as a configurable social simulation system with clean,
native extension surfaces. Concordia-shaped code is legacy compatibility only
and belongs behind `compat: concordia`.

## Four Pillars

Silisocs is organized around four core surfaces:

1. **Agents** decide what to do. Native agents subclass `silisocs.agents.base_agent.Agent`
   and usually use `NativeAgent` or `FixedAgent`.
2. **Environment** owns game masters and backends. A game master observes backend
   state, prompts agents, resolves typed actions, and runs update components.
3. **Evaluations** run probes and postprocessors over the native runtime surface.
4. **Engine** owns startup phases and the simulation loop.

## Config To Execution

Runtime startup follows this path:

1. Hydra composes config groups.
2. `runtime.execution.session` validates migrated config keys.
3. Construction helpers create models, agents, game masters, initializers, probes,
   and an engine from direct `class_path` + `params` specs.
4. The engine initializes agents, then game masters, then simulation setup such as
   seed posts.
5. The engine loop runs step strategies, turn policies, probes, logging, metrics,
   and checkpoint policy.

`runtime.runner` is only the CLI entrypoint. Most construction and validation
logic lives under focused runtime subpackages.

## Agents

Native agents expose:

```python
initialize(context) -> None
observe(observation: str) -> None
act(action_spec: ActionSpec) -> ActionOutput
get_state() -> dict
set_state(state: Mapping[str, Any]) -> None
```

`Agent._call_model(context, action_spec)` is the native model-routing helper. Agent
implementations build their own context in `act()` and call `_call_model`.

Use:

- `silisocs.agents.native.NativeAgent` for persona/goal/context driven agents.
- `silisocs.agents.fixed.FixedAgent` for scripted action plans.
- `compat: concordia` only for legacy Concordia prefabs.

## Game Masters

The native game master public surface is intentionally flat:

```python
initialize(agents, context) -> None
update(step, agents, context) -> None
acting_agents(candidate_agents) -> list[str]
action_prompt(agent_name: str) -> ActionSpec
make_observation(agent_name: str) -> str
resolve_action(agent_name: str, action: ActionOutput | str) -> str
```

`env.gm.components` contains typed native slots:

- `initialize`: backend/user/social graph setup.
- `next_acting`: actor selection.
- `action_prompt`: typed prompt and output specification.
- `observe`: per-agent observation.
- `resolve`: typed backend action execution.
- `update`: per-step backend updates such as recommendations.

Components are plain Silisocs helper objects. They do not expose Concordia
lifecycle hooks such as `pre_act`, `post_act`, `set_entity`, or `get_entity`.

## Flow Scheduling and Component Routing

There are two separate flow mechanisms. They can be used together, but they
solve different problems:

- **Engine flow scheduling** controls which groups of agents act in which sequence
  during a step. Configure this under `sim.engine.step`.
- **GM component routing** selects per-flow component instances inside a
  `FlowRoutedGameMaster`. Configure this with `instances + flow_map` inside each
  GM component slot.

Example routed observation slot:

```yaml
env:
  gm:
    class_path: silisocs.environments.gm.game_master.FlowRoutedGameMaster
    components:
      observe:
        instances:
          active:
            built_in: timeline
            params: {}
          summary:
            built_in: episode_summary
            params: {}
        flow_map:
          default: active
          fixed_pre: summary
```

Custom components stay flow-unaware; the GM routes the call.

Use simple mode for most scenarios. Use flow scheduling when groups of agents
need a meaningful order inside each step. Use routed components when different
groups need different observations, prompts, action resolution, or update
behavior.

## Engine

The base engine is flow-free. It owns:

- `initialize(...)`
- `run_loop(...)`
- `run_step(...)`
- `run_agent_step(...)`

Scheduling behavior lives in strategies:

- single-step strategies for simple runs;
- flow strategies for ordered flow phases;
- multi-GM strategies for routing agents to assigned game masters.

Turn policy is configured under `sim.engine.turn_policy`; probe timing under
`evals.probes.schedule`.
