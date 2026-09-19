---
name: reproduce
description: Recreate a paper or code repository as a faithful, decomposed SiliSocs scenario and study. Use when a user links research, an implementation, or both and asks to reproduce, replicate, port, or operationalize its experiments in SiliSocs.
---

# Reproduce In SiliSocs

Recreate the source's experimental system and study design, not merely its visible demo. Preserve provenance, separate confirmed facts from assumptions, and prefer small replaceable SiliSocs components over a scenario-specific monolith.

## 1. Establish The Reproduction Target

1. Read `agent_docs/README.md` -> **Workflow Context**, including its four core references and each focused guide for a concern the source may customize. This provides the repository map from runtime composition through agents, backends, GM components, scheduling, initialization, interventions, evaluation, studies, analysis, and Studio. For every mapped layer, inspect its actual Protocol/base class, construction factory, and one shipped implementation; documentation is navigation, not a substitute for verifying the live extension contract.
2. Open every user-provided paper, supplement, repository, configuration, and dataset reference. For a paper, locate its experiment/method appendix and official code when available. For a repository, inspect its executable paths, configs, tests, and pinned dependencies rather than inferring behavior from the README.
3. State the target claim or experiment, target repository, expected fidelity, and execution budget. Use the current repository when the user has not named another target.
4. Record what is directly evidenced, what is inferred, and what is missing. Do not silently invent prompts, parameters, populations, treatments, metrics, or stopping rules.

## 2. Reconstruct The Experimental Contract

Extract these items before writing implementation code:

- Research question, hypotheses, independent and dependent variables.
- Conditions, controls, seeds, replication counts, sample/population sizes, and stopping rules.
- Agent identities, state, memory, prompts/policies, model settings, and available observations/actions.
- Environment state, transition rules, action semantics, ordering, concurrency, and exogenous events.
- Orchestration, intervention timing, initialization, and termination behavior.
- Measurements, probes, evaluators, aggregation, and the figures/tables or claims they support.
- External data, services, credentials, licenses, and irreducible sources of nondeterminism.

Treat paper prose, released configuration, and executable code as separate evidence. When they disagree, document the mismatch and use the implementation that produced the reported result unless the user chooses otherwise.

## 3. Mandatory Component Map

Before implementation, create `scenarios/<scenario>/REPRODUCTION.md` and add a mapping table with these columns:

| Source construct | Evidence | SiliSocs owner | Selection/config | State owner | Fidelity or assumption |
|---|---|---|---|---|---|

Map every source construct to the smallest appropriate SiliSocs surface:

- Population/personas -> `agents.persona_pipeline` and an existing or custom `Agent` class.
- World state and domain actions -> `BackendApp` plus `@app_action` catalog entries.
- Initialization, actor selection, prompts, observations, resolution, and updates -> their individual GM component slots.
- Episode traversal and action counts -> loop, step, turn, and participation policies.
- Alternative coordinators -> multi-GM flow bindings and routers.
- Timed treatments or injected events -> the generic intervention language.
- Measurements -> probes, event semantics, evaluators, panels, and study aggregations.
- Experimental conditions and replication -> `study.yaml` hypotheses, conditions, overrides, and seeds.

For each mapping, answer: can this piece be selected, tested, and replaced independently? If not, split it unless the source truly has inseparable state. Never force a non-social experiment into a social backend, timeline, post, or recommendation abstraction.

### Mandatory slot-escalation audit

Configuration and slots are an escalation ladder. Always stop at the first layer
that fully owns the behavior:

1. Existing built-in plus configuration.
2. Narrow project-local slot: backend action/state, one GM component role, agent,
   router, intervention handler, probe, evaluator, or panel.
3. Custom turn or participation policy.
4. Custom `StepStrategy` through `sim.engine.step.class_path`.
5. Custom `LoopStrategy` through `sim.engine.loop.class_path`.
6. Custom whole engine through `sim.engine.class_path`.

Before moving down that list, write in `REPRODUCTION.md` which narrower interfaces
and factories were inspected and the concrete reason each cannot express the source
behavior. Higher-level implementations must compose or delegate narrower concerns;
they must not absorb backend state, prompting, measurement, and scheduling into one
class merely because they can.

Reproduction-specific behavior stays in the reproduction package and is selected
through configuration. Do not edit a core factory, add a source/backend-name branch,
or add a built-in for one reproduction. If even the whole-engine seam is insufficient,
stop and describe the defective or missing general extension contract to the user.
Treat repairing that general contract as a separate framework change, not part of the
reproduction implementation.

Do not begin implementation until the map covers every experimental mechanism and measurement. Resolve material ambiguities with the user; otherwise record a conservative assumption and continue.

## 4. Design The Scenario And Study

Read and reuse the current workflows instead of duplicating their schemas:

- `agent_docs/skills/new-scenario.md` for scenario structure, generation, and config validation.
- `agent_docs/skills/new-study.md` for hypotheses, conditions, replication, evaluators, and study planning.

Use `silisocs new-scenario --from-spec-json` only when its social-world scaffold matches the mapped environment. For general or custom environments, author the same canonical scenario config groups directly and select the backend/components through slots. Use `silisocs new-study --from-spec-json` when its schema covers the design; make explicit, validated edits for advanced fields.

Keep source-specific Python beside the reproduction or in a focused importable package. One module should own one concern. Backend actions describe domain capabilities; agents must not encode backend response formats; study evaluators must consume artifacts rather than mutate the simulation. Re-check the slot-escalation audit before creating each custom module.

## 5. Implement In Traceable Slices

Implement in this order:

1. Minimal deterministic environment and action contract.
2. Agent construction and observation/action loop.
3. Scheduling, interventions, and condition overrides.
4. Probes, evaluators, and study aggregation.
5. Source-calibrated prompts, models, datasets, and scale.

After each slice, update `REPRODUCTION.md` with source references, deviations, and the exact config or class that realizes the behavior. Preserve source licenses and attribution. Never commit credentials or downloaded data whose license forbids redistribution. Do not create or push a remote repository unless the user explicitly requests that external mutation.

## 6. Verify Fidelity

Run the narrowest checks that establish the contract:

1. Scenario config dry-run and one short scripted-model smoke run.
2. Targeted tests for custom backend/component/policy state and action semantics.
3. `silisocs-study ... plan` to validate every condition and override.
4. A tiny multi-seed execution when cost and credentials permit.
5. Compare reproduced intermediate quantities and headline metrics with the source, including units, aggregation, and uncertainty.

A scripted smoke run proves plumbing, not the paper's scientific result. Label exact reproductions, close approximations, and unverified behavior separately. Finish with runnable commands, produced artifact paths, known deviations, and the next experiment needed to close each fidelity gap.
