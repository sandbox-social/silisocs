# AGENTS.md

This file is a contributor guide for LLM coding agents working in this repository.

## 1) What This Repository Is

Mastodon-Sim is a Concordia-based social simulation framework with:

- YAML-first scenario and runtime configuration (Hydra + OmegaConf)
- A social-media game-master/environment layer
- Multiple platform backends (Twitter-like, Reddit-like, Mastodon)
- Declarative persona pipeline plus custom builder extension path
- Probe-based evaluation and rich runtime telemetry
- Streamlit dashboard for scenario creation and launch

The runtime entrypoint is:

- `src/mastodon_sim/runtime/runner.py`

## 2) High-Level Architecture

Core runtime layers:

### 1. Agent Construction Layer
- `src/mastodon_sim/agents/builders.py`
- Builds agents from `scenario.persona_pipeline` and class data sources
- Supports fixed-action set loading and template rendering
- Entry point: `EntityBuilder.build_agents(cfg, model)`

### 2. Prefab/Entity Layer
- `src/mastodon_sim/agents/base_agent.py` — Abstract Agent interface
- `src/mastodon_sim/agents/entity.py` — LLM-based agent (Concordia-compatible)
- `src/mastodon_sim/agents/fixed_entity.py` — Deterministic agent (pre-scripted actions)
- Custom agents must implement: `name`, `observe(str)`, `act(ActionSpec) → str`
- To add custom agent: create prefab module with `Entity(Prefab)` class, reference in scenario

### 3. Game Master Layer (Component-Slotted Architecture)
- `src/mastodon_sim/environments/gm/base_game_master.py` — Base coordinator
- `src/mastodon_sim/environments/gm/game_master.py` — Simple preset (single components)
- `src/mastodon_sim/environments/gm/shared_flow_game_master.py` — Multi-flow preset (multi-instance routing)
- `src/mastodon_sim/environments/gm/act.py` — SMAct (simple) & MultiFlowSMAct (routing logic)
- `src/mastodon_sim/environments/gm/components/` — Pluggable components:
  - `next_acting.py` — Determine which agent acts next
  - `observe.py` — Generate timeline/episode observations
  - `resolve.py` — Parse agent output into backend actions
  - `initializer.py` — Initialize agents with memories
  - `recommend.py` — Schedule recommendation algorithm updates
  - `seed_post_provider.py` — Generate seed posts (LLM/CSV/JSON)
- To add custom component: implement `Component` interface, set in `sim.gm.components.{role}.class_path`

### 4. Engine Layer (Execution Policies)
- `src/mastodon_sim/environments/engines/social_media.py` — BaseSocialMediaEngine
- `src/mastodon_sim/environments/engines/social_media.py` — FlowSocialMediaEngine (multi-flow scheduling)
- `src/mastodon_sim/environments/engines/multi_gm_social_media.py` — MultiGMSocialMediaEngine (multi-GM orchestration)
- `src/mastodon_sim/environments/engines/policies/` — Action loop & probe schedule policies:
  - Action loop: `single_action`, `fixed_count`, `open_ended`
  - Probe schedule: `step_schedule`, `fixed_interval`, `disabled`
- To add custom policy: create class inheriting from `ActionLoopPolicy` or `ProbeSchedulePolicy`, reference via `class_path`

### 5. Backend Action Layer
- `src/mastodon_sim/environments/backends/base.py` — ActionCatalog, base app interface
- `src/mastodon_sim/environments/backends/twitter_like/` — TwitterLikeApp with SQL backend
- `src/mastodon_sim/environments/backends/reddit_like/` — RedditLikeApp
- `src/mastodon_sim/environments/backends/mastodon/` — Real Mastodon server integration
- Actions discovered via `@app_action(name=..., description=...)` decorator
- To add custom backend: subclass `SocialMediaApp`, implement action methods, register in app factory

### 6. Runtime Orchestration
- `src/mastodon_sim/runtime/runner.py` — CLI entrypoint, Hydra config composition
- `src/mastodon_sim/runtime/simulation.py` — SimulationRunner orchestrates full workflow
- `src/mastodon_sim/runtime/config.py` — Config validation and initialization
- Handles: model creation, agent building, memory initialization, simulation execution, checkpoint save/resume

## 3) Configuration Model

Top-level config composition:

- `src/mastodon_sim/conf/config.yaml` — Hydra root config
- Defaults: `sim: base`, `social_media: twitter_like`, `scenario: default`

Main simulation knobs (`src/mastodon_sim/conf/sim/base.yaml`):

