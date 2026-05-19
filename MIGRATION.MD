# Native Silisocs Runtime Migration Plan

## Summary

Silisocs should become a native social-simulation framework that installs and runs without
Concordia by default. Concordia portability should remain available through an optional
compatibility bridge, not through direct imports spread across the runtime.

The earlier analysis in this file was directionally correct: the codebase is still
Concordia-first at construction, entity lifecycle, game-master component lifecycle,
action specs, engine inheritance, and docs. The important correction is that much of the
actual social-media behavior is already Silisocs-owned. Backends, action catalogs, flow
routing policy, study orchestration, and most artifact conventions can be preserved while
we replace Concordia lifecycle/type plumbing.

Target packaging decision:

- Default install: no `gdm-concordia` dependency.
- Optional bridge install: `silisocs[concordia]`.
- Native runtime APIs are the primary extension surface.
- Concordia types appear only in `silisocs.adapters.concordia`, legacy examples, and
  bridge-specific tests/docs.

## Current Branch Implementation Status

This branch implements the first complete migration milestone:

- `gdm-concordia` has been removed from default dependencies and moved to the
  `concordia` optional extra.
- Native runtime contracts now exist under `silisocs.core` for action specs, prefab
  records, component/entity lifecycle, and model helpers.
- Runtime code no longer imports `concordia.*` directly. A test enforces that any future
  direct Concordia import must stay isolated behind `silisocs.adapters.concordia`.
- The adapter is native-backed: it preserves the small Concordia-shaped surface the
  current runtime historically used, without making the external Concordia package part
  of default runtime importability.
- Public docs now present Silisocs-native extension contracts first and document
  Concordia only as an optional legacy bridge.
- Existing runtime behavior is preserved by the current test suite, lint workflow, docs
  build, and package build.

The remaining cleanup target is reducing internal compatibility-shaped names over time.
That is now ordinary internal API cleanup rather than a hard external Concordia
dependency.

## Verified Dependency Audit

Rating scale:

- **5/5 Critical**: native runtime cannot exist until this is replaced or isolated.
- **4/5 High**: important runtime behavior is coupled, but mostly through lifecycle types.
- **3/5 Medium**: indirect or feature-specific coupling.
- **2/5 Low**: docs/tests/vocabulary coupling.
- **1/5 Very low**: comments or legacy residue.

| Area | Where | Why used today | Importance |
|---|---|---|---|
| Packaging/default importability | `pyproject.toml` | `gdm-concordia` is a core dependency and many imports are module-level. | 5/5 |
| Runtime config and prefabs | `src/silisocs/runtime/runner.py`, `runtime/dataclasses.py`, `runtime/simulation.py` | Builds `prefab_lib.Config`, `InstanceConfig`, `Role`; custom prefabs must subclass Concordia `Prefab`. | 5/5 |
| Simulation lifecycle | `runtime/simulation.py` | Subclasses Concordia `Simulation`; assumes Concordia entities, component state, and HTML log helpers. | 5/5 |
| Engine/action-spec loop | `simulation_engines/base_engines.py` | Subclasses `simultaneous.Simultaneous`; uses `entity.ActionSpec`, `OutputType`, and `action_spec_parser`. | 5/5 |
| GM wrapper and act routing | `environments/gm/base_game_master.py`, `gm/act.py`, `shared_flow_game_master.py` | Builds `EntityAgentWithLogging`; `SMAct` subclasses Concordia `SwitchAct`. | 5/5 |
| Standard LLM agent | `agents/entity.py`, `agents/components/concat_act.py` | The default agent is composed from Concordia components; act component subclasses `ConcatActComponent`. | 5/5 |
| GM slot components | `environments/gm/components/*` | Observe/resolve/next-acting components inherit Concordia component classes and inspect Concordia `ActionSpec`. | 4/5 |
| Initialization, probes, seed posts | `agents/initialization/*`, `evaluations/probes/*`, `gm/components/seed_post_provider.py` | Memory initialization is a Concordia GM; probes construct Concordia action specs and recover Concordia phase state. | 4/5 |
| LLM/model abstraction | `utils/media.py` | `GptLanguageModel` subclasses Concordia `LanguageModel`; uses Concordia no-op model, sampling, and measurements helpers. | 4/5 |
| Memory | `utils/memory.py`, `agents/entity.py` | `list` backend is mostly native, but the default LLM agent wraps it in Concordia memory components; `associative` backend is Concordia. | 4/5 |
| Studies/evaluators | `experiments/run_study.py`, `evaluations/default_evaluators.py` | Mostly indirect through runner, checkpoints, and artifact names. | 3/5 |
| Legacy scenario agents | `scenarios/election/input/entity_lib/*` | Custom Concordia-prefab examples need bridge support or rewrite guidance. | 3/5 |
| Tests/docs/dashboard | `tests/*`, `docs/*`, `dashboard/launch_app.py` | Tests and docs expose Concordia contracts; dashboard mostly exposes prefab/component vocabulary. | 2-3/5 |
| Backends/action catalog | `environments/backends/*` | Mostly independent; only docs phrase it as Concordia-facing. | 1/5 |

