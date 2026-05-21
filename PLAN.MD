# Final Native Core Cleanup Plan

## Locked Decisions
- Normal runtime is purely native: default agents, engine, GMs, backends, probes,
  and configs do not import Concordia or local Concordia-like copies.
- Concordia support is explicit opt-in via `compat: concordia` and lives behind
  `silisocs.adapters.concordia`.
- Concordia remains an optional extra; adapter code fails loudly with an install
  hint when `gdm-concordia` is unavailable.
- Shipped result-sensitive Concordia-style scenario agents keep stable module
  paths, but their configs must opt into compatibility.
- Default Silisocs agents are plain native agents. They do not use
  `ComponentEntity`, `SocialConcatActComponent`, `ConcatActComponent`, or
  `InteractiveDocument`.
- Native `ActionSpec` uses `prompt`; `call_to_action` is removed from the native
  interface.
- Native GM slots may still be called components, but they are plain Silisocs
  runtime helpers, not Concordia lifecycle objects.
- Probe fallback is allowed only as native batch-to-one-at-a-time probing.
- Per-agent runtime task isolation stays; configuration and interface errors
  fail loudly.

## Work Queue
- [x] Replace default agent construction with `NativeAgent`.
- [x] Remove native exports/imports of local Concordia-like component/document
  copies.
- [x] Move Concordia-style agent/component compatibility into
  `silisocs.adapters.concordia`.
- [x] Require `compat: concordia` for component-style shipped legacy prefabs.
- [x] Remove `GameMasterAdapter` from normal engine path.
- [x] Remove native `ActionSpec.call_to_action`.
- [x] Make probe fallback native and remove phase mutation recovery.
- [x] Update `CONTEXT.md`, add ADR, and refresh user docs.
- [x] Add/update tests for native agent, adapter boundary, config opt-in, and
  probe fallback.
- [x] Run focused tests.
- [x] Run full tests and lint.

## Running Notes
- Created during the final native core cleanup pass.
- Default native construction now points directly at
  `silisocs.agents.native.NativeAgent`.
- Local component/document copies are removed from the native runtime surface.
- `silisocs.adapters.concordia` owns Concordia imports and legacy adapters.
- Runtime assembly wraps non-native prefabs only when `compat: concordia` is
  explicitly configured.
- Focused migration suite passed:
  `uv run pytest -q tests/test_native_runtime_interfaces.py tests/test_tool_calling_smoke.py tests/test_probe_questionnaire.py tests/test_environment_app_contracts.py tests/test_recsys_flow_selection.py tests/test_concordia_import_boundary.py`.
- Full validation passed:
  `uv run pytest -q` and `uv run poe lint`.

## Final Release Cleanup Decisions
- Native runtime has no `silisocs.core` package. Contracts move to
  `silisocs.runtime.types` and `silisocs.runtime.language_models`.
- Native construction uses direct `class_path` plus params. `Prefab`,
  `prefab_module`, `prefab`, and `Role.ENTITY` are removed from native code.
- Native agents are `silisocs.agents.native.NativeAgent`; fixed native agents
  are `silisocs.agents.fixed.FixedAgent`.
- Native agents own memories/observations directly. Concordia-shaped memory
  banks are compat-only and come from upstream `gdm-concordia`.
- Concordia-style legacy modules import upstream `concordia.*` directly.
  `silisocs.adapters.concordia` only holds Silisocs-specific adapters and
  extensions.
- Engine startup takes typed native initializer context only; no initializer
  memory-bank argument.
- Checkpoints use a new native class-path/state schema and reject old
  prefab-shaped checkpoints.
- GM components remain named components, but all Concordia lifecycle language
  is removed from native docs/code.
- The top-level `legacy/` directory remains outside the supported runtime
  surface for now.

## Final Release Cleanup Work Queue
- [x] Move native contracts from `silisocs.core` into runtime modules and
  delete `silisocs.core`.
