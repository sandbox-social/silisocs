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
- `src/mastodon_sim/environments/gm/act.py`
- `src/mastodon_sim/environments/gm/components/`
- Component-slotted architecture for next-acting, observe, resolve, initializer

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
- Checkpoint controls:
  - `checkpoint.every_n_steps`
  - `checkpoint.explicit_steps`
  - `checkpoint.resume_file`
  - `checkpoint.resume_step`

Scenario content lives under:

- `scenarios/<name>/conf/scenario/<name>.yaml`

## 4) Defining New Agent Behaviors Cleanly

Use behavior flows instead of adding custom manager branches:

1. Assign class flow:
- `persona_pipeline.classes.<class>.params.action_flow`

2. Define flow order:
- `sim.engine.flow_routing.flow_order`

3. Optional per-entity override:
- `sim.engine.flow_routing.entity_to_flow`

4. Optional observe specialization for selected flows:
- `sim.gm.components.observe.params.episode_observation_flows`

Fixed agents (`mastodon_sim.agents.fixed_entity`) are the reference example.

## 5) Enabling New Agent Runtime Code

Today, runtime entities are expected to be Concordia-compatible.

Minimum practical runtime interface:

- `name` attribute
- `observe(observation: str) -> None`
- `act(action_spec) -> str`

Recommended extras for observability and compatibility:

- `get_last_log() -> dict` (if you want per-agent structured logs)
- `set_state(...)` / `get_state(...)` via Concordia components for checkpoint resume
- `set_allowed_action_types(...)` if action vocabulary constraints are needed

Prefab-level requirement:

- Expose `Entity(prefab_lib.Prefab)` with `build(model, memory_bank)` returning the runtime object.

How custom code is loaded:

- Via prefab module path in scenario class config (`prefab_module`)
- Resolved in runner via `get_prefab_instance(...)` for non-core prefabs

## 6) Checkpoints and Replay

- Checkpoints are saved as JSON under run output `checkpoints/`.
- Runtime resume path uses:
  - `sim.checkpoint.resume_file`
  - optional `sim.checkpoint.resume_step`
- Resume restores game-master and entity component state plus raw log.

Important: checkpoint saving is disabled unless `every_n_steps` or `explicit_steps` is configured.

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
