# CLAUDE.md

This file provides Claude Code-specific instructions for this repository.

**Read AGENTS.md before starting any task** - it contains build commands, architecture, and conventions.

## Session State

Use `SESSION_STATE.md` (gitignored) to maintain context across a work session.

**Auto-read**: At session start, check if `SESSION_STATE.md` exists and read it to restore context.

**Auto-update**: After completing significant subtasks (commits, refactors, feature completion), offer to update SESSION_STATE.md with current progress.

**Manual clear**: User decides when to clear - typically at start of unrelated work or after a clean commit.

**Template:**
```markdown
# Session State

## Current Focus
Brief description of current task

## Modified Files
- path/to/file.py - what changed

## Decisions Made
- Chose approach X because Y

## Next Steps
- [ ] Pending task 1
- [ ] Pending task 2

## Open Questions
- Question for user about X?
```

## Reminders

- When adding a feature, ask: "Should I update AI_CONTEXT.md with this new pattern/feature?"
- Run pre-commit before committing (see AI_CONTEXT.md for workflow)
- Use `uv run` prefix for all commands

## WSL Performance

If imports are slow (1+ min), the venv is likely on `/mnt/c`. Use a WSL-native venv:
```bash
export UV_PROJECT_ENVIRONMENT=~/venvs/simulator
uv sync  # re-sync into the WSL-native location
```
