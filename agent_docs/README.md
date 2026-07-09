# Agent Docs

These guides are written for repo-aware coding agents and human contributors.
They are intentionally tool-neutral: Claude Code, Codex, Cursor, and other agents
should all be able to follow them.

Start with `../AGENTS.md` when changing framework code. Use the guides here when
designing scenarios, studies, or multi-flow/multi-GM configurations.

## Guides

- `scenario_design.md` — design and create a scenario through configuration.
- `architecture.md` — understand Engine flows, Game Masters, component routing,
  participation, branch routing, and multi-GM execution.

## Guided Workflows

- `skills/new-scenario.md` — conversational workflow for designing a new
  scenario, then writing it with `uv run silisocs new-scenario`.
- `skills/new-study.md` — conversational workflow for designing a reproducible
  study, then writing it with `uv run silisocs new-study`.

## Maintenance Notes

- Keep public docs in `docs/` canonical for user-facing behavior.
- Keep these files focused on agent navigation, design prompts, and codebase
  extension context.
- When runtime paths or config keys change, update `AGENTS.md`, this index, and
  any affected workflow under `agent_docs/skills/` in the same change.
