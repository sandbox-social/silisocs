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
- Streamlit dashboard for scenario creation and launch

The runtime entrypoint is:

- `src/silisocs/runtime/runner.py`

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
- `src/silisocs/environments/backends/twitter_like/` — TwitterLikeApp with SQL backend
- `src/silisocs/environments/backends/reddit_like/` — RedditLikeApp
- `src/silisocs/environments/backends/mastodon/` — Real Mastodon server integration
- Actions discovered via `@app_action(name=..., description=...)` decorator
- To add custom backend: subclass `SocialBackendApp`, implement action methods, register in app factory

### 6. Runtime Orchestration
- `src/silisocs/runtime/runner.py` — CLI entrypoint, Hydra config composition
- `src/silisocs/runtime/config.py` — Config validation and initialization
- Handles: model creation, direct runtime construction, initialization,
  simulation execution, checkpoint save/resume

## 3) Configuration Model

Top-level config composition (`src/silisocs/conf/experiment.yaml`):

- Defaults: `world: default`, `agents: default`, `sim: base`, `env: twitter_like`, `eval: base`

Config groups and their base files:

| Group | Base file | Controls |
|-------|-----------|----------|
| `world` | `world/default.yaml` (`@package _global_`) | Run params, setting, event, data |
| `agents` | `agents/default.yaml` (`@package agents`) | Persona pipeline, shared memories |
| `sim` | `sim/base.yaml` (`@package sim`) | LLM, engine, tool-calling, memory, checkpoint |
| `env` | `env/twitter_like.yaml` (`@package env`) | Backend, GM components, initialization |
| `eval` | `eval/base.yaml` (`@package eval`) | Probe configuration |

Key sim knobs (`src/silisocs/conf/sim/base.yaml`):

| Parameter | Value | Notes |
|-----------|-------|-------|
| `sim.llm.name` | gpt-4o-mini | Default LLM model |
| `sim.llm.temperature` | 0.5 | Sampling temperature |
| `sim.llm.disabled` | false | No-op model for testing |
| `sim.action_mode` | custom | Prompt style (`custom` or `generic`) |
| `sim.tool_calling.mode` | single | `none` \| `single` \| `multi` |
| `sim.engine.step.built_in` | base | `base`, `sequential`, `flow`, `multi_gm`, `multi_gm_serial`, or `multi_gm_staged` (the three `multi_gm*` strategies select the flow-chain traversal mode; see below) |
| `sim.engine.turn_policy.built_in` | single_action | `single_action` \| `fixed_count` \| `open_ended` |
| `sim.engine.participation.built_in` | activity_probability | Sim-level per-step roster filter: `all` \| `activity_probability` \| `activity_markov` (or `class_path` to a custom `ParticipationPolicy`). Filters who is in the step's roster BEFORE scheduling and every GM's next_acting (effective acting = participation ∩ next_acting). Stateless/seed-derived, so replay- and resume-stable. The GM `next_acting` slot keeps only env-derived built-ins (`all_agents`, `fixed_order`); a config naming the moved built-ins there gets a migration error. Set `all` for deterministic/turn-based runs |
| `sim.engine.step.params.gm_turn_policies` | {} | Per-GM turn policy map (`gm_name -> {built_in\|class_path, params}`); applies under any step mode |
| `sim.engine.step.params.gm_concurrency_caps` | {} | Per-GM concurrency caps (`gm_name -> int`); effective per-GM = `min(cap, sim.max_concurrent_actions)` via a per-GM semaphore; empty = global cap everywhere; applies under any step mode |
| `sim.checkpoint.every_n_steps` | null | Checkpoint frequency (run_study.py sets 1 by default) |

`agents.persona_pipeline.classes.<class>.model` accepts a scalar model name OR a
full `{name, temperature, provider, api_base, api_key, extra_kwargs, disabled}`
block overriding `sim.llm` per-field (unset fields fall back to global;
`extra_kwargs` replaces, not deep-merges); models are deduped by effective config.

Key run params live in `world/default.yaml` (at config root via `@package _global_`):

| Parameter | Default | Notes |
|-----------|---------|-------|
| `num_agents` | 10 | Number of agents |
| `num_steps` | 5 | Simulation episodes |
| `scenario_name` | default | Used in output path |
| `seed` | 1 | Random seed |