Corrections to the previous analysis:

- Correct: runtime construction, GM lifecycle, engine loop, action specs, components,
  LLM agents, memory, probes, and docs are coupled.
- Overstated: backends, action catalogs, study evaluators, and dashboard are mostly
  coupled indirectly through runtime artifacts or vocabulary.
- Missing detail: `BaseRuntimeEngine` already contains mostly custom Silisocs behavior;
  the hard dependency is inheritance plus Concordia action-spec/parser constants.
- Missing detail: `FixedActionEntityRuntime` is already close to native; only its prefab
  wrapper and action-spec compatibility tie it to Concordia.
- Missing detail: compatibility should be an optional extra, not a default dependency.

## Target Architecture

Introduce native contracts under `silisocs.core` and make runtime code depend on them:

- `ActionSpec`: dataclass with `call_to_action: str`, `output_type: OutputType`,
  `options: tuple[str, ...] = ()`, and `tag: str | None = None`.
- `OutputType`: enum covering current runtime values: `FREE`, `CHOICE`, `FLOAT`,
  `MAKE_OBSERVATION`, `NEXT_ACTING`, `NEXT_ACTION_SPEC`, `RESOLVE`,
  `NEXT_GAME_MASTER`, `TERMINATE`, and `SKIP_THIS_STEP`.
- `Agent`: promote the existing three-method contract (`name`, `observe`, `act`) as
  the native runtime interface, with optional `get_state` and `set_state`.
- `Prefab`: native build protocol with `build(model, memory_bank) -> Agent`.
- `Component`: native lifecycle with `pre_act(action_spec) -> str`, optional
  `post_act`, `update`, `get_state`, `set_state`, and `set_entity`.
- `ComponentEntity`: native component-owning agent wrapper that replaces
  `EntityAgentWithLogging` for GMs and default LLM agents.
- `Engine`: native `run_loop(...)` protocol implemented by Silisocs engines.
- `LanguageModel`: native protocol for `sample_text`, `sample_choice`, optional
  `sample_tool_call`, and optional `sample_structured_response`.
- `MemoryBank`: native protocol implemented by `ListMemoryBank` and a native
  associative/lexical backend.

Add `silisocs.adapters.concordia` as the only place where Concordia is imported:

- Convert native `ActionSpec`/`OutputType` to and from Concordia equivalents.
- Wrap Concordia prefabs as native prefabs.
- Wrap Concordia agents/components as native agents/components.
- Support explicit config compatibility with `compat: concordia` on prefab/component
  slots.
- Do not support arbitrary Concordia full simulations as the primary runtime; the
  bridge exists for prefabs, agents, and components needed for portability.

## Implementation Changes

### 1. Native contracts and import boundary

- Add `silisocs.core.types`, `silisocs.core.prefab`, `silisocs.core.components`,
  `silisocs.core.models`, and `silisocs.core.memory`.
- Replace direct Concordia imports in runtime, engines, GMs, components, probes, and
  default agents.
- Add an import-boundary test that fails when `src/silisocs` imports Concordia outside
  `silisocs.adapters.concordia`.

### 2. Runner and config construction

- Replace `prefab_lib.Config`, `InstanceConfig`, and `Role` with native
  `RuntimeConfig`, `InstanceConfig`, and `Role`.
- Keep existing YAML keys stable: `prefab_module`, `prefab`, `sim_role`,
  `gm.components.*`, and flow routing keys.
- Support old Concordia-prefab modules only when `compat: concordia` is configured or
  the bridge loader detects a Concordia `Prefab` and the optional dependency is present.

### 3. Native agents and GM lifecycle

- Rebuild `silisocs.agents.entity.Entity` as a native prefab returning a native LLM
  agent composed from small native context components: instructions, constants,
  observation history, memory, and concat act.
- Keep `FixedActionEntityRuntime` largely unchanged and make its prefab native.
- Replace `EntityAgentWithLogging` usage in GMs and initializers with `ComponentEntity`.

### 4. Native component slots

