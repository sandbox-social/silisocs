# Agent Docs

These guides are written for repo-aware coding agents and human contributors.
They are intentionally tool-neutral: Claude Code, Codex, Cursor, and other agents
should all be able to follow them.

Start with [`AGENTS.md`](../AGENTS.md) for repository-wide contracts. Then use
the map below to inspect the smallest runtime surface that owns the requested
behavior before writing code.

## Workflow Context

Every scenario, study, and reproduction workflow starts with the same four
references:

1. [`AGENTS.md`](../AGENTS.md) for runtime invariants and contributor rules.
2. [`configuration.md`](../docs/configuration.md#slots) for the canonical config
   and slot surface.
3. [`architecture.md`](architecture.md) for execution and data flow.
4. [`scenario_design.md`](scenario_design.md) for scenario file ownership and
   composition.

Follow only the focused guides relevant to the behavior being changed:

| Concern | Runtime owner | Implementation entry point | Focused guide |
|---|---|---|---|
| Runtime composition and execution | Session and construction | `src/silisocs/runtime/execution/session.py`, `src/silisocs/runtime/construction/` | [`configuration.md`](../docs/configuration.md) |
| Agents, personas, models, and memory | Agent and builder slots | `src/silisocs/agents/`, `src/silisocs/runtime/construction/agent_builders/` | [`building_agents.md`](../docs/building_agents.md) |
| Model providers, harnesses, and plugins | Provider, agent, and plugin slots | `src/silisocs/runtime/language_models/`, `src/silisocs/agents/harness/`, `src/silisocs/runtime/plugins.py` | [`building_agents.md`](../docs/building_agents.md) |
| World state and domain actions | Backend slot | `src/silisocs/environments/backends/` | [`backends.md`](../docs/backends.md) |
| Prompting, observation, resolution, updates, and actor selection | Individual GM component slots | `src/silisocs/environments/gm/components/` | [`simulation_extensibility_api.md`](../docs/simulation_extensibility_api.md) |
| Turns, participation, step traversal, loops, and multi-GM routing | Policy and engine slots | `src/silisocs/simulation_engines/` | [`multi_gm_architecture.md`](../docs/multi_gm_architecture.md) |
| Initial state and formative memory | Initialization slots | `src/silisocs/initialization/` | [`memory_initialization.md`](../docs/memory_initialization.md) |
| Timed changes and injected events | Intervention handler slots | `src/silisocs/simulation_engines/interventions.py` | [`configuration.md`](../docs/configuration.md#mid-run-interventions) |
| Checkpoint, resume, and branch behavior | Save/restore strategies and runtime execution | `src/silisocs/runtime/checkpointing/`, `src/silisocs/runtime/execution/branching.py` | [`configuration.md`](../docs/configuration.md#checkpoint-restore) |
| Probes, event semantics, and run artifacts | Evaluation extensions | `src/silisocs/evaluations/` | [`probes.md`](../docs/probes.md) |
| Conditions, replication, and aggregation | Study schema and runner | `src/silisocs/studies/` | [`study_guide.md`](../docs/study_guide.md) |
| Reports, panels, and interactive inspection | Analysis and Studio extensions | `src/silisocs/analysis/`, `src/silisocs/studio/` | [`analysis_panels.md`](../docs/analysis_panels.md), [`studio.md`](../docs/studio.md) |

For a custom implementation, inspect the live interface or base class, its
factory, and one shipped implementation. Documentation identifies the owner;
the current code defines the contract.

## Guides

- [`scenario_design.md`](scenario_design.md) - design and create a scenario through configuration.
- [`architecture.md`](architecture.md) - understand Engine flows, Game Masters, component routing,
  participation, branch routing, and multi-GM execution.

## Guided Workflows

- [`skills/new-scenario.md`](skills/new-scenario.md) - conversational workflow for designing a new
  scenario, then writing it with `uv run silisocs new-scenario`.
- [`skills/new-study.md`](skills/new-study.md) - conversational workflow for designing a reproducible
  study, then writing it with `uv run silisocs new-study`.
- [`reproduce/SKILL.md`](../.agents/skills/reproduce/SKILL.md) - evidence-first workflow for mapping a
  paper or codebase into independently composable SiliSocs scenario and study
  components before implementing it. Claude users can invoke `/reproduce`.

## Maintenance Notes

- Keep public docs in `docs/` canonical for user-facing behavior.
- Keep these files focused on agent navigation, design prompts, and codebase
  extension context.
- Every workflow must enforce the same extension order: configure a built-in,
  implement the narrowest owning slot, then escalate through policy, step, loop,
  and whole-engine slots only when the preceding contract is demonstrably
  insufficient. Scenario-specific core branches are never an escalation step.
- When runtime paths or config keys change, update `AGENTS.md`, this index, and
  any affected workflow under `agent_docs/skills/` in the same change.
