# Silisocs Runtime

Silisocs is a configurable social simulation runtime. It models agents acting
inside environments, records what happens, and evaluates the resulting behavior.

## Language

**Agent**:
A simulated actor that observes an environment and produces typed actions.
_Avoid_: Entity, player, Concordia agent

Default agents use `NativeAgent`: they assemble semantic context from
instructions, persona, scenario, goal, style, recent observations, and memory,
then route through `Agent._call_model(context, action_spec)`.

**Agent Name**:
The unique simulation identity for an Agent. The same name is used to route
observations and actions, connect the Agent to backend state, select flows, run
probes, and write logs/checkpoints.
_Avoid_: Treating names as optional labels or deriving runtime identity silently

**Agent Builder**:
The config-to-agent-spec translator that returns `AgentConfig` records for
runtime construction. The default builder reads `agents.persona_pipeline`, and
custom builders may provide different config-to-spec logic.
_Avoid_: Runtime agent, agent factory, prefab

**Environment**:
The simulated world that agents act inside.
_Avoid_: Simulator, social system

**Game Master**:
The environment-facing coordinator that owns one `backend` plus typed slots for
initialization, update, actor selection, prompting, observation, and resolution.
_Avoid_: GM-as-agent, simulator object

**Game Master Component**:
A native helper selected under `env.gm.components.<slot>` that implements one
direct role method such as `initialize(...)`, `acting_agent_names()`,
`action_prompt(agent_name)`, `make_observation(agent_name)`,
`resolve_action(agent_name, action)`, or `update(step, agents, context)`.
Behavior knobs live with the component that owns them: graph setup under
`initialize.params.graph`, activity rates under `next_acting.params`,
timeline settings under `observe.params`, and recommendation settings under
`update.params`.
_Avoid_: Concordia lifecycle component, `pre_act`, `post_act`

**Backend**:
The domain state and operations exposed by an environment.
_Avoid_: App when referring to the whole environment

**BackendApp**:
The domain-neutral backend contract: initialization, observations, action
discovery, action schemas, and action invocation.
_Avoid_: Social timeline contract

**SocialBackendApp**:
A backend capability interface for timeline, feed formatting, parsed social
actions, social setup, and recommendation hooks. Backends remain peer
environment choices; this interface only marks the extra surface needed by
timeline and recommendation components.
_Avoid_: Putting social methods on `BackendApp`

**Engine**:
The runtime loop that initializes objects, schedules turns, and advances the simulation.
_Avoid_: Simulation object, runner

**Engine Loop Policy**:
The Engine subcomponent that owns the outer episode lifecycle.
_Avoid_: Hiding startup or per-agent action logic in the loop

**Engine Step Policy**:
The Engine subcomponent that decides which Game Master/agent batches run during
one episode step. Flow and multi-GM sequencing live here.
_Avoid_: Turn policy, Game Master component

**Turn Policy**:
The Engine subcomponent that decides how many observe/act/resolve cycles a
selected agent may take in one step.
_Avoid_: Action chunk

**Initializer**:
A slottable startup object owned by one explicit Engine startup phase.
_Avoid_: Initializer GM, hidden runtime bootstrap

**Agent Initialization**:
The first Engine startup phase. It prepares agent-owned state such as configured
or formative memories, then calls each agent's `initialize(...)` hook with a
typed native payload.
_Avoid_: Memory-bank setup, GM-mediated agent setup

**Game Master Initialization**:
The second Engine startup phase. It calls each Game Master's canonical
`initialize(agents, context)` method so the GM can prepare backend/environment
state through its own initializer slot.
_Avoid_: `initialize_backend`, global backend initializer

**Simulation Initialization**:
The third Engine startup phase. It prepares application-level starting content
after agents and backends exist. Seed posts are generated or loaded here and
posted through normal `GameMaster.resolve_action(...)` tool-call actions.
_Avoid_: Backend seed-post helpers, `post_seed_posts`

**Evaluation**:
A post-run or in-run measurement of agent behavior or environment state.
_Avoid_: Probe when referring to all evaluation forms

**Probe**:
An in-run question or measurement directed at agents. Probe prompts ask the
measurement question and answer-format constraint only; agent identity and
persona context belong to the agent.
_Avoid_: Query when describing runtime behavior, probe-owned identity framing

**Checkpoint Source Run**:
A previous output directory used as the source for checkpoint restore.
_Avoid_: Passing individual checkpoint files as public resume inputs

**Checkpoint Restore Strategy**:
A checkpoint-owned object that rebuilds runtime/backend state from a source
run after the Engine has initialized agents and game masters. The built-in
social restore replays backend action events through `GameMaster.resolve_action`.
_Avoid_: Simulation initializer replay, agent-turn replay payloads

**Compatibility Adapter**:
An explicit boundary for running older Concordia-shaped agents or game masters.
_Avoid_: Fallback, normal runtime path

Compatibility requires `compat: concordia` in config. Native runtime packages
must not import `concordia.*` or local Concordia-like component/document copies.

## Relationships

- An **Engine** runs one simulation using many **Agents** and one or more **Game Masters**.
- The runner selects an **Agent Builder** before runtime objects are
  instantiated; the builder returns specs, not live agents.
- Engine startup has exactly three native phases: **Agent Initialization**,
  **Game Master Initialization**, then **Simulation Initialization**.
- A **Game Master** coordinates exactly one **Backend** for its environment scope.
- An **Agent** acts only through typed action requests and typed action responses.
- An **Evaluation** may read run artifacts, and a **Probe** may ask **Agents** questions during the engine loop.
- A **Compatibility Adapter** is opt-in and is not part of the normal runtime path.
- Native `ActionSpec` uses `prompt`, `output_type`, `options`, `tag`, and
  `extra_args`; `call_to_action` is only a Concordia adapter concern.
- Native Game Masters expose only `initialize`, `update`, `acting_agents`,
  `action_prompt`, `make_observation`, and `resolve_action`.
- Native Game Master Components keep the public `gm.components` name for
  configuration, but they are plain Silisocs helpers with no Concordia
  lifecycle methods. Per-flow behavior routes to separately configured
  component instances; components do not implement flow-field mixins.
- `env` wires `backend` and `gm`; it is not a behavior namespace. Component
  params own behavior controls.
- Checkpoint restore is configured under `sim.checkpoint.source_run` plus
  `sim.checkpoint.restore`; it is not a Simulation Initialization mode.

## Example Dialogue

> **Dev:** "Can this custom Concordia game master be used directly by the engine?"
> **Domain expert:** "No. A **Game Master** must satisfy the native Silisocs surface. Concordia-shaped code needs an explicit **Compatibility Adapter**."

## Flagged Ambiguities

- "simulation" has been used to mean both the **Engine** loop and an old opaque object; resolved: the **Engine** owns the loop.
- "GM" has been used as both a normal **Game Master** and a compatibility shim; resolved: compatibility belongs only in a **Compatibility Adapter**.
- "initializer" has been used as a hidden simulator-like object; resolved:
  the **Engine** owns explicit agent, game-master, and simulation startup phases.