| Parameter | Value | Notes |
|-----------|-------|-------|
| `llm_name` | gpt-4o-mini | Default LLM model |
| `num_agents` | 100 | Number of agents |
| `num_steps` | 50 | Simulation episodes |
| `action_mode` | custom | Prompt style (`custom` or `generic`) |
| `tool_calling.mode` | single | Tool-calling behavior (`none` \| `single` \| `multi`) |
| `enable_gm_multi_flow` | false | Multi-component-instance routing |
| `enable_engine_multi_flow` | false | Flow-phase scheduling |
| `gm.preset` | base | Simple (single components) or shared_flow (multi-instance) |
| `engine.preset` | base | Simple scheduling or flow-aware |
| `engine.action_loop.built_in` | single_action | single_action \| fixed_count \| open_ended |
| `timeline_mode` | hybrid_recsys_follower | Timeline assembly mode |
| `seed_posts.type` | llm | llm \| csv \| json \| none \| fallback |

`timeline_mode` is the timeline selector.

`enable_gm_multi_flow` and `enable_engine_multi_flow` are independent switches.
The first controls GM component routing (`gm.preset: shared_flow`), while the
second controls engine flow scheduling/policies (`engine.preset: flow`).

Scenario content lives under:

- `scenarios/<name>/conf/scenario/<name>.yaml` (@package scenario)
- **Optional**: `scenarios/<name>/conf/sim.yaml` (@package sim) — scenario-specific overrides
- **Optional**: `scenarios/<name>/conf/social_media.yaml` (@package social_media) — platform choice

**For designing experiments via config (no code changes):** See [EXPERIMENTS.md](EXPERIMENTS.md)

**For understanding config structure deeply:** See [docs/configuration.md](docs/configuration.md)

## 4) Defining New Agent Behaviors Cleanly

Use class-level behavior flows instead of adding custom manager branches:

1. Assign class flow:
- `persona_pipeline.classes.<class>.flow_tag`

2. Define flow order:
- `sim.engine.flow_routing.flow_order`

3. Optional per-entity override:
- `sim.engine.flow_routing.entity_to_flow`

4. Optional observe specialization for selected flows:
- `sim.gm.components.observe.params.episode_observation_flow`

5. Advanced multi-GM orchestration (optional):
- `sim.gm_orchestration.gms`
- `sim.gm_orchestration.flow_bindings.flow_to_gm`
- `sim.gm_orchestration.flow_bindings.flow_to_gms`
- `sim.gm_orchestration.flow_bindings.gm_to_flows`

Default UX rule:
- Keep users on simple mode (`gm.preset=simple`, advanced dashboard toggle off).
- Only expose flow tags and multi-GM controls behind advanced mode.

Fixed agents (`mastodon_sim.agents.fixed_entity`) are the reference example.

## 5) Agent Interface: Concordia vs Custom

All agents in mastodon-sim implement a common interface defined by `mastodon_sim.agents.base_agent.Agent` (ABC).

### Minimum Required Interface

Every agent (whether Concordia-based or custom) must implement:

```python
from mastodon_sim.agents.base_agent import Agent

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

### Reference Implementation: FixedActionEntity

The `mastodon_sim.agents.fixed_entity.FixedActionEntityRuntime` is a concrete example of a non-LLM agent:

```python
# src/mastodon_sim/agents/fixed_entity.py
class FixedActionEntityRuntime(Agent):
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

1. Create a module with an `Entity` class (Prefab wrapper):
   ```python
   # my_agents.py
   import dataclasses
   from concordia.typing import prefab as prefab_lib
   
   @dataclasses.dataclass
   class Entity(prefab_lib.Prefab):
       description: str = "My custom agent"
       params: dict = dataclasses.field(default_factory=dict)
       
       def build(self, model, memory_bank):
           return MyCustomAgentRuntime(params=self.params)
   ```

2. Reference in scenario config:
   ```yaml
   persona_pipeline:
     prefab_module: my_agents
     classes:
       Alice:
         role: influencer
         prefab: Entity  # Uses my_agents.Entity
   ```

3. Runner loads it via `get_prefab_instance(prefab_module, class_name)`.

### Concordia Integration Points

If building a **Concordia-compatible** agent (using EntityAgentWithLogging):

- Agents are context components (observe/act participate in component orchestration)
- Extend `EntityAgentWithLogging` to get logging + checkpoint support automatically
- Return from prefab's `build()` method

If building a **custom (non-Concordia)** agent:

- Implement only the `Agent` interface
- Concordia integration still works (engine calls `agent.observe()` and `agent.act()`)
- Checkpoint support optional (implement `get_state()`/`set_state()` if needed)

No special ABC requirement for Concordia agents—they naturally implement the interface via activity slots.

### Tool-Calling Implementation for Entities

When **tool-calling is enabled** (`tool_calling.mode: single|multi`), the platform uses backend
actions as tools and the language model selects which action(s) to invoke.

**Architecture for tool-calling:**