- Port observe, resolve, next-acting, recommend, initializer-observe, and flow-field
  behavior to native components.
- Keep constructor signatures and config names unchanged.
- Replace Concordia component keys with Silisocs constants while accepting legacy key
  aliases during checkpoint/config migration.

### 5. Native act routing and engine

- Replace `SMAct` and `MultiFlowSMAct` inheritance with native routers that dispatch by
  `OutputType`.
- Replace `BaseRuntimeEngine(simultaneous.Simultaneous)` with a native engine retaining
  current behavior: probes, worker throttling, flow grouping, multi-GM phase grouping,
  checkpoints, and action-loop policies.
- Replace `engine_lib.action_spec_parser` with a native parser for the current
  `prompt: ... ;;type: ...` action-spec text.

### 6. Models, documents, memory, and logs

- Make `GptLanguageModel` implement native `LanguageModel` directly.
- Replace `InteractiveDocument` usage with a small native prompt/document helper used by
  concat-act and formative memory generation.
- Move Concordia HTML log generation behind the bridge; native default should write
  JSONL and HTML using Silisocs log structures.
- Preserve artifact names: `effective_config.yaml`, `action_events.jsonl`,
  `probe_events.jsonl`, `sim_metrics.json`, and `checkpoints/step_*_checkpoint.json`.

### 7. Probes and studies

- Make probe types construct native `ActionSpec`.
- Remove Concordia phase recovery logic; replace with native agent error recovery no-op
  or native state reset when available.
- Keep `experiments/run_study.py` behavior unchanged except that its runner target uses
  the native runtime by default.

### 8. Optional Concordia bridge

- Move remaining Concordia imports behind `silisocs.adapters.concordia`.
- Add optional dependency group: `concordia = ["gdm-concordia>=2.1.0,<2.2.0"]`.
- Add bridge tests under an optional marker.
- Document bridge limitations and native rewrite examples for legacy Concordia agents.

## Migration Phases

1. **Boundary and tests**
   - Add native contracts and import-boundary tests.
   - Add parity tests for action specs, output types, parser, `ComponentEntity`, and
     checkpoint state.

2. **Native runtime skeleton**
   - Convert runner dataclasses/config construction to native types.
   - Keep a temporary Concordia adapter path only while existing behavior is being
     migrated.

3. **Engine and GM port**
   - Port engine loop to native types.
   - Port act routing, GM construction, and all GM components.
   - Verify base, flow, shared-flow, multi-GM, open-ended, fixed-count, and tool-calling
     paths.

4. **Agent/model/memory port**
   - Port standard LLM agent and concat-act.
   - Convert memory initialization to native components.
   - Replace Concordia model/document helpers.

5. **Probes, checkpoints, studies**
   - Port probes to native `ActionSpec`.
   - Version checkpoint schema as `schema_version: 2`.
   - Read old Concordia-shaped checkpoints through the bridge when installed.
   - Verify `run_study.py` still injects checkpoint frequency and evaluators can read
     outputs.

6. **Bridge isolation and packaging**
   - Move remaining Concordia imports behind `silisocs.adapters.concordia`.
   - Make default install Concordia-free.
   - Add bridge-specific tests and docs.

7. **Cleanup and default flip**
   - Remove temporary dual-path code.
   - Delete, quarantine, or mark legacy Concordia examples as bridge examples.
   - Run the full quality gate and update all public docs.

## Test Plan

- Unit tests: native `ActionSpec`, parser, component lifecycle, flow fields, state
  save/restore, model helper, and memory bank.
- Component tests: observe, resolve, next-acting, recommend, initializer, and
  tool-calling.
- Engine tests: base loop, flow routing, multi-GM, skip step, fixed-count, open-ended,
  probe schedule, and worker throttling.
- Compatibility tests: Concordia prefab/component bridge loads under
  `silisocs[concordia]`; default import path does not require Concordia.
- Regression scenarios: smoke tests for `ai_conference`, `misinformation`, `science_qa`,
  `hobby_collective`, and one fixed-agent scenario with `sim.llm.disabled=true`.
- Study tests: `experiments.run_study plan`, one tiny generated run, evaluator reads
  checkpoints and action/probe events.
- Docs tests: update API docs and fail docs build if Concordia-native guidance remains
  outside bridge docs.

## Assumptions

- Concordia is an optional extra, not a required dependency.
- Existing YAML scenario keys remain stable unless a bridge-only feature explicitly
  requires `compat: concordia`.
- Native runtime owns all new extension APIs.
- Backward compatibility is required for common run artifacts and current config files.
- Old Concordia checkpoints may require installing the bridge.
