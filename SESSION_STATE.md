# Session State

## Current Focus
Designing and implementing scenario + study generation skill (coding-agent-agnostic).

## Conceptual Model

**Scenario** = shared social world (setting + agents + platform config).
- Stable, versioned, shared via git in `scenarios/` directory.
- Community resource — multiple researchers ask different questions on the same scenario.

**Study** = research question asked on top of one or more scenarios.
- Hypotheses, conditions (Hydra override diffs), evaluators.
- Owned by a researcher; lives in `experiments/studies/<name>/`.

Relationship: many-to-many. A scenario hosts many studies; a study can span multiple scenarios.

## Two Workflows

### `silisocs new-scenario` / `/new-scenario`
Goal: build a reusable social world.
Outputs: `scenarios/<name>/conf/` + optionally `input/entity_lib/` stubs.

Hybrid interactive flow:
1. Free-form: "What social phenomenon does this scenario capture?"
2. LLM expands → setting name + background bullets, event context paragraph
3. Confirm/edit each section
4. "What do your agents DO?" → LLM maps to existing prefabs:
   - `silisocs.agents.entity` — general-purpose thinking/posting agent
   - `silisocs.agents.fixed_entity` — scripted/deterministic (e.g. news bot)
   - If a role can't be mapped: explain + offer to scaffold `input/entity_lib/` stubs
5. Per-role details: names, usernames, personality, goal, seed_post
6. Network & platform: enabled actions, topology, bridge roles
7. Write files + validate (`num_steps=1 llm.disabled=true`)

### `silisocs new-study` / `/new-study`
Goal: design a research study on an existing (or new) scenario.
Outputs: `experiments/studies/<name>/study.yaml`

Hybrid interactive flow:
1. "What is your research question?"
2. LLM: suggest which existing scenarios fit + why → user picks one, or triggers new-scenario inline
3. "What would you vary across conditions?" (independent variable)
4. LLM: draft 2 hypotheses + conditions as Hydra override diffs
5. Confirm/edit each hypothesis + condition block
6. Write `experiments/studies/<name>/study.yaml`

## Architecture

### Shared core: `src/silisocs/scenario_gen/`
```
scenario_gen/
  __init__.py
  models.py      # ScenarioSpec, StudySpec (Pydantic)
  writer.py      # write_scenario(spec, path), write_study(spec, path) — pure file I/O
  expander.py    # LLM-based free-form → structured spec (used by CLI mode)
  validator.py   # dry-run: num_steps=1 llm.disabled=true
```

### CLI entry points (added to silisocs typer app)
- `silisocs new-scenario [--description "..."] [--llm claude-opus-4-7]`
- `silisocs new-study [--description "..."] [--scenario <name>] [--llm claude-opus-4-7]`
- `silisocs new-scenario --from-spec-json '<json>'`  ← used by skill
- `silisocs new-study --from-spec-json '<json>'`     ← used by skill

### Skill entry points (coding-agent-agnostic markdown instructions)
- `.claude/commands/new-scenario.md`
- `.claude/commands/new-study.md`
Agent does the LLM expansion + confirmation loop in conversation,
then calls `--from-spec-json` to write files.

### LLM for CLI expansion
- Configurable via `--llm` flag
- Default: strong model (claude-opus-4-7 or gpt-4o)
- Falls back to `sim.llm` config if set

## Existing Prefab Modules
- `silisocs.agents.entity` — general-purpose agent (persona, goal, memory, tool-calling)
- `silisocs.agents.fixed_entity` — scripted/deterministic sequence agent

## Next Steps
- [ ] Implement `src/silisocs/scenario_gen/models.py` (ScenarioSpec, StudySpec Pydantic models)
- [ ] Implement `src/silisocs/scenario_gen/writer.py` (file generation from spec)
- [ ] Implement `src/silisocs/scenario_gen/expander.py` (LLM expansion)
- [ ] Implement `src/silisocs/scenario_gen/validator.py` (dry-run check)
- [ ] Add `new-scenario` and `new-study` CLI subcommands to runner.py
- [ ] Write `.claude/commands/new-scenario.md` skill
- [ ] Write `.claude/commands/new-study.md` skill