1. **Detect tool-calling mode**: Game master sets the action_spec with a `### TOOL_CALLING_MODE ###` marker
2. **Entity act layer**: Uses `SocialConcatActComponent` to detect this marker
3. **Tool selection**: Calls model's `sample_tool_call()` with available backend action schemas
4. **Format result**: Returns structured tool-call result as JSON
5. **Resolve execution**: ToolCallingResolveComponent parses result and executes the selected tool

### Enabling Tool-Calling

The base `Entity` prefab now uses `SocialConcatActComponent` by default, so tool-calling
is enabled automatically when the game-master action spec includes tool markers.

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

When tool-calling is active AND the action_spec contains the marker,
`SocialConcatActComponent.get_action_attempt()` handles tool selection.
Otherwise, normal Concordia act proceeds.

### Validation & Error Handling

Game master initialization (`src/mastodon_sim/environments/gm/game_master.py`) validates agents:

```python
# Checks at GM build time:
for entity in self.entities:
    assert hasattr(entity, 'name'), f"{entity} missing 'name' attribute"
    assert hasattr(entity, 'observe'), f"{entity} missing 'observe' method"
    assert hasattr(entity, 'act'), f"{entity} missing 'act' method"
```

Runner also validates during prefab construction, so **missing methods will fail fast**.

### Multi-Action Support (Open-Ended Policy)

When using `engine.action_loop.built_in: open_ended`:

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

- **custom**: Custom parsing format determined by the scenario
- **generic**: Generic action name + parameters format

Tool-calling is configured separately via `sim.tool_calling.mode`.

The specific action format and response interpretation is determined by the resolve component and scenario configuration, not by the agent. Agents simply return strings; the platform interprets them according to the active mode.

For **tool-calling mode** specifically: The entity layer is responsible for calling `sample_tool_call()` when the action_spec indicates tool-calling is needed. The resolve component then processes the result. This architecture keeps tool-calling logic in the entity/act layer, not in resolve.

## 6) Checkpoints and Replay

- Checkpoints are saved as JSON under run output `checkpoints/`.
- Runtime resume path uses:
  - `sim.checkpoint.resume_file`
  - optional `sim.checkpoint.resume_step`
- Resume restores game-master and entity component state plus raw log.

Important: checkpoint saving is disabled unless `every_n_steps` or `explicit_steps` is configured.

**For custom agents**: By default, only Concordia `EntityWithComponents` entities are checkpointed.
If your custom agent has episodic state that needs saving, implement `get_state()` and `set_state()`
methods. The simulation will call these after checking `isinstance(entity, EntityWithComponents)`.

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
- Lint workflow: `uv run poe lint`
- Test workflow: `uv run poe test`
- Pre-commit hooks all files: `uv run pre-commit run --all-files --verbose`
- Commit with Commitizen: `uv run cz c`

Fast contributor workflow (LLM-agent friendly):

1. `uv sync --group dev`
2. Run targeted tests for changed files first (`uv run pytest <targeted_tests>`)
3. Run full quality gate: `uv run pre-commit run --all-files --verbose`
4. Run coverage workflow: `uv run poe test`
5. Commit with Conventional Commits (`uv run cz c` or `git commit -m "feat: ..."`)
6. Push branch (`git push origin <branch>`)

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
→ See [EXPERIMENTS.md](EXPERIMENTS.md) — Scenario design guide for config-based users

**Detailed architecture deep dive** (multi-flow, multi-GM, component routing):
→ See [ARCHITECTURE.md](ARCHITECTURE.md) — Reference for complex orchestration patterns

**Public documentation** (for end users):
- `docs/index.md` — Hub for all documentation
- `docs/configuration.md` — Config reference (all knobs explained)
- `docs/usage.md` — End-to-end workflow
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
- Not understanding fallback config behavior (Hydra merges scenario overrides with base defaults)

## 11) PR Readiness Checklist

- Code compiles and tests pass in uv environment
- Lint/pre-commit workflow passes
- New behavior has tests
- Docs updated for config + usage + architecture
- Commit message follows Conventional Commits (Commitizen workflow)

## 12) Entry Points for Quick Exploration

Start from these files to understand the flow:

1. **Config composition**: `src/mastodon_sim/runtime/runner.py` — How Hydra merges configs
2. **Simulation orchestration**: `src/mastodon_sim/runtime/simulation.py` — Full workflow
3. **Engine execution**: `src/mastodon_sim/environments/engines/social_media.py` — Episode loop
4. **Game master**: `src/mastodon_sim/environments/gm/game_master.py` — Simple preset
5. **Multi-flow GM**: `src/mastodon_sim/environments/gm/shared_flow_game_master.py` — Advanced preset
6. **Component slots**: `src/mastodon_sim/environments/gm/components/` — Pluggable behavior
7. **Backend actions**: `src/mastodon_sim/environments/backends/twitter_like/app.py` — Example backend


- `src/mastodon_sim/agents/builders.py`
- `src/mastodon_sim/environments/backends/base.py`
