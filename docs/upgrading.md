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
- **`sim.engine.step.params.chain_execution` was removed (BREAKING).** The
  multi-GM flow-chain traversal mode is now selected by `sim.engine.step.built_in`
  — `multi_gm` (concurrent, default), `multi_gm_serial` (legacy row-major), or
  `multi_gm_staged` (column-major with a per-stage barrier). A config that still
  sets `chain_execution` raises a `ValueError` with a migration hint. Map the old
  value to the matching `built_in`.
- **Activity models moved to the sim layer (BREAKING).** The config-derived
  participation models (`activity_probability`, `activity_markov`) moved out of the
  GM's `env.gm.components.next_acting` slot to `sim.engine.participation`. A config
  that names them under `next_acting` raises a build-time migration error. Set
  `sim.engine.participation.built_in: <model>` with the same `params` (minus
  `agent_names`), and leave `next_acting` on an environment-derived built-in
  (`all_agents` or `fixed_order`). Effective acting each step is participation ∩
  `next_acting`.
- **Unmatched activity rates fail the run (BREAKING for misconfigured runs).**
  Under `activity_probability` / `activity_markov`, every agent must match an
  `activity_transition_rates` entry (by agent name or sim role) declaring
  `inactive_to_active` or `active_to_inactive`. An agent matching none used to fall
  back to a 0.3 activation probability with a one-time warning; it now raises at the
  first step, naming the unmatched agents and roles. Add the missing rates, set
  `active_probability` for one global rate (`activity_probability` only), or use
  `sim.engine.participation.built_in: all`. Runs whose rates already covered their
  roles — including every bundled scenario — are unaffected.
- **The `current_user` actor-argument alias is gone.** `agent_name` is the only
  runtime-injected actor parameter, and the anti-impersonation guard now covers
  exactly that name. A backend action that declared `current_user` never received
  injection anyway; an agent that supplies it now gets the ordinary
  `Unexpected argument(s): current_user` rejection.
- **Routing fallbacks are counted.** A branch router that falls back (an unusable
  answer or a raised routing call under `on_invalid: random|first`) increments the
  new `routing_fallbacks` [run-health counter](usage.md#run-health), alongside
  `harness_tool_failures`, which now also reaches run health and the manifest.
- **Branch routers are now plain callables (BREAKING for custom routers only).**
  A custom `{branch: {router: {class_path: ...}}}` router is no longer a `Router`
  subclass. The `Router` ABC, `RouteContext`, `RouterGMView`, and the
  `reads_live_state` / `drives_agent` capability flags are gone. A router is now any
  callable `route(agents, gms, ctx) -> {agent name: chosen gm name}`: it receives the
  flow's agent objects (call `agent.act(...)` directly to involve them), `gms`
  (`{gm name: game master}`, one per choice — read `gm.backend` directly for live
  state), and `ctx` (`RouteInfo(flow, step, seed)`). `class_path` accepts a plain
  function (config `params` bound as keyword arguments) or a class (built with
  `params`). The built-in `random` and `agent_choice` configs are unchanged. Branch
  routing now runs at execution time under **all three** `multi_gm*` traversals —
  `multi_gm_serial` no longer rejects live-state/agent-driven routers. To migrate a
  custom router: drop the base class and flags, rename `route(self, ctx)` to a
  callable taking `(agents, gms, ctx)`, read `gms[name].backend` instead of
  `ctx.gm_views[name].backend`, ask agents by building an `ActionSpec` and calling
  `agent.act(...)` yourself (import `match_choice` for the same lenient answer
  matching), and return a per-agent `{name: gm}` mapping instead of one choice.
