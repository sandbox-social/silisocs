# Backend Boundary Refactor Log

This document is the running implementation log for separating generic
environment backend behavior from social-media-specific game-master behavior.

## Target Architecture

- The core backend contract is `BackendApp`: initialize state, provide a
  generic observation string, and expose `@app_action` methods.
- Social media remains supported through `SocialMediaApp`, timeline observation,
  parsed social action resolution, seed-post initialization, and recommendation
  components.
- Non-social environments can use generic observation and tool/generic action
  resolution without implementing timelines, feeds, posts, follows, or recsys.
- `resource_market` is the first non-social sample backend and is intentionally
  in-memory for fast non-LLM tests.

## Change Log

### Checkpoint

- Created commit `c611900 chore: checkpoint before backend boundary refactor`
  with `--no-verify` before starting this refactor, as requested.

### Generic Backend Contract

- Added `BackendApp` in `src/silisocs/environments/backends/base.py`.
- Kept `SocialMediaApp` as a social-specific subclass for existing social
  backends and tests.
- Added `create_environment_app` in the backend factory and retained
  `create_social_media_app` as a compatibility wrapper.
- Added generic `EnvironmentParams` / `EnvironmentRuntimeData` names while
  keeping social-media dataclass compatibility.

### Generic Components

- Added `AppObservationComponent`, which calls `env_app.observe(...)` instead
  of assuming timeline/feed methods.
- Added `NoOpComponent` for optional disabled component slots such as
  recommendation scheduling in non-social environments.
- Added `GameMaster`, `EnvironmentAct`, and multi-flow generic
  aliases while preserving existing social class names.

### Resource Market Backend

- Added `ResourceMarketApp`, an in-memory non-social environment backend with
  cash, inventory, open listings, and recent events.
- Added actions: `INSPECT_MARKET`, `PRODUCE_RESOURCE`, `LIST_RESOURCE`,
  `BUY_LISTING`, `CONSUME_RESOURCE`, and inherited `FINISHED`.
- Added `env/resource_market.yaml` using generic observation and disabled
  recommendation scheduling.

### Tests

- Added `tests/test_environment_app_contracts.py` for the generic app contract
  and generic observation component.
- Added `tests/test_resource_market_backend.py` for market initialization,
  listing/purchase flow, invalid actions, consumption, and `FINISHED`.
- Added `tests/test_generic_game_master_build.py` proving the generic GM can
  build without `seed_posts` or `social_network`.
- Verified new targeted tests: `7 passed`.
- Verified social regression subset covering action catalogs, prompt/tool
  calling, timeline observation, recsys selection, and shared-flow GM build:
  `50 passed, 2 skipped`.
- Verified non-LLM runtime smoke:
  `uv run python -m silisocs.runtime.runner scenario=resource_market agents=resource_market env=resource_market sim.llm.disabled=true num_agents=2 num_steps=1 ...`
  completed successfully with one episode and output under `/tmp`.
- Added `scenario/resource_market.yaml` and `agents/resource_market.yaml` so
  the sample backend has a non-social scenario and persona set.
- Verified broad non-LLM suite with LLM tests deselected by request:
  `uv run pytest tests -q -k 'not llm'` => `226 passed, 2 skipped, 9 deselected`.
- Verified `git diff --check` reports no whitespace errors in the current diff.
