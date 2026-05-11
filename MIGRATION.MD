# Concordia Decoupling and Full Simulation Migration Plan (Silisocs)

## Problem and approach
Silisocs has an explicit custom `Agent` interface, but core runtime construction, execution, and component wiring still depend heavily on Concordia types and base classes. This creates a steep extension surface for newcomers because most extension points are documented as Silisocs concepts while the runtime contract is still Concordia-first.

The plan is to (1) map and score all Concordia dependencies by criticality, (2) introduce a narrow internal runtime contract owned by Silisocs, and (3) keep Concordia portability through a dedicated adapter bridge rather than direct imports across the codebase.

## Current-state dependency map (where + why + criticality)

Rating scale:
- **Critical (5/5)**: simulation cannot run without this dependency today.
- **High (4/5)**: key extension/runtime behavior is tightly coupled, but replaceable with moderate effort.
- **Medium (3/5)**: important feature path, not core scheduler contract.
- **Low (2/5)**: ergonomic/doc/testing coupling; easy to isolate.
- **Very low (1/5)**: naming/comments/legacy residue.

1. **Prefab/config contract and role typing — Critical (5/5)**  
   - **Where:** `runtime/runner.py`, `runtime/simulation.py`, `runtime/dataclasses.py`, `agents/entity.py`, `agents/fixed_entity.py`  
   - **Why:** runtime instance construction uses `concordia.typing.prefab` (`Config`, `InstanceConfig`, `Role`, `Prefab`) as primary schema, so build/dispatch is Concordia-native.

2. **Entity and action-spec typing — Critical (5/5)**  
   - **Where:** `simulation_engines/base_engines.py`, `gm/components/{observe,next_acting,resolve,recommend}.py`, `gm/act.py`, `evaluations/probes/types.py`  
   - **Why:** execution loop, dispatch, and probe flow assume `entity.ActionSpec` and `OutputType` from Concordia.

3. **Game master concrete runtime class — Critical (5/5)**  
   - **Where:** `environments/gm/base_game_master.py`, `shared_flow_game_master.py`, `gm_factory.py`, `agents/initialization/base.py`  
   - **Why:** GMs and initializers are built as `EntityAgentWithLogging` with Concordia `switch_act` + context components.

4. **Engine inheritance and parser semantics — Critical (5/5)**  
   - **Where:** `simulation_engines/base_engines.py`, `runtime/simulation.py`  
   - **Why:** `BaseRuntimeEngine` subclasses Concordia `Simultaneous`; action parsing uses Concordia engine utilities.

5. **Component base classes (observe/next_act/resolve/act) — High (4/5)**  
   - **Where:** `gm/components/*.py`, `gm/act.py`, `agents/components/concat_act.py`  
   - **Why:** component contracts inherit Concordia component abstractions directly; custom components must match those expectations.

6. **Memory backend default and API shape — High (4/5)**  
   - **Where:** `utils/memory.py`, `agents/entity.py`, `agents/initialization/base.py`  
   - **Why:** default associative memory path is Concordia-backed; component memory wiring assumes Concordia memory component behavior.

7. **LLM/document helpers — Medium (3/5)**  
   - **Where:** `utils/media.py`, `agents/components/concat_act.py`, `agents/initialization/formative.py`  
   - **Why:** model interface extends Concordia LM abstractions and uses Concordia `InteractiveDocument`; replaceable with internal wrappers.

8. **Runtime helper and prefab discovery utilities — Medium (3/5)**  
   - **Where:** `runtime/runner.py` (`helper_functions.get_package_classes`), runtime logging paths and concordia-prefab maps  
   - **Why:** startup/build path pulls Concordia package prefab registries and helper utilities directly.

9. **Docs and positioning — Low (2/5)**  
   - **Where:** `README.md`, `docs/index.md`, `docs/concordia_bridge.md`, `docs/simulation_extensibility_api.md`  
   - **Why:** documentation frames Silisocs as Concordia-based and exposes Concordia types in public extension guidance, reinforcing coupling for contributors.