- [x] Rename native agent modules and convert configs to direct `class_path`.
- [x] Replace prefab-based assembly with direct native class instantiation and
  explicit Concordia-prefab compat construction.
- [x] Delete native memory-bank plumbing and remove `sim.memory_backend` /
  `sim.sentence_encoder`.
- [x] Shrink Concordia adapter and update legacy modules to upstream imports.
- [x] Remove legacy engine hooks and initializer memory-bank args.
- [x] Replace checkpoint schema and hard-error old prefab checkpoints.
- [x] Remove legacy probe aliases and update probe tests/docs.
- [x] Sweep docs/dashboard/scenario generator/tests for deleted APIs.
- [x] Run focused suites, full pytest, lint, and docs build if practical.

## Final Release Cleanup Execution Update
- Deleted the native `silisocs.core` package and moved remaining native
  contracts/utilities into `silisocs.runtime.*`.
- Deleted native Concordia-style copies and tombstones: agent component copies,
  old entity/fixed-entity modules, old agent initialization package,
  GM `act.py`, runtime `simulation.py`, and native memory-bank utilities.
- Native assembly now imports direct `class_path` runtime classes. Concordia
  construction only runs through explicit `compat: concordia`.
- Legacy result-sensitive Concordia scenario agents now import real upstream
  `concordia.*` surfaces directly and stay outside the native import boundary.
- Final residue scan leaves only intentional hard-error strings for removed
  config keys and explicit legacy Concordia modules.
- Final validation:
  `uv run pytest -q` -> 257 passed, 2 skipped, 7 deselected.
  `uv run poe lint` -> passed.
  `uv run mkdocs build --strict` -> passed with only the upstream MkDocs 2.0
  advisory warning.

## Native Initialization Boundary Decisions
- The Engine owns startup explicitly. Startup order is always agents, then
  game masters, then simulation.
- `sim.initializer` is removed. Config uses `sim.initialization.agents`,
  `sim.initialization.game_masters`, and `sim.initialization.simulation`.
- Agent initialization is a slottable `AgentInitializer`. The default/raw path
  builds typed `AgentInitializationContext` payloads and calls
  `agent.initialize(...)`; formative memory generation is an agent initializer.
- Game-master initialization is a two-layer contract: the Engine-level strategy
  calls `gm.initialize(agents, context)`, and each GM owns a required
  per-GM `initializer` slot.
- Native Game Masters expose only `initialize`, `update`, `acting_agents`,
  `action_prompt`, `make_observation`, and `resolve_action`.
- Seed posts are simulation initialization. They are generated or loaded by a
  `SimulationInitializer`, mapped to typed `ToolCall` actions, and posted
  through `gm.resolve_action(...)`.
- Native backend apps no longer expose seed-post helper methods. They expose
  callable actions; simulation initialization uses those actions through GM
  resolution.

## Native Initialization Boundary Work Queue
- [x] Add typed initialization context objects.
- [x] Add `initialization/agents`, `initialization/game_masters`, and
  `initialization/simulation` phase packages.
- [x] Wire `BaseRuntimeEngine` and `MultiGMRuntimeEngine` to run the three
  explicit startup phases.
- [x] Replace runner construction of `DefaultRuntimeInitializer` with separate
  phase initializers.
- [x] Hard reject old `sim.initializer`, `env.seed_posts`, and
  `env.gm.components.initializer`.
- [x] Move social/backend setup to GM-owned initializer slots.
- [x] Move seed-post generation/loading/posting to simulation initialization
  via typed tool calls and `resolve_action`.
- [x] Delete old `initialization.sim` and GM backend-initialization modules.
- [x] Migrate base and Echo replication configs to the new initialization keys.
- [x] Update tests for three-phase initialization and deterministic no-LLM E2E.
- [x] Run full pytest.