Note: scenario `world/default.yaml` files REPLACE the base world group (Hydra
searchpath shadowing), so every scenario must re-declare the universal run
params (`num_steps`, `seed`, `output_rootname`, ...). Flat scenario
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

## 4) Defining New Agent Behaviors Cleanly

Use class-level behavior flows instead of adding custom manager branches:

1. Assign class flow:
- `persona_pipeline.classes.<class>.flow_tag`

2. Define flow order:
- `sim.engine.step.params.flow_order`

3. Optional per-entity override:
- `sim.engine.step.params.agent_to_flow`

4. Optional observe specialization for selected flows:
- `env.gm.components.observe.params.episode_observation_flows`

5. Optional per-flow turn policy (how many actions a flow takes per step):
- `sim.engine.step.params.flow_turn_policies` (map flow_tag ->
  `{built_in|class_path, params}`; unlisted flows use `sim.engine.turn_policy`).
  Only effective under `engine.step.built_in` of `flow`/`multi_gm`.
- Optional per-GM turn policy (per-backend action cadence):
  `sim.engine.step.params.gm_turn_policies` (map gm_name -> turn policy slot, same
  `{built_in|class_path, params}` shape; precedence per-flow > per-GM > global;
  resolved per batch by GM name, so it applies under ANY step mode). Lets a
  GM/backend set its own cadence — e.g. `single_action` in a "world" GM but
  `open_ended` in a social GM — and disambiguates multi-GM-chain hops that share
  one flow (which `flow_turn_policies`, being flow-keyed, cannot). Unset/empty =
  the global turn policy applies everywhere (unchanged behavior).
