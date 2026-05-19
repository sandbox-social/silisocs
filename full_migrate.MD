# Final Native Runtime Migration Running Document

## Goal
Complete the hard break from Concordia-shaped runtime flow to a native,
typed, direct Silisocs runtime.

The runtime should not route normal execution through GM-as-agent dispatch,
string-prefixed resolve calls, prompt marker insertion, or an opaque
`Simulation` object. The runner builds runtime objects directly, the engine
initializes and runs them, GMs connect agents to backend apps, and agents
return typed action outputs.

## Locked Decisions
- `GameMaster.acting_agents(...)` returns `list[str]`, not `AgentTurn`.
- `ActionSpec` is an agent-facing prompt contract with `output_type` and
  `extra_args`.
- Agents return `ActionOutput`, with typed fields for text, choices, floats,
  tool calls, and structured responses.
- Tool calling and structured responses use `ActionSpec.extra_args`, not
  prompt markers or JSON blocks embedded in strings.
- The language model prompt path uses modern instruct-chat messages, without
  the old completion-context few-shot prompt.
- The public `Simulation` facade is removed. Runner construction becomes
  explicit and helper-driven.
- Initialization moves out of `silisocs.agents.initialization` into
  `silisocs.initialization.{agents,gm,sim}`.
- Compatibility is minimal and isolated under `silisocs.adapters`. Keep only
  lightweight Concordia-style agent compatibility and a clean future GM adapter
  boundary. Delete legacy engine/simulation compatibility.
- Fail loudly. Do not silently fall back to legacy parsing, old config shapes,
  missing seed files, or malformed action payloads.

## Target Public Interfaces
- `GameMaster`:
  - `acting_agents(candidate_agents) -> list[str]`
  - `action_prompt(agent_name: str) -> ActionSpec`
  - `make_observation(agent_name: str) -> str`
  - `resolve_action(agent_name: str, action: ActionOutput) -> str`
- `OutputType`:
  - `TEXT`
  - `CHOICE`
  - `FLOAT`
  - `TOOL_CALLS`
  - `STRUCTURED`
  - `SKIP`
- `ActionSpec`:
  - `prompt: str`
  - `output_type: OutputType`
  - `options: tuple[str, ...] = ()`
  - `tag: str | None = None`
  - `extra_args: Mapping[str, Any] = {}`
  - compatibility property: `call_to_action` may exist only as a short-term
    alias during migration and must be removed or quarantined before final pass.
- `ActionOutput`:
  - `output_type: OutputType`
  - `text: str = ""`
  - `tool_calls: tuple[ToolCall, ...] = ()`
  - `structured: Mapping[str, Any] | None = None`
  - `choice: str | None = None`
  - `number: float | None = None`
  - `raw: Any | None = None`
- `ToolCall`:
  - `name: str`
  - `arguments: Mapping[str, Any]`

## Work Queue
- [x] Replace runtime action types with typed `ActionSpec`, `ActionOutput`,
  and `ToolCall`.
- [x] Update `Agent.act` and native component agents to return `ActionOutput`.
- [x] Replace marker-based tool/structured handling in agent concat
  components.
- [x] Update language model helpers to expose direct instruct, tool-call, and
  structured-output methods.
- [x] Update GMs to return `list[str]`, build typed action specs, and resolve
  typed action outputs.
- [x] Update engine and action-loop policies to use direct GM methods and typed
  action outputs only.
- [ ] Move initialization modules to `silisocs.initialization`.
- [x] Delete or quarantine legacy GM routing and Concordia compatibility.
- [x] Remove the public `Simulation` facade and move checkpoint/log helpers to
  explicit runtime helper modules.
- [x] Migrate probes/evaluations to typed structured outputs.
- [x] Migrate configs, dashboard, scenario generator, docs, and tests.
- [x] Run cleanup scans and delete remaining unmigrated normal-path code.
- [x] Run targeted tests, full tests, and lint.

## Running Change Log
- Started implementation on branch `migrate-from-concordia`.
- Added typed runtime contract tests in `tests/test_native_runtime_interfaces.py`.
- Added `ActionSpec.extra_args`, `ToolCall`, and `ActionOutput` to
  `silisocs.core.types`.
- Updated the direct engine action path to pass `ActionOutput` into
  `gm.resolve_action(agent_name, action)`.
- Updated action-loop policy helpers to count typed `ActionOutput.tool_calls`.
- Updated `SocialConcatActComponent` to return typed tool-call and structured
  outputs from `ActionSpec.extra_args`.
- Updated `StructuredProbe` to request `OutputType.STRUCTURED` with schema in
  `extra_args`.
- Moved runtime initialization modules from `silisocs.agents.initialization`
  to `silisocs.initialization`.
