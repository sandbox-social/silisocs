# v0 Improvements Plan

This plan captures the current review findings and turns them into concrete work items for getting the codebase into a cleaner v0 state.

## P0: Correctness and Observability

### 1. Attribute probe LLM usage to the probe phase

**Problem:** Usage reporting documents `probe/action/other` splits, but probe execution does not appear to set the model retry/usage phase to `probe`. Probe calls likely get counted as `other`.

**Files to inspect/change:**

- `docs/configuration.md`
- `src/silisocs/simulation_engines/scheduling.py`
- `src/silisocs/evaluations/probes/deployment.py`
- `src/silisocs/evaluations/probes/agent_speech.py`
- `tests/test_llm_usage_reporting.py`

**Plan:**

- Bracket probe deployment with the same phase-context mechanism used for action turns.
- Add an integration test that runs an actual probe path and verifies probe tokens/retries land under `probe`.
- Keep docs unchanged if behavior is fixed; otherwise update docs to reflect the real phase model.

**Done when:**

- Probe LLM calls are reported under `probe`.
- Existing action-phase accounting remains unchanged.

### 2. Make dashboard results support nested multi-GM logs

**Problem:** Event resolvers support flat and per-GM log layouts, but dashboard result loading reads only root-level event files. Multi-GM runs can be displayed incompletely or split incorrectly.

**Files to inspect/change:**

- `src/silisocs/dashboard/results.py`
- `src/silisocs/evaluations/action_events.py`
- `src/silisocs/evaluations/exposure.py`
- `tests/test_dashboard_results.py`

**Plan:**

- Use the existing event-file resolvers from dashboard loading.
- Treat the parent run directory as the run, not each GM subdirectory.
- Add tests for flat logs, nested per-GM logs, and mixed action/exposure/probe availability.

**Done when:**

- Dashboard loads complete action/probe/exposure data for multi-GM runs.
- Existing single-GM/flat run behavior stays compatible.

## P1: Backward Compatibility and Runtime Surprises

### 3. Handle legacy retrieval memory `k` configs explicitly

**Problem:** Retrieval memory now uses `window_count` and `retrieved_count`. Unknown params are silently filtered, so configs using `params.k` can run with defaults instead of the requested value.

**Files to inspect/change:**

- `src/silisocs/agents/memory.py`
- `tests/test_memory_policies.py`
- `docs/configuration.md`

**Plan:**

- Decide whether `k` should be an alias or a hard validation error.
- Prefer accepting `k` as a migration alias if real experiment configs used it.
- Add a test proving `k` is either honored or rejected loudly.
- Document the migration behavior.

**Done when:**

- No retrieval memory config silently ignores user intent.

### 4. Prevent or correctly classify summarizing-memory model calls outside action turns

**Problem:** `SummarizingMemory.record()` can trigger summarization during observation. That can happen during initialization, interventions, probes, or other non-action phases, despite comments assuming action bracketing.

**Files to inspect/change:**

- `src/silisocs/agents/native.py`
- `src/silisocs/agents/memory.py`
- `src/silisocs/simulation_engines/interventions.py`
- `tests/test_memory_policies.py`
- `tests/test_llm_usage_reporting.py`

**Plan:**

- Identify all observation paths that can cause memory summarization.
- Either defer summarization to action-time rendering or set an explicit phase around non-action summarization.
- Update comments/docs to explain when memory policies may call models.
- Add a test for summarization triggered outside an agent turn.

**Done when:**

- Summarization calls are either phase-attributed correctly or avoided in non-action contexts.
- Initialization does not unexpectedly make untracked model calls.

### 5. Strengthen intervention preflight validation

**Problem:** Some intervention errors, especially unknown flows for `set_turn_policy` and `set_router`, are detected only when the intervention fires.

**Files to inspect/change:**

- `src/silisocs/simulation_engines/interventions.py`
- `tests/test_interventions.py`
- `docs/configuration.md`

**Plan:**

- Add preflight validation against resolved flow names where available.
- Keep custom/dynamic cases possible, but fail loudly for known invalid built-in configurations.
- Add tests for unknown flow names failing before the run starts.

**Done when:**

- Common intervention typos fail during configuration validation, not halfway through a long run.

## P2: Feature Clarity and Scalability

### 6. Clarify or expand `swap_component` flow behavior

**Problem:** `swap_component` appears to target the default flow component only. Users may expect all matching flow-specific components to be swapped.

**Files to inspect/change:**

- `src/silisocs/environments/gm/game_master.py`
- `src/silisocs/simulation_engines/interventions.py`
- `docs/configuration.md`
- `docs/environment_layer.md`
- `tests/test_interventions.py`

**Plan:**

- Decide whether v0 supports default-flow-only or all-flow swaps.
- If default-only, document that clearly in configuration and environment docs.
- If all-flow, add flow selection semantics and tests.

**Done when:**

- `swap_component` behavior is explicit and covered by tests.

### 7. Stream exposure analysis instead of materializing full logs

**Problem:** Exposure analysis reads full JSONL logs into memory before summarizing. This is fine for small runs, but it conflicts with the scalability direction.

**Files to inspect/change:**

- `src/silisocs/evaluations/exposure.py`
- `tests/test_exposure_logging.py`

**Plan:**

- Convert JSONL reading to generator-based aggregation.
- Preserve the current summary schema.
- Add a regression test that proves action/exposure joins still work with nested logs.

**Done when:**

- Exposure summaries can process large logs without holding every event row in memory.

### 8. Split the intervention module before it grows further

**Problem:** `interventions.py` is readable but becoming a large mixed module: handler interface, built-in handlers, parsing, validation, replay, and telemetry live together.

**Files to inspect/change:**

- `src/silisocs/simulation_engines/interventions.py`
- `tests/test_interventions.py`

**Plan:**

- Split only along existing responsibilities.
- Keep public imports stable.
- Suggested shape:
  - `interventions/base.py`
  - `interventions/handlers.py`
  - `interventions/parse.py`
  - `interventions/replay.py`

**Done when:**

- The intervention system is easier to scan without changing behavior.
- Existing tests pass without broad rewrites.

## Verification Checklist

- `python -m compileall -q src/silisocs tests`
- Focused tests:
  - `uv run pytest tests/test_interventions.py tests/test_memory_policies.py tests/test_exposure_logging.py tests/test_llm_usage_reporting.py tests/test_scalability_phase1.py -q`
  - `uv run pytest tests/test_scalability_phase2_async.py tests/test_concurrent_chain_execution.py tests/test_checkpoint_completeness.py tests/test_auto_resume.py -q`
- Dashboard tests:
  - `uv run pytest tests/test_dashboard_results.py tests/test_analysis_dashboard_parsers.py -q`
- Full suite when time permits:
  - `uv run pytest`

## Known Test Hygiene Issue

`uv run ruff check src/silisocs tests` currently reports a large existing backlog, so lint is not yet useful as a regression gate. Either establish a baseline or split lint cleanup into a separate tracked effort.