- Optional per-GM concurrency cap (how many of a GM's turns run AT ONCE):
  `sim.engine.step.params.gm_concurrency_caps` (map gm_name -> int) caps that GM's
  concurrent agent turns via a per-GM semaphore; effective per-GM =
  `min(cap, sim.max_concurrent_actions)` (the global stays the overall ceiling and
  per-GM default). Orthogonal to `gm_turn_policies` (turn policy = how MANY actions
  a turn takes; this = how many turns run AT ONCE). Empty = global cap everywhere;
  applies under ANY step mode. Use to throttle a rate-limited backend below the
  global limit while other GMs keep running concurrently.

6. Optional per-flow action enforcement (which actions a flow may execute):
- `env.gm.components.resolve.params.flow_action_filters` (map flow_tag ->
  `{enabled_actions, excluded_actions}`; further-restricts the backend filter,
  enforced at resolve time, never blocks `FINISHED`). Works on the default GM.

7. Optional per-flow probe/eval targeting:
- `eval.probes.deployment.include_flows` / `exclude_flows` (deploy probes only to
  agents in the named flow(s); empty = all agents).

8. Advanced multi-GM orchestration (optional):
- `env.gm_orchestration.gms` (each `gms[*]` — and the single default `env.gm` —
  accepts optional per-GM `action_mode` and `tool_calling_mode` (alias
  `tool_calling`, scalar or `{mode: ...}`) overrides; unset falls back to the
  global `sim.action_mode` / `sim.tool_calling.mode`. Resolve compatibility is
  validated PER-GM against each GM's effective mode: an effective `single`/`multi`
  GM must use `components.resolve.built_in: tool_calling`, a `none` GM must not.)
- `env.gm_orchestration.flow_bindings.flow_to_gms` (maps a flow to a GM or a
  strictly-increasing-`sequence` GM chain; the only supported flow binding key).
  A chain entry may be `null` (an **empty slot**) — meaningful only under
  `multi_gm_staged` — meaning the flow IDLES at that stage and resumes at its
  next non-null hop. Trailing empty slots are trimmed (no effect); an all-empty
  chain is rejected ("cannot be empty"); the strictly-increasing-`sequence` rule
  applies to the real (non-null) GMs only. Under `multi_gm` / `multi_gm_serial`
  null hops are simply dropped.
  A chain entry may also be a **branch node** — `{branch: {router, choices}}` —
  that routes each of the flow's agents to one of `choices` (alternative GMs) at
  that one stage. The `router` is a `{built_in|class_path, params}` slot built by
  the engine (`policies/routers.py` + `build_router`); built-in `random`
  (`RandomChoiceRouter`, weighted, deterministic per `(seed, flow, step, agent)`),
  and a `class_path` router is the "a custom function chooses" seam. The branch is
  ONE stage resolved at materialization, so the router acts FIRST (incl. under the
  staged barrier) and the GMs before/after it still run once on every agent (shared
  hops are not split per choice). v1 resolves only PURE routers; a router declaring
  `reads_live_state`/`drives_agent` is rejected (the reserved execution-time path).
  Rules: ≤1 branch/chain, ≥2 distinct known choices whose `sequence`s sit strictly
  between the branch's neighbours, `multi_gm*` mode only, not inside a `flow_order`
  flow. Restore + per-GM `owned_flows` treat a branch as any of its choices via
  `collapse_flow_chains` / `flow_chain_gm_names`.
- The multi-GM flow-chain traversal mode is selected by `sim.engine.step.built_in`
  (`multi_gm` / `multi_gm_serial` / `multi_gm_staged`); the former
  `sim.engine.step.params.chain_execution` knob has been removed (a config that
  still sets it raises a `ValueError` with a migration hint):
  - `multi_gm` (concurrent, **default** — unchanged behavior): runs flows as
    independent pipelines through their GM chains. Distinct flows advance
    concurrently and serialize only on shared-GM overlap (the per-GM lock), with
    `flow_order` flows running first as a strict serial prefix.
  - `multi_gm_serial` (legacy row-major): each flow runs its full GM chain to
    completion before the next flow starts, one batch at a time, in deterministic
    flow-order.
  - `multi_gm_staged` (column-major with a global per-stage barrier): `flow_order`
    flows run first as a serial prefix (same as concurrent); then every remaining
    flow advances ONE STAGE AT A TIME — all flows' stage-N hops run concurrently
    and stage N+1 does not begin until ALL of stage N's turns finish. Empty slots
    (`null` chain entries) let flows with different chain shapes stay stage-aligned.
    Tradeoff: the barrier can leave the worker pool idle at stage tails (a fast
    flow waits for slow flows); use `multi_gm` when you don't need stage alignment.

Default UX rule:
- Keep users on the default `ComponentGameMaster`, with advanced dashboard toggles off.
- Only expose flow tags and multi-GM controls behind advanced mode.

All per-flow keys above are additive and backward compatible: each falls back to
the global/default behavior when omitted, and the agent->flow mapping is the same
materialized `agent_flow_tags` used by scheduling and component routing.

Fixed agents (`silisocs.agents.fixed.FixedAgent`) are the reference example.

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

### Reference Implementation: FixedAgent

`silisocs.agents.fixed.FixedAgent` is a concrete example of a non-LLM agent:

```python
# src/silisocs/agents/fixed.py
class FixedAgent(Agent):
    """Deterministic agent executing pre-defined actions by episode."""
    
    def observe(self, observation: str) -> None:
        # Extract episode number from observation
        self._current_episode = extract_episode(observation)
    
    def act(self, action_spec) -> str:
        # Look up action for current episode and return as string
        action = self._next_action_item()
        return format_action(action)
```

### How Custom Agents Are Loaded

1. Create a native runtime class:
   ```python
   # my_agents.py
   from silisocs.agents.base_agent import Agent
   from silisocs.runtime.types import ActionOutput
   
   class MyCustomAgent(Agent):
       def __init__(self, *, model, name: str, context: str = ""):
           super().__init__(model)
           self._name = name
           self._context = context

       @property
       def name(self) -> str:
           return self._name

       def observe(self, observation: str) -> None:
           self._context += f"\n{observation}"

       def act(self, action_spec):
           return self._call_model(self._context, action_spec)
   ```

2. Reference in world config:
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
   plus the configured `params`.

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

To enable tool-calling at the game-master layer, configure resolve as `tool_calling`.
This keeps prompt generation mode (`custom` or `generic`) independent from parsing mode.

Example custom-cta + tool-calling:

```python
sim:
    action_mode: custom      # custom prompt text still used
    tool_calling:
        mode: single
    components:
        game_master:
            resolve:
                built_in: tool_calling
```

When tool-calling is active, the native `ActionSpec` carries tool schemas in
`extra_args`; `Agent._call_model()` routes the request to `sample_tool_calls`.

### Adding a Custom LLM Provider

Common providers that expose an OpenAI-compatible API ship as built-in presets
(`anthropic`, `gemini`, `openrouter`, `groq`, `together`, `deepseek`, `mistral`,
`fireworks`, `xai`, `ollama`); set `sim.llm.provider` to the name and supply the
key via the provider's env var (see `OPENAI_COMPATIBLE_PRESETS` in
`runtime/language_models/factory.py`). For anything else, two paths exist, with no
core edits required:

1. Register a provider name (import the module before the run starts):
   ```python
   from silisocs.runtime.language_models.registry import register_llm_provider

   @register_llm_provider("my_provider")
   class MyModel(LanguageModel): ...
   ```
   then set `sim.llm.provider: my_provider`.
2. Or set `sim.llm.provider: mypkg.models.MyModel` (fully qualified class path).

Providers that speak an OpenAI-compatible HTTP API should subclass
`OpenAICompatibleLanguageModel` to inherit retry/backoff and telemetry support.

### Validation & Error Handling

Game master initialization (`src/silisocs/environments/gm/game_master.py`) validates agents:

```python
# Checks at GM build time:
for entity in self.entities:
    assert hasattr(entity, 'name'), f"{entity} missing 'name' attribute"
    assert hasattr(entity, 'observe'), f"{entity} missing 'observe' method"
    assert hasattr(entity, 'act'), f"{entity} missing 'act' method"
```

Runner validates direct class construction, so **missing methods fail fast**.

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

## 5.5) Action Modes and Platform Configuration

The platform supports different action modes configured via `sim.action_mode`:

```yaml
sim:
    action_mode: custom  # custom | generic
    tool_calling:
        mode: none         # none | single | multi
```

Each mode corresponds to how the agent's responses are interpreted and executed:

- **custom**: Custom parsing format determined by the world
- **generic**: Generic action name + parameters format

Tool-calling is configured separately via `sim.tool_calling.mode`.

The specific action format and response interpretation is determined by the resolve component and world configuration, not by the agent. Agents simply return strings; the platform interprets them according to the active mode.

For **tool-calling mode** specifically: The entity layer is responsible for calling `sample_tool_call()` when the action_spec indicates tool-calling is needed. The resolve component then processes the result. This architecture keeps tool-calling logic in the entity/act layer, not in resolve.

## 6) Checkpoints and Replay

- Checkpoints are saved as JSON under run output `checkpoints/step_{N}_checkpoint.json`.
- Runtime resume uses:
- `sim.checkpoint.source_run` (explicit prior output directory)
- `sim.checkpoint.auto_resume` (default `true`): when `source_run` is unset, resume
  from this run's own output directory if it already contains checkpoints; a fresh
  output directory still starts from scratch. Set `false` to force a fresh start.
- `sim.checkpoint.restore`
- Resume restores game-master and entity component state plus raw log.

**Backend restore contract** (backend authors): a backend supports either (or both)
of two restore paths. (1) Authoritative snapshot — set the class flag
`provides_checkpoint_state = True` and make get_state/set_state round-trip the full
state; restore applies it directly. (2) Action-event replay — this is a *mechanism*
that lives entirely in the pluggable `sim.checkpoint.restore` strategy, not on the
backend. The built-in `social_action_event_replay` strategy owns its per-backend
event→action mappings in a registry keyed by `backend_type`
(`runtime/checkpointing/replay_mappers.py`): a backend "supports replay" exactly
when a mapper is registered for its `backend_type`. Backends themselves carry **no**
replay method — they implement only `get_state`/`set_state`. The shipped registry
maps `twitter_like` to the stateless `microblog_event_to_replay_action`; `reddit_like`
has none (no valid microblog mapping) and self-restores via snapshot. A custom
backend opts into the built-in strategy with
`register_replay_mapper(backend_type, mapper)` (mirrors `register_llm_provider`) — no
core edit, no backend method; a bespoke restore that a stateless mapper can't express
is a custom `sim.checkpoint.restore.class_path` strategy instead. Every shipped
backend self-restores via `set_state` (`provides_checkpoint_state=True`); Mastodon —
which can't snapshot its live server — does so by embedding its action history in
`get_state` and replaying it (with old→new toot-id remapping) in `set_state` through
its own private mapper, so it is not driven by the replay strategy at all. The
authoritative-vs-replay decision is **per game master**: snapshot GMs restore from
their block, the rest go to the strategy (which requires a registered mapper).
Multi-GM runs isolate each GM's backend db + `action_events.jsonl` under
`<output>/<gm_name>/`; both restore and eval discover these via
`silisocs.evaluations.action_events.resolve_action_event_files` (restore passes
all per-GM logs to the strategy as `action_events_files`). A GM may override the
global `sim.checkpoint.restore` with its own strategy via
`env.gm_orchestration.gms[*].restore` (same schema; absent → global default).

