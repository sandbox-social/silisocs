# Agent/Model/GM-Owned Backend Bootstrap Migration Plan

> Running tracker for the second native-runtime cleanup pass.

## Summary
- [x] Clarify the agent contract: every agent has a `LanguageModel`, and base agents inherit a protected `_call_model(context, action_spec)` helper used inside `act()`.
- [x] Clarify the model contract: one abstract `LanguageModel` surface, separate OpenAI and local OpenAI-compatible implementations, no Qwen/model-name heuristics.
- [x] Move backend initialization ownership from initializer backend components into game masters.
- [x] Engine startup order: agent initialization, GM/backend initialization, seed-post simulation initialization, main loop.
- [x] Fail loudly on missing model methods, unsupported output types, old config shapes, and legacy initialization paths.

## Public Interface Changes
- [x] `silisocs.agents.base_agent.Agent`
  - [x] Add `__init__(model: LanguageModel)`.
  - [x] Add protected `_call_model(context: str, action_spec: ActionSpec) -> ActionOutput`.
  - [x] Keep `act(action_spec) -> ActionOutput` abstract; custom agents curate context inside `act()` and call `_call_model(...)`.
  - [x] Keep `initialize(context)` as no-op unless overridden.
  - [x] Update built-in, fixed, test, and custom scenario agents to call `super().__init__(model)`.
- [x] `silisocs.core.models.LanguageModel`
  - [x] Explicit `sample_text`.
  - [x] Explicit `sample_choice`.
  - [x] Explicit `sample_tool_calls`.
  - [x] Explicit `sample_structured`.
  - [x] `NoLanguageModel` returns deterministic empty/first-choice/no-tool/no-structured outputs.
- [x] Model implementations/config
  - [x] Introduce `OpenAILanguageModel`.
  - [x] Introduce `LocalLanguageModel`.
  - [x] Add `sim.llm.provider: openai | local | disabled`.
  - [x] Add `sim.llm.extra_kwargs`.
  - [x] Remove model-name provider heuristics and Qwen-specific branching.
- [x] Game master/backend initialization
  - [x] Add direct GM `initialize_backend(agents, context) -> None`.
  - [x] `EnvironmentGameMasterRuntime` owns and delegates to a backend initializer object.
  - [x] Runtime initializer owns agent memory and seed-post simulation setup only.

## Implementation Changes
- [x] Agent contract and generic agent path
  - [x] Update `Agent` base class and all subclasses/tests to store a model.
  - [x] Move output-type routing logic toward `Agent._call_model(...)`.
  - [x] Keep concat components focused on context/prompt assembly.
  - [x] Update `ComponentEntity.act()` to collect context and return typed `ActionOutput`.
  - [x] Update fixed and custom agents to pass models or `NoLanguageModel`.
- [x] Language model cleanup
  - [x] Refactor `src/silisocs/utils/media.py` into provider classes.
  - [x] Remove `_use_qwen_extra_body`.
  - [x] Implement local OpenAI-compatible provider with explicit `extra_kwargs`.
  - [x] Replace `sample_tool_call` with `sample_tool_calls`.
  - [x] Make structured-output failures raise clear exceptions after retries.
  - [x] Keep retry telemetry provider-neutral.
- [x] GM-owned backend bootstrap
  - [x] Move backend initializer classes into `silisocs.environments.gm.backend_initialization`.
  - [x] Inject configured backend initializer into `EnvironmentGameMasterRuntime`.
  - [x] `DefaultRuntimeInitializer.initialize(...)` calls agent memory, GM backend init, seed provider, seed posting.
  - [x] Delete or hard-error old initializer backend factories.
- [x] Runner/config flow
  - [x] `build_runtime_initializer()` builds agent-memory and seed-post pieces only.
  - [x] `build_game_masters()` passes backend initializer config into each GM config.
  - [x] `select_large_language_model()` is provider-based and rejects old implicit config.
  - [x] Migrate shipped configs, dashboard defaults, scenario generator, docs, replication configs.

## Test Plan
- [x] Add agent `_call_model` routing tests for text, choice, float, tool calls, structured, skip, and unsupported output types.
- [x] Add tutorial-style custom agent test.
- [x] Add model provider tests for OpenAI direct messages, local `api_base` requirement, `extra_kwargs`, no Qwen special-casing, and provider failure.
- [x] Add GM/backend initialization tests for phase order and no construction-time setup.
- [x] Update existing tests:
  - [x] `tests/test_native_runtime_interfaces.py`
  - [x] `tests/test_tool_calling_smoke.py`
  - [x] `tests/test_runner_processing_mode.py`
  - [x] `tests/test_initializer_bootstrap.py`
  - [x] model retry telemetry tests
  - [x] docs/config tests and dashboard/scenario generator tests
- [x] Run targeted validation.
- [x] Run `uv run pytest -q`.
- [x] Run `uv run poe lint`.

## Assumptions
- This is a hard break for old `sim.llm` implicit provider selection.
- `_call_model` is protected because it is intended for agent internals, not external runtime calls.
- GM-owned backend initialization is now the target architecture.
- Existing output artifacts and seed-post semantics should remain stable.

## Running Notes
- 2026-05-16: Created tracker before implementation.
- 2026-05-16: Added agent/model/backend ownership tests and made focused suites green.
- 2026-05-16: Implemented provider-based `OpenAILanguageModel`/`LocalLanguageModel`, explicit `sample_tool_calls`, protected agent `_call_model`, and GM-owned backend initialization.
- 2026-05-16: Updated configs/docs/dashboard defaults for explicit model provider and nested agent-memory initializer config.
- 2026-05-16: Final validation passed: `uv run pytest -q` reported 256 passed, 2 skipped, 7 deselected; `uv run poe lint` passed.