10. **Study orchestration and experiment execution — High (4/5)**  
   - **Where:** `experiments/` runners and study expansion flow (config planning/execution path)  
   - **Why:** study runtime depends on core runner/simulation contracts, so decoupling must preserve study plan expansion and run reproducibility.

11. **Evaluation probes and deployment orchestration — High (4/5)**  
   - **Where:** `evaluations/probes/*`, probe deployment paths in engine/runtime  
   - **Why:** probes rely on Concordia-flavored action specs/entities; migration must avoid breaking longitudinal evaluation output contracts.

12. **Telemetry and output artifacts — Medium (3/5)**  
   - **Where:** `runtime/telemetry/*`, log writers, run metrics and checkpoint paths  
   - **Why:** mostly independent of Concordia but currently attached to Concordia-centric engine/entity lifecycle events.

13. **Dashboard/API integration surfaces — Medium (3/5)**  
   - **Where:** `dashboard/`, API/task orchestration entry paths that invoke runner/studies  
   - **Why:** user-facing configuration and launch UX depends on runtime schema and option names that currently encode Concordia assumptions.

14. **Tests and fixtures — Medium (3/5)**  
   - **Where:** `tests/*` including mocks for `EntityAgentWithLogging`/Concordia typing  
   - **Why:** test harness currently mirrors Concordia classes; migration requires test-contract realignment to internal interfaces.

## Target architecture (keep Concordia portability, remove broad coupling)

1. **Define Silisocs-owned core contracts (`silisocs.core.runtime`)**
   - Internal `ActionSpec`, `OutputType`, `EntityLike`, `GameMasterLike`, `PrefabLike`, `MemoryBankLike`.
   - Keep these minimal and backend-agnostic.

2. **Create a dedicated bridge package (`silisocs.adapters.concordia`)**
   - `ConcordiaActionSpecAdapter` (bi-directional conversion).
   - `ConcordiaPrefabAdapter` (load/build Concordia prefabs through internal `PrefabLike`).
   - `ConcordiaComponentAdapter` (wrap Concordia context components for GM slots).
   - `ConcordiaEngineAdapter` (optional compatibility engine wrapping existing Concordia-based engines).

3. **Refactor runtime to depend on internal contracts first**
   - Runner/simulation/engine/gm interfaces import `silisocs.core.runtime`.
   - Concordia imports move behind adapter boundary only.

4. **Preserve “port Concordia components into Silisocs” capability explicitly**
   - Support config such as `env.gm.components.<slot>.compat: concordia`.
   - Bridge maps Concordia component class + params into internal slot contract.
   - Publish constraints list (supported output types/state APIs).

5. **Broaden migration boundary beyond runtime core**
   - Include studies, probes, telemetry, dashboard/API launch paths, and test contracts.
   - Ensure all public run artifacts (logs/checkpoints/eval outputs) remain stable or versioned.

## Expanded scope by simulation subsystem

1. **Core runtime and orchestration**
   - `runtime/runner.py`, `runtime/simulation.py`, `runtime/factories.py`, `runtime/dataclasses.py`
   - Goal: replace direct Concordia typing/contracts with internal runtime contracts.

2. **Simulation engines and policies**
   - `simulation_engines/base_engines.py`, `simulation_engines/multi_gm.py`, `simulation_engines/policies/*`
   - Goal: abstract engine-facing action spec/entity contracts; isolate Concordia engine adapter.

3. **Agent and prefab pipeline**
   - `agents/entity.py`, `agents/fixed_entity.py`, `agents/builders.py`, `runtime/agent_building.py`
   - Goal: preserve YAML-first prefab UX while decoupling prefab base class contract.

4. **GM layer and component system**
   - `environments/gm/*`, `environments/gm/components/*`
   - Goal: keep slot architecture but move interfaces to Silisocs-owned contracts; support compat wrappers.

5. **Memory and language model abstraction**
   - `utils/memory.py`, `utils/media.py`, initialization modules
   - Goal: introduce internal memory and prompting/document adapters with Concordia bridge backend.