- Deleted the old `silisocs.agents.initialization` package so old imports fail
  loudly.
- Simplified `GptLanguageModel.sample_text` to use direct instruct-chat
  messages instead of the old completion-context few-shot scaffold.
- Kept temporary migration aliases (`FREE`, `SKIP_THIS_STEP`, etc.) so
  unmigrated call sites can be converted incrementally and detected by the
  cleanup gate.
- Verified focused interface test:
  `uv run pytest tests/test_native_runtime_interfaces.py -q`.
- Verified focused engine/policy typed-output tests:
  `uv run pytest tests/test_engine_policy_factory.py::test_engine_single_action_uses_direct_gm_methods_without_name_prefix tests/test_engine_policy_factory.py::test_action_policies_count_typed_tool_calls_toward_budget -q`.
- Verified typed tool/structured smoke tests:
  `uv run pytest tests/test_tool_calling_smoke.py -q`.
- Verified broader native/GM/engine/tool slice:
  `uv run pytest tests/test_native_runtime_interfaces.py tests/test_engine_policy_factory.py tests/test_generic_game_master_build.py tests/test_shared_flow_gm_build.py tests/test_tool_calling_smoke.py -q`.
- Verified initializer package migration:
  `uv run pytest tests/test_initializer_bootstrap.py tests/test_runner_processing_mode.py tests/test_social_app_init_logging.py -q`.
- Verified direct instruct-chat prompt shape:
  `uv run pytest tests/test_native_runtime_interfaces.py::test_gpt_language_model_sample_text_uses_direct_instruct_messages -q`.
- Split `silisocs.runtime.simulation.Simulation` into explicit
  `runtime.assembly`, `runtime.checkpointing`, and `runtime.io` helpers.
- Updated the runner to build runtime objects directly and call the engine
  run loop without the opaque `Simulation` wrapper.
- Removed the public `silisocs.Simulation` export and deleted
  `src/silisocs/runtime/simulation.py`.
- Verified direct-runner/checkpoint slice:
  `uv run pytest tests/test_simulation_checkpoint_policy.py tests/test_runner_mode_matrix.py tests/test_runner_processing_mode.py -q`.
- Deleted `src/silisocs/environments/gm/act.py` and removed `SMAct` /
  `MultiFlowSMAct` exports.
- Removed `SwitchAct` from native GM routing components and from the Concordia
  adapter namespace.
- Converted prompt-pipeline tests from marker/SMAct wrapping to direct native
  `EnvironmentGameMasterRuntime.action_prompt(agent_name)` checks with typed
  `ActionSpec.extra_args`.
- Tightened `EnvironmentGameMasterRuntime` to fail loudly unless next-acting,
  observation, and resolve components expose direct native methods.
- Verified GM/prompt/adapter slice:
  `uv run pytest tests/test_prompt_pipeline_integration.py tests/test_generic_game_master_build.py tests/test_shared_flow_gm_build.py tests/test_concordia_import_boundary.py -q`.
- Removed marker-schema parsing from `SocialConcatActComponent`; tool calls and
  structured responses now require typed `ActionSpec.extra_args`.
- Removed the unused marker-based `concat_act_reason.py`.
- Tightened `ToolCallingResolveComponent` to accept only
  `ActionOutput.TOOL_CALLS`.
- Updated fixed-action agents to return typed `ActionOutput`, including real
  `ToolCall` objects for tool-calling mode.
- Updated action-loop policy budget accounting to count typed tool calls
  directly instead of JSON tool-call strings.
- Verified typed action/probe/policy slice:
  `uv run pytest tests/test_tool_calling_smoke.py tests/test_action_mode_architecture.py tests/test_fixed_entity_runtime.py tests/test_engine_policy_factory.py tests/test_probe_types.py -q`.
- Renamed the OpenAI structured-output model helper from
  `sample_structured_response(...)` to `sample_structured(...)` and updated the
  agent path to require that direct method.
- Adjusted `ActionOutput` so `ActionOutput.text(...)` and
  `ActionOutput.tool_calls(...)` are constructor-style accessors while
  instances expose `.text` and `.tool_calls` payloads.
- Verified full test suite after formatting/type fixes:
  `uv run pytest -q` -> 246 passed, 2 skipped, 7 deselected.
- Verified lint/typecheck:
  `uv run poe lint` -> passed.

## Remaining Cleanup Scan
Legacy runtime symbols were removed from normal-path runtime code and public docs.
Compatibility-only `action_spec_parser` remains isolated in
`silisocs.adapters.concordia`.