## Native Initialization Boundary Execution Update
- Added agent, game-master, and simulation initializer packages with loud
  `built_in`/`class_path` factories and unsupported-parameter checks.
- `GameMaster.initialize(...)` now delegates to its required per-GM initializer
  slot. `initialize_backend` and `post_seed_posts` are gone from native GM
  protocol/runtime paths.
- Seed posts support `agent`, `csv`, `json`, `fallback`, and `none` providers.
  The `seed_posts` simulation initializer maps posts to backend tool calls for
  twitter-like, reddit-like, and Mastodon apps, with override support.
- Runner now builds `InitializationContext`, `AgentInitializer`,
  `GameMasterInitializerStrategy`, and `SimulationInitializer` directly after
  config processing.
- The deterministic no-LLM E2E now exercises the real Hydra/runner/engine/
  backend path with `sim.llm.provider=scripted` and the new seed-post slot.
- Final validation:
  `uv run pytest -q` -> 246 passed, 2 skipped, 7 deselected.
  `uv run poe lint` -> passed.
  `uv run mkdocs build --strict` -> passed with the existing MkDocs 2.0
  advisory warning.

## Native Engine And GM Cleanup Verification Update

- Verified the previous engine migration was functionally green, then removed
  remaining native Concordia-shaped scaffolding from the GM/component path.
- Deleted `src/silisocs/environments/gm/components/routing.py` and moved the
  useful native constants/base behavior into direct component modules.
- Native GM components now expose direct Silisocs slot methods only. The normal
  component package no longer contains `pre_act`, `post_act`, `set_entity`, or
  `get_entity`; those remain a Concordia adapter concern only.
- Replaced legacy component keys with native slots: `next_acting`, `observe`,
  `resolve`, and `update`.
- Renamed the native next-acting built-in from `all_entities` to `all_agents`
  and migrated tests/config/docs that used it.
- Removed `_run_single_entity_action`; turn policies now call
  `engine.run_agent_step(...)` directly.
- Renamed engine policy factory/user-facing config from action-loop language to
  turn-policy language: `sim.engine.turn_policy`.
- Moved flow helpers out of the base engine surface; flow-only helpers now live
  on `FlowRuntimeEngine`, and multi-GM routing is exercised through
  `MultiGMStepStrategy`.
- Removed old multi-GM introspection/map helper tests and replaced them with a
  native multi-GM routing test.
- Updated dashboard and scenario generator output to emit
  `sim.engine.step.*`, `sim.engine.turn_policy.*`, and
  `evals.probes.schedule.*`.
- Updated docs/glossary/ADR language so `gm.components` is clearly a native slot
  system, not a Concordia lifecycle component system.
- Added boundary tests for native GM component lifecycle hooks, removed routing
  imports, and stale dashboard/scenario-gen config keys.
- Converted Echo replication GM components that were configured into native GMs
  from Concordia lifecycle methods to native direct slot methods.
- Final validation:
  - `uv run pytest -q` -> 234 passed, 2 skipped, 7 deselected.
  - `uv run poe lint` -> passed.
  - `uv run mkdocs build --strict` -> passed with the external MkDocs 2.0
    advisory warning only.

## Final Native Runtime Surface Hardening Update

- Removed the temporary GM component key module and replaced internal component
  wiring with explicit typed slots: `next_acting`, `observe`, `resolve`, and
  `update`.
- Added `GameMaster.update(step, agents, context)` to the native GM protocol.
  Engines call it once per GM before actor selection each step.
- Converted recommendation refresh into the native `update` component slot via
  `SocialRecommendationUpdateComponent`; old `env.gm.components.recommend`
  hard-fails.
- Replaced the old clever `ActionOutput.text(...)` / `output.text` descriptor
  with explicit constructors: `from_text`, `from_tool_calls`,
  `structured_response`, `choice_response`, `float_response`, and `skip`.
