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

1. Agent construction layer
- `src/mastodon_sim/agents/builders.py`
- Builds agents from `scenario.persona_pipeline` and class data sources
- Supports fixed-action set loading and template rendering

2. Prefab/entity layer
- `src/mastodon_sim/agents/entity.py`
- `src/mastodon_sim/agents/fixed_entity.py`
- Prefabs must build runtime objects with Concordia-compatible entity behavior

3. Game Master layer
- `src/mastodon_sim/environments/gm/game_master.py`
- `src/mastodon_sim/environments/gm/base_game_master.py`
- `src/mastodon_sim/environments/gm/shared_flow_game_master.py`
- `src/mastodon_sim/environments/gm/act.py`
- `src/mastodon_sim/environments/gm/components/`
- Component-slotted architecture for next-acting, observe, resolve, initializer
- `game_master.py` is the simple default preset; `shared_flow_game_master.py`
    is the advanced shared-flow preset; both use `base_game_master.py`

4. Engine layer
- `src/mastodon_sim/environments/engines/social_media.py`
- Policy hooks in `src/mastodon_sim/environments/engines/policies/`
- Executes episodes, probe scheduling, concurrency throttling, flow routing

5. Backend action layer
- `src/mastodon_sim/environments/backends/base.py`
- Platform-specific apps under `src/mastodon_sim/environments/backends/`
- Actions discovered via `@app_action(...)`

6. Runtime orchestration
- `src/mastodon_sim/runtime/runner.py`
- `src/mastodon_sim/runtime/simulation.py`
- Model creation, config validation, simulation construction/play, checkpoints

## 3) Configuration Model

Top-level config composition:

- `src/mastodon_sim/conf/config.yaml`
- Defaults: `sim`, `social_media`, `scenario`

Main runtime knobs:

- `src/mastodon_sim/conf/sim/base.yaml`
- Includes GM slots, engine policies, action mode, enabled action filtering
- `gm.preset` should stay on `simple` unless advanced orchestration is needed
- advanced orchestration is configured under `sim.gm_orchestration`
- Checkpoint controls:
  - `checkpoint.every_n_steps`
  - `checkpoint.explicit_steps`
  - `checkpoint.resume_file`
  - `checkpoint.resume_step`

Scenario content lives under:

- `scenarios/<name>/conf/scenario/<name>.yaml`

## 4) Defining New Agent Behaviors Cleanly

Use class-level behavior flows instead of adding custom manager branches:

1. Assign class flow:
- `persona_pipeline.classes.<class>.flow_tag`
- Backward compatibility still accepts `params.action_flow`.

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

In **tool-calling mode** (`action_mode: tool_calling`), the platform uses backend actions as tools
and the language model selects which action to invoke.

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

When using `engine.action_chunk_policy: open_ended`:

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
  action_mode: custom  # custom | generic | tool_calling
```

Each mode corresponds to how the agent's responses are interpreted and executed:

- **custom**: Custom parsing format determined by the scenario
- **generic**: Generic action name + parameters format
- **tool_calling**: Direct tool invocation via language model

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

## 8) Testing Expectations for Agents

When changing runtime behavior:

1. Run targeted tests for touched modules first.
2. Run full suite before finalizing if feasible.
3. Add tests for new config/behavior paths.
4. Avoid deleting tests unless they are obsolete due to architecture removal.

Useful tests in this repo include action parsing, worker limits, probe deployment,
backend action catalogs, and checkpoint policy tests.

## 9) Documentation Map

Primary docs directory:

- `docs/index.md` (hub)
- `docs/configuration.md`
- `docs/usage.md`
- `docs/environment_layer.md`
- `docs/backends.md`
- `docs/building_agents.md`
- `docs/dashboard.md`
- `docs/contributing.md`

When adding features, update docs in all relevant layers:

- Config schema and fields
- Runtime behavior and extension guidance
- User-facing usage examples
- Dashboard behavior (if applicable)

## 10) Common Pitfalls

- Adding GM/engine bloat instead of using flow routing + component hooks
- Breaking backward compatibility of action text format consumed by resolve
- Forgetting to keep docs aligned with runtime defaults
- Assuming dashboard run snapshot loading equals checkpoint state replay
- Relying on non-uv environment when reproducing tests

## 11) PR Readiness Checklist

- Code compiles and tests pass in uv environment
- Lint/pre-commit workflow passes
- New behavior has tests
- Docs updated for config + usage + architecture
- Commit message follows Conventional Commits (Commitizen workflow)

## 12) If You Need to Explore Quickly

Start from these files first:

- `src/mastodon_sim/runtime/runner.py`
- `src/mastodon_sim/runtime/simulation.py`
- `src/mastodon_sim/environments/engines/social_media.py`
- `src/mastodon_sim/environments/gm/game_master.py`
- `src/mastodon_sim/agents/builders.py`
- `src/mastodon_sim/environments/backends/base.py`