**Saving policy**: checkpoint saving is disabled by default when running directly via `run_experiment.py` unless `every_n_steps` or `explicit_steps` is configured. When running via `run_study.py`, checkpointing is enabled automatically (`every_n_steps=1`) so that `eval.py` can access the final checkpoint for action-type metrics. Studies can change the frequency via `run_defaults.overrides: {sim.checkpoint.every_n_steps: N}`.

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

Fast contributor workflow (LLM-agent friendly):

1. `uv sync --group dev`
2. Run targeted tests for changed files first (`uv run pytest <targeted_tests>`)
3. Run full quality gate: `uv run pre-commit run --all-files --verbose`
4. Run coverage workflow: `uv run --group dev poe test`
5. Commit with Conventional Commits (`uv run cz c` or `git commit -m "feat: ..."`)
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

This guide (AGENTS.md) is for you if you're **extending the framework** — writing new components, backends, agents, or changing architecture.

If instead you want to **design and run experiments via config only**:
→ See [agent_docs/scenario_design.md](agent_docs/scenario_design.md) — Scenario design guide for config-based users

**Detailed architecture deep dive** (multi-flow, multi-GM, component routing):
→ See [agent_docs/architecture.md](agent_docs/architecture.md) — Reference for complex orchestration patterns

**Guided workflows** (interactive design workflows — readable by any coding agent):
→ [agent_docs/skills/new-scenario.md](agent_docs/skills/new-scenario.md) — Step-by-step scenario design assistant
→ [agent_docs/skills/new-study.md](agent_docs/skills/new-study.md) — Step-by-step study design assistant

**Public documentation** (for end users):
- `docs/index.md` — Hub for all documentation
- `docs/configuration.md` — Config reference (all knobs explained)
- `docs/usage.md` — End-to-end workflow
- `docs/study_schema.md` — Study YAML schema, directory layout, and analysis conventions
- `docs/environment_layer.md` — Engine/GM/component extensibility patterns
- `docs/backends.md` — Backend plugin patterns
- `docs/building_agents.md` — Agent builder patterns
- `docs/dashboard.md` — GUI usage
- `docs/contributing.md` — Code standards

When adding features, update docs in:

- Config schema and fields (docs/configuration.md)
- Runtime behavior and extension guidance (this file + docs/environment_layer.md)
- User-facing usage examples (docs/usage.md)
- Dashboard behavior (docs/dashboard.md if applicable)

## 10) Common Pitfalls

- Adding GM/engine bloat instead of using flow routing + component hooks
- Breaking the action text format consumed by resolve
- Forgetting to keep docs aligned with runtime defaults
- Assuming dashboard run snapshot loading equals checkpoint state replay
- Relying on non-uv environment when reproducing tests
- Not understanding config composition (Hydra merges scenario-local overrides with base defaults)

## 11) PR Readiness Checklist

- Code compiles and tests pass in uv environment
- Lint/pre-commit workflow passes
- New behavior has tests
- Docs updated for config + usage + architecture
- Commit message uses gitmoji prefix (see §14)

## 11.5) Branching Rules

- **Never commit code changes directly to `main`.** All code changes must go through a `dev` branch (or feature branch) and be merged via PR.
- **Documentation-only changes** (edits to `docs/`, `agent_docs/`, `AGENTS.md`, `README.md`) may be committed directly to `main`.
- When starting new work, create a branch: `git checkout -b dev` (or a descriptive feature branch name).

## 12) Entry Points for Quick Exploration

Start from these files to understand the flow:

1. **Config composition**: `src/silisocs/runtime/runner.py` — How Hydra merges configs
2. **Simulation orchestration**: `src/silisocs/runtime/simulation.py` — Full workflow
3. **Engine execution**: `src/silisocs/simulation_engines/base_engines.py` — Episode loop
4. **Game master**: `src/silisocs/environments/gm/game_master.py` — Simple preset
5. **Multi-flow GM**: `src/silisocs/environments/gm/game_master.py` — Advanced component routing
6. **Component slots**: `src/silisocs/environments/gm/components/` — Pluggable behavior
7. **Backend actions**: `src/silisocs/environments/backends/twitter_like/app.py` — Example backend

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
- Commit messages must use gitmoji prefixes (repo uses `cz_gitmoji` schema):
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