## Cleanup Gate
Before this migration is considered complete, normal runtime code must not
contain:
- `AgentTurn`
- `SMAct`
- `MultiFlowSMAct`
- `SwitchAct`
- `NEXT_ACTING`
- `NEXT_ACTION_SPEC`
- `MAKE_OBSERVATION`
- `RESOLVE`
- `action_spec_parser`
- prompt marker constants such as `TOOL_CALLING_MODE` or
  `STRUCTURED_RESPONSE_MODE`
- `runtime.simulation.Simulation`
- GM `_act_component` metadata reads

Known allowed exceptions during cleanup:
- `silisocs.adapters` may contain explicit compatibility shims.
- migration docs may mention removed symbols as historical context.

## Final Native Core Cleanup Update

- Created `PLAN.MD` as the active running tracker for the final native-core
  cleanup pass.
- Replaced default component-style agent construction with
  `silisocs.agents.entity.NativeAgent`.
- Removed `ActionSpec.call_to_action` from the native type surface; native code
  now uses `ActionSpec.prompt`.
- Quarantined local Concordia-like component/document copies so imports fail
  loudly with migration guidance.
- Moved Concordia-style agent and GM compatibility into
  `silisocs.adapters.concordia`, backed by real optional Concordia imports.
- Updated shipped legacy Concordia-style scenario modules to import through the
  adapter, and marked the Echo loose-social agent config with
  `compat: concordia`.
- Updated runtime assembly so non-native agent/GM prefab returns hard-fail
  unless `compat: concordia` is explicit.
- Made probe fallback native: batch questionnaire first, then single-probe
  fallback; removed phase mutation recovery.
- Added `CONTEXT.md` native/compat language and ADR
  `docs/adr/0001-native-core-concordia-compatibility-boundary.md`.
- Updated docs for `NativeAgent`, native GM helpers, and explicit Concordia
  compatibility.
- Final validation:
  - `uv run pytest -q` -> 257 passed, 2 skipped, 7 deselected.
  - `uv run poe lint` -> passed.
  - `uv run mkdocs build --strict` -> passed (with upstream MkDocs 2.0
    advisory warning only).

## Final Native Release Cleanup Update

- Completed the hard native cleanup pass:
  - removed native `silisocs.core` and moved contracts to
    `silisocs.runtime.types` / `silisocs.runtime.language_models`;
  - removed native prefab/entity modules, native memory-bank utilities,
    local Concordia-style agent components/documents, GM `act.py`, and the old
    opaque runtime simulation facade;
  - renamed native agents to `silisocs.agents.native.NativeAgent` and
    `silisocs.agents.fixed.FixedAgent`;
  - migrated configs, scenario generator, dashboard defaults, and tests to
    direct `class_path` + `params`;
  - kept Concordia only as explicit `compat: concordia`, using upstream
    `concordia.*` imports in legacy scenario modules and the adapter boundary;
  - replaced checkpoint payloads with the native schema version and hard-error
    old prefab-shaped checkpoints.
- Final residue scan:
  - native source/tests/configs no longer reference deleted `silisocs.core`,
    `utils.memory`, `agents.entity`, `fixed_entity`, `AgentQuery`,
    `TemplateProbe`, `run_episode`, or `_setup_agents_and_environment`;
  - remaining `prefab_module` / `sim.memory_backend` / `sim.sentence_encoder`
    mentions are hard-error migration guards;
  - remaining direct `concordia.*` imports are only explicit legacy scenario
    modules.
- Final validation:
  - `uv run pytest -q` -> 257 passed, 2 skipped, 7 deselected.
  - `uv run poe lint` -> passed.

## Native Initialization Boundary Cleanup Update

- Replaced the hidden runtime initializer path with three explicit Engine
  startup phases:
  1. agent initialization;
  2. Game Master initialization;
  3. simulation initialization.
- Removed native `gm.initialize_backend(...)` and `gm.post_seed_posts(...)`.
  Native Game Masters now expose only `initialize`, `acting_agents`,
  `action_prompt`, `make_observation`, and `resolve_action`.
- Added typed initialization context objects and new phase packages:
  - `silisocs.initialization.agents`
  - `silisocs.initialization.game_masters`
  - `silisocs.initialization.simulation`
- Moved social/backend graph setup into GM-owned initializer slots. Each native
  GM spec now carries an explicit `initializer` config.
- Moved seed posts into simulation initialization. Seed content is generated or
  loaded by the simulation initializer, converted to typed backend `ToolCall`s,
  and posted via `gm.resolve_action(...)`.
- Deleted the old native initializer modules:
  - `silisocs.initialization.sim`
  - `silisocs.environments.gm.backend_initialization`
- Migrated base config, Echo replication config, dashboard output, docs, and
  tests to `sim.initialization.agents/game_masters/simulation`.
