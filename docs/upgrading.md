# Upgrading

Notes for moving between releases. Each entry lists only user-affecting changes.

## 0.2.x to 0.3.0

- **Study runner is a package CLI.** Use `silisocs-study` (or
  `python -m silisocs.studies.run_study`). The legacy study script shims and
  aliases were removed.
- **Scenarios resolve by name.** The scenario library is bundled and addressed by
  name, for example `--config-path election`. Example scenarios are no longer
  shipped in the installed wheel; reference a bundled scenario by name or pass an
  explicit path.
- **Class paths are validated at startup.** An invalid `class_path` now fails fast
  during construction instead of part-way through a run.
- **`num_agents` is a declared total, not a cap.** When a class needs more agents
  than it has personas, the persona records recycle with numbered suffixes, and a
  mismatch between the built agent count and `num_agents` logs a warning.
- **Checkpoint restore is atomic and checkpoint-owned.** Restore validates then
  applies state (see [ADR 0003](adr/0003-checkpoint-owned-restore.md)). Multi-GM
  runs keep per-GM logs and support per-GM restore overrides; the Mastodon backend
  self-restores from its embedded action history.
- **Auto-resume is on by default.** A run now resumes from its own output
  directory when that directory already contains checkpoints, unless you set an
  explicit `sim.checkpoint.source_run`. A fresh output directory (the usual case)
  still starts from scratch. Set `sim.checkpoint.auto_resume: false` to force a
  fresh start into a directory that already has checkpoints.
- **More built-in LLM providers.** Common providers are available as named presets
  (`anthropic`, `gemini`, `openrouter`, `groq`, `together`, `deepseek`, `mistral`,
  `fireworks`, `xai`, `ollama`). Set `sim.llm.provider` to the name; see
  [Configuration](configuration.md). Existing `openai` / `openai_compatible`
  configs are unchanged.