- Deleted `silisocs.utils.media`, `silisocs.utils.network`, and
  `silisocs.utils.misc`. Model providers live in `runtime.language_models`; social graph
  helpers live in `environments.backends.social.network`; logs/telemetry live
  under `runtime`.
- Removed flow-specific turn-policy helpers. Flow engines schedule agent groups;
  the engine turn policy remains global.
- Added boundary tests for deleted utility imports, removed GM keys/routing, and
  shipped config use of the old recommend slot.
- Final validation:
  - `uv run pytest -q` -> 238 passed, 2 skipped, 7 deselected.
  - `uv run poe lint` -> passed.
  - `uv run mkdocs build --strict` -> passed with the external MkDocs 2.0
    advisory warning only.

## GM And Engine Final Shape Cleanup Update

- Replaced native `RuntimeComponent`, `LoggingComponent`, and `FlowComponent`
  with a plain `BaseComponent` plus role-specific ABCs.
- Made every native GM behavior slottable: `initialize`, `next_acting`,
  `action_prompt`, `observe`, `resolve`, and `update`.
- Moved action prompt construction into `DefaultActionPromptComponent`; the GM
  now delegates prompt generation like every other slot.
- Moved GM-owned backend setup under `env.gm.components.initialize`; old
  `env.gm.initializer` now hard-fails in runner validation.
- Flow-routed GMs now route slot instances via `instances` plus `flow_map`
  instead of component field overrides. Custom components remain flow-unaware.
- Flow-aware engine scheduling now asks the GM for turns per flow group, which
  lets routed `next_acting` components work without putting flow logic in the
  base engine.
- Removed hidden component log side channels. Runtime logging remains explicit
  through action/probe/model/metrics logs and component-owned loggers where
  needed.
- Added scripted no-LLM assertions for prompt/response log shape in addition
  to action/probe artifacts.

## Documentation Repair And Compat Agent Update

- The apparent line-count spike in the final runtime pass was mostly caused by
  moving flat runtime files into focused subpackages. Git counted destination
  files such as `runtime.language_models`, `runtime.checkpointing`,
  `runtime.construction`, and `runtime.execution.session` as additions while
  also counting the old flat files as deletions.
- The real new code in that pass was narrower: checkpoint restore helpers,
  scripted backend/dashboard E2E tests, the dashboard config writer, and small
  runtime package boundaries. The broader branch remains net smaller after
  deleting the old native Concordia-style clones and flat utility modules.
- Some documentation rewrites were too aggressive and flattened useful domain
  language. Repair strategy is now locked: restore good original prose first,
  then patch only stale code/config references.
- `silisocs.agents.concordia.ConcordiaAgent` is the explicit compatibility
  version of the old Concordia-style entity prefab. It is only valid with
  `compat: concordia` and is wrapped by `ConcordiaAgentAdapter` before it reaches
  the native engine.

## Final Docs Restoration And Agent Builder Shape Update

- Restored `agent_docs/*_old.md` as the semantic baseline for the agent-facing
  architecture, scenario-design, new-scenario, and new-study docs, then patched
  only stale runtime/config facts in the current docs.
- Reaffirmed **Agent Builder** as the config-to-agent-spec translator. It
  returns `AgentConfig` records and does not instantiate runtime agents.
- Split the default builder internals into focused modules for record loading,
  fixed-action rendering, parameter mapping, and normalization while keeping
  `BaseAgentBuilder` as the public subclassing surface.
- Added scripted Concordia compatibility coverage: `ConcordiaAgent` must be
  configured with `compat: concordia`, then enters native runtime through
  `ConcordiaAgentAdapter` and can act with `ScriptedLanguageModel`.

## Public Release Follow-Up

- Keep optional-library coverage in the normal contributor path by using
  `uv sync --all-extras --group dev --group docs` before full validation.
- Before release, do one final user-facing docs read-through for prose quality;
  technical boundary tests catch stale keys but not tone or pedagogy.