- Added ADR `docs/adr/0002-engine-owned-initialization-lifecycle.md` and
  updated `CONTEXT.md` with the new glossary.
- Final validation:
  - `uv run pytest -q` -> 246 passed, 2 skipped, 7 deselected.
  - `uv run poe lint` -> passed.
  - `uv run mkdocs build --strict` -> passed with upstream MkDocs 2.0 advisory
    warning only.

## Engine Shape and Config Migration Update

- Refactored the runtime engine into an explicit contract with clear lifecycle
  methods:
  - `initialize(...)`
  - `run_agent_step(...)`
  - `run_step(...)`
  - `run_loop(...)`
- Introduced strategy interfaces for loop behavior, step behavior, turn policy,
  probes, and recording so core engine flow is minimal and composable.
- Moved probe execution behind `DefaultProbeRunner` in evaluations; the engine no
  longer owns probe scheduling internals.
- Removed legacy engine config keys and now hard-require the new shape:
  - `sim.engine.class_path/loop/step/turn_policy`
  - `evals.probes.schedule`
- Migrated scenario and replication sim configs to the new engine structure and
  canonical GM flow bindings.
- Removed remaining unused `next_game_master` routing alias from native GM
  components.
- Validation after this pass:
  - `uv run pytest -q` -> 246 passed, 2 skipped, 7 deselected.
  - `uv run poe lint` -> passed.
  - `uv run mkdocs build --strict` -> passed (with external MkDocs ecosystem
    advisory warning only).

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

## GM And Engine Final Shape Cleanup Update

- Native GM components now use a plain `BaseComponent` plus role-specific ABCs.
  Removed native `RuntimeComponent`, `LoggingComponent`, and `FlowComponent`.
- GM behavior is fully slottable through `components.initialize`,
  `components.next_acting`, `components.action_prompt`, `components.observe`,
  `components.resolve`, and `components.update`.
- `action_prompt` is now delegated to `DefaultActionPromptComponent`; GMs no
  longer build action specs inline.
- Flow-specific behavior is configured with component `instances` and
  `flow_map`; routing lives in the GM, not in custom components.
- Engine flow scheduling now asks the GM for selected turns per flow group, so
  routed `next_acting` slots work naturally.
- Moved default engine stats/log recording to
  `silisocs.simulation_engines.recorders`.
- Migrated shipped env configs and dashboard output from `gm.initializer` to
  `gm.components.initialize`.
- Final validation:
  - `uv run pytest -q` -> 225 passed, 2 skipped, 7 deselected.
  - `uv run poe lint` -> passed.
  - `uv run mkdocs build --strict` -> passed with the external MkDocs 2.0
    advisory warning only.

## Final Native Runtime Robustness Pass Update

- Fixed a runtime logging bug that could drop action events under scripted
  multi-tool outputs:
  - JSONL writer now serializes nested runtime payloads safely (`ToolCall`,
    `ActionOutput`, dataclasses, enums, mappings, lists).
  - This removed async writer thread crashes and stabilized replay/log tests.
- Added structured replay payload support for checkpoints:
  - `ActionOutput.to_dict()` / `ActionOutput.from_dict()`.
  - checkpoint metadata now stores runtime details (`action_events_file`,
    `output_rootname`, and serialized Python RNG state).
  - resume path restores RNG state and can replay prior turns through
    `checkpoint restore` simulation initialization.
- Expanded scripted-model runtime context telemetry:
  - engine now sets per-turn model context (`agent_name`, `episode_idx`,
    `phase`, `tag`) and logs replayable typed actions in action events.
- Updated dashboard config emission to the native probe schema:
  - removed legacy `queries` / `query_*` and `include_entities` /
    `exclude_entities` keys.
  - dashboard now emits `probes.probes` and deployment filters via
    `include_agents` / `exclude_agents`.
  - cleaned remaining native dashboard wording from `entity` to `agent`.
- Updated docs to match the current runtime/config surface:
  - probe examples now use `probes.probes` + `probe_type/probe_data`;
  - fixed stale `entity` wording in usage/environment/dashboard references;
  - refreshed stale coverage notes around removed prefab/native-local legacy.
- Added/updated tests:
  - scripted social E2E verifies posts + likes + timeline-context behavior.
  - checkpoint replay initializer test validates typed action replay.
  - boundary test now checks dashboard/scenario-gen source for banned legacy
    keys and confirms current probe key usage.
- Final validation:
  - `uv run pytest -q` -> 229 passed, 2 skipped, 7 deselected.
  - `uv run poe lint` -> passed.
  - `uv run mkdocs build --strict` -> passed (external MkDocs ecosystem
    advisory warning only).