6. **Evaluation and probe runtime**
   - `evaluations/probes/*`, probe scheduling/deployment integration in engine
   - Goal: make probes consume internal `ActionSpec` and entity contracts.

7. **Studies and experiment runner**
   - `experiments/*` run/plan/summary flows
   - Goal: preserve study schema and reproducibility while internal runtime contracts change.

8. **Telemetry, logs, checkpoints, outputs**
   - runtime telemetry modules + output conventions
   - Goal: keep artifact stability (or explicit versioning/migration) independent from Concordia internals.

9. **Dashboard/API launch interfaces**
   - dashboard + API entry points
   - Goal: avoid exposing Concordia-specific concepts in UI/launch schema for newcomers.

10. **Documentation and onboarding**
   - AGENTS/docs/README and extensibility references
   - Goal: position Silisocs-native extension path as primary; Concordia bridge as explicit compatibility mode.

## Structured todos

- `concordia-audit-matrix`: Produce file-level dependency matrix with reason + criticality score.
- `core-contracts-v1`: Introduce internal runtime protocols/types for action specs, entities, prefabs, memory, engine IO.
- `concordia-bridge-scaffold`: Build adapter module to convert between internal contracts and Concordia constructs.
- `runtime-import-inversion`: Refactor runner/simulation/engine/GM surfaces to consume internal contracts.
- `component-compat-layer`: Add explicit compatibility wrapper for importing Concordia-designed GM/agent components.
- `migration-config-flags`: Add config switches for `runtime_mode` (`native`, `concordia_bridge`) and per-slot compatibility mode.
- `engine-policy-migration`: Migrate base/flow/multi-GM engines and policy interfaces to internal contracts.
- `probe-contract-migration`: Move probe APIs and deployment to internal action/entity contracts.
- `study-runner-compat`: Validate and update experiments/study planning/execution paths for decoupled runtime.
- `telemetry-artifact-stability`: Guarantee log/checkpoint/eval artifact compatibility or add explicit versioned migration.
- `dashboard-api-schema-update`: Remove Concordia-first terminology from dashboard/API runtime configuration.
- `test-suite-contract-refactor`: Replace Concordia-typed test fixtures with internal contract fixtures and bridge-path integration tests.
- `deprecation-docs`: Update docs to make Silisocs-native extension path primary and Concordia bridge secondary.
- `compat-tests`: Add contract tests for native and bridged paths (action loop, component routing, checkpoint, tool-calling).

## Phased implementation roadmap (expanded)

1. **Phase A — Inventory + target contract spec**
   - Complete full subsystem dependency map (runtime, engines, probes, studies, telemetry, dashboard/API, tests).
   - Finalize internal contract definitions and migration invariants (artifact compatibility, schema stability).

2. **Phase B — Adapter-first foundation**
   - Implement `silisocs.adapters.concordia` and conversion utilities.
   - Add feature flags for runtime mode and compat mode without changing default behavior.

3. **Phase C — Runtime and GM migration**
   - Move runner/simulation/engine/GM to internal contracts.
   - Keep Concordia behavior reachable via adapter mode.

4. **Phase D — Evaluation/study/telemetry alignment**
   - Migrate probe and study execution paths.
   - Verify output artifacts and checkpoint replay compatibility.

5. **Phase E — UX and docs completion**
   - Update dashboard/API launch vocabulary and docs for newcomer-first extensibility.
   - Publish migration guide and compatibility matrix.

6. **Phase F — Default flip and deprecation**
   - Switch default to native runtime mode once parity gates pass.
   - Keep minimal bridge for importing Concordia-designed components.

## Notes / considerations

- Start with import inversion at type boundaries (contracts) before behavior refactors.
- Keep migration non-breaking by defaulting to bridge mode initially, then flipping default after parity.
- Treat Concordia as a plugin dependency after contract stabilization, not as the runtime substrate.
- Compatibility goal confirmed: **native-first with minimal bridge** for Concordia component portability.
