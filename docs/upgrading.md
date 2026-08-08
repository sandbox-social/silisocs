# Upgrading

Notes for moving between releases. Each entry lists only user-affecting changes.

## Checkpoint compatibility policy

Checkpoints carry a `schema_version` (and, since 0.4.x, the
`silisocs_version` that wrote them). A checkpoint loads only into a runtime
with the **same** schema version — there is no cross-version migration. On a
mismatch, restore fails with an error naming both versions; either re-run the
simulation or restore with the release that wrote the checkpoint. Schema
bumps are listed in the release notes below when they happen.

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

## 0.3.0 to 0.4.0

### Visual layer

- **Studio replaces the Streamlit and Dash apps (BREAKING).** `silisocs.dashboard`
  and `silisocs.evaluations.analysis.dashboard` are gone, along with the
  `dashboard` and `viz` extras. Install `silisocs[studio]` and run
  `silisocs-studio` (localhost by default; binding wider requires
  `STUDIO_AUTH_TOKEN`). Studio covers what the two apps did — scenario browsing,
  launch, run inspection, analysis — plus study drill-down, live run control, and
  the platform viewers, which previously needed the separate `viz` extra. See
  [Studio](studio.md). `silisocs-report` renders a run's analysis panels to a
  standalone file. There is no shim: `pip install silisocs[dashboard]` and
  `pip install silisocs[viz]` both fail on this release.

### Engine and scheduling

- **Engine preset classes retired (BREAKING for `sim.engine.class_path` only).**
  `BaseRuntimeEngine`, `FlowRuntimeEngine`, `MultiGMRuntimeEngine`, and the
  `silisocs.simulation_engines.multi_gm` module were removed, and
  `BaseRuntimeEngine` is no longer re-exported from the `silisocs` namespace. All
  three were preset wrappers with no behavior of their own: each constructor only
  chose a default step strategy. That choice is now `sim.engine.step.built_in`
  (`base`, `sequential`, `flow`, `multi_gm`, `multi_gm_serial`,
  `multi_gm_staged`). **Engine subclassing is unchanged** — `RuntimeEngine` is
  still exported from `silisocs`, still subclassable, and `sim.engine.class_path`
  still builds it with the full strategy set injected. To migrate, drop
  `sim.engine.class_path` and set `sim.engine.step.built_in`; a stale path raises a
  `ValueError` naming the replacement. A subclass that overrode
  `step_strategy_class` becomes a `sim.engine.step.class_path` instead — that seam
  now receives `flow_chains` and `seed` directly rather than reading them off the
  game master.
- **`multi_gm` now traverses flow chains concurrently (BREAKING).** On 0.3.0,
  `sim.engine.step.built_in: multi_gm` ran flows row-major: each flow completed its
  whole GM chain before the next flow started. It now runs flows as independent
  pipelines that serialize only where they share a GM, with `flow_order` flows as a
  serial prefix. Throughput improves; per-step action ordering across flows no
  longer matches 0.3.0, so a run reproduced from an old seed can differ. Set
  `sim.engine.step.built_in: multi_gm_serial` for the previous traversal, or
  `multi_gm_staged` for a global per-stage barrier.
- **Activity models moved to the sim layer (BREAKING).** The config-derived
  participation models (`activity_probability`, `activity_markov`) moved out of the
  GM's `env.gm.components.next_acting` slot to `sim.engine.participation`. A config
  that names them under `next_acting` raises a build-time migration error. Set
  `sim.engine.participation.built_in: <model>` with the same `params` (minus
  `agent_names`), and leave `next_acting` on an environment-derived built-in
  (`all_agents` or `fixed_order`). Effective acting each step is participation ∩
  `next_acting`. The `ActivityProbabilityNextActing` and `ActivityMarkovNextActing`
  classes no longer exist; the equivalents are in
  `silisocs.simulation_engines.policies.participation`.
- **The bundled social env presets no longer gate participation (BREAKING, and
  silent).** `env/twitter_like.yaml`, `env/reddit_like.yaml`, and
  `env/mastodon.yaml` shipped `next_acting: activity_probability` with
  `user: {inactive_to_active: 0.3, active_to_inactive: 0.3}`, so a bare `env=`
  preset run activated roughly a third of the population each step. Those presets
  now use `next_acting: all_agents`, and `sim.engine.participation` defaults to
  `all`, so **every agent acts every step** — more LLM calls per step and different
  results, with no error to tell you. The default moved because the non-social
  presets (`resource_market`, `virtual_space`, `messaging`, the game backends) have
  no `user` role and were silently falling through to random participation. Every
  bundled *scenario* was migrated and keeps its previous behavior; a scenario of
  your own that relied on the `env=` preset default did not. To restore it, add to
  your scenario's `sim.yaml`:

  ```yaml
  sim:
    engine:
      participation:
        built_in: activity_probability
        params:
          active_probability: null
          min_active_agents: 1
          activity_transition_rates:
            user:
              inactive_to_active: 0.3
              active_to_inactive: 0.3
  ```

- **`flow_order: []` means "no serial prefix" (BREAKING, and silent).** An empty
  list used to be indistinguishable from an absent key, so it collapsed back to
  the default prefix (`["fixed_pre", "default"]`) and quietly reinstated the
  barrier the scenario had deliberately removed. An explicit `[]` is now
  preserved: every flow runs as an independent pipeline with no serial prefix. An
  absent `flow_order` still gets the default. A non-list value (a bare string, a
  number) now raises at build time instead of being coerced. If a scenario of
  yours set `flow_order: []` and relied on the default, spell the two flows out.
- **Unmatched activity rates fail the run (BREAKING for misconfigured runs).**
  Under `activity_probability` / `activity_markov`, every agent must match an
  `activity_transition_rates` entry (by agent name or sim role) declaring
  `inactive_to_active` or `active_to_inactive`. An agent matching none used to fall
  back to a 0.3 activation probability with a one-time warning; it now raises at the
  first step, naming the unmatched agents and roles. Add the missing rates, set
  `active_probability` for one global rate (`activity_probability` only), or use
  `sim.engine.participation.built_in: all`. Runs whose rates already covered their
  roles — including every bundled scenario — are unaffected.

### Config keys

- **Checkpoint schema v7.** Checkpoints persist the game master's backend config,
  which now carries `output_dir` (formerly `output_rootname`), so the schema
  version was bumped: a pre-rename (v6) checkpoint refuses to restore with a
  version error instead of silently rebuilding its backend against an empty
  output directory. Per the compatibility policy above, re-run rather than
  migrate.
- **`silisocs.studies.run_study` is now an entry-point shim.** The study runner
  lives in `silisocs.studies.plan` (pure planning), `silisocs.studies.execute`,
  and `silisocs.studies.cli`. The shim keeps `silisocs-study`,
  `python -m silisocs.studies.run_study`, and the public names (`RunSpec`,
  `EvalSpec`, `StudyConfigError`, `RUN_COMPLETE_MARKER`, ...) importable; code
  importing private `_helpers` from `run_study` must import them from their new
  module.

- **`silisocs.runtime.execution.concurrency` moved to
  `silisocs.runtime.concurrency` (BREAKING for direct importers).** The module is
  a runtime-kernel helper (`run_tasks`, `EventLoopThread`) that layers above
  `runtime.execution` already used, so it moved down to the kernel where the
  import-linter contract can hold it. The old path was removed rather than
  aliased; update the import.
- **`output_rootname` was renamed to `output_dir` (BREAKING).** The key never named
  a *root* under which run directories were created — it named the run's output
  directory itself, and the runtime stamps the resolved path straight back onto it.
  The new spelling matches the programmatic API it has always mirrored
  (`run_simulation(cfg, output_dir=...)`) and the `output_dir=` line the runner
  prints. Rename the key in every scenario's `conf/world/*.yaml` (it is one of the
  universal run params a scenario world file must re-declare) and in any override
  you pass — `output_rootname=./run` becomes `output_dir=./run`, and Studio /
  `silisocs-study` emit `++output_dir=...`. A composed config still carrying
  `output_rootname` fails validation with a message naming both spellings; it is
  never silently aliased. The study plan/run records follow the key: their
  `planned_output_rootname` field is now `planned_output_dir`. Recorded run
  artifacts under `outputs/` and
  `experiments/studies/*/generated/` keep the old key in their
  `effective_config.yaml` — they are historical records of what those runs were
  configured with, and nothing reads the key back out of them.
- **`activity_transition_rates` entry shape is validated at config time.** The
  canonical (and only) entry shape is unchanged — a mapping naming both rates,
  `{inactive_to_active: <p>, active_to_inactive: <p>}` — but a positional pair
  (`user: [0.8, 0.1]`), a scalar, an out-of-range probability, or an entry
  declaring neither rate now fails during config validation instead of at the
  first step. Also note which rate each policy actually reads: under
  `activity_probability`, `inactive_to_active` *is* the per-step probability that
  an agent acts and `active_to_inactive` is inert (see
  [Configuration](configuration.md#social-setup-and-participation)); under
  `activity_markov` both rates drive the chain. No config change is required for
  rates that were already well-formed, including every bundled scenario's.
- **The stale `env.gm.components.next_acting.params.activity_transition_rates`
  cross-reference check was removed.** Activity rates moved to
  `sim.engine.participation.params` in this release, so the check validated a
  location nothing reads and could never fire.

### Backends and run artifacts

- **`action_events.jsonl` records committed actions only (BREAKING for downstream
  analysis).** The log was every invoked action; it is now the canonical log of
  actions that committed a state change or performed a deliberate logged read. A
  rejected, failed, or idempotent call — an agent liking a post twice, replying to
  a post id that does not exist — no longer produces a row. Action counts computed
  from an old log are therefore not comparable to a new one. Backend authors return
  `ActionResult(message, committed=False)` to mark a call uncommitted; plain
  returns still log automatically. Every committed row also appends to an in-memory
  mirror queryable at runtime via `count_committed_events(...)` /
  `iter_committed_events(...)`. See [Backends](backends.md).
- **`event_to_replay_action` moved off backends into a registry (BREAKING for
  custom backends).** Backends no longer carry a replay method; they implement only
  `get_state`/`set_state`. Event-to-action replay now belongs to the
  `social_action_event_replay` restore strategy, which looks up a mapper by
  `backend_type` in `runtime/checkpointing/replay_mappers.py`. A custom backend
  that implemented `event_to_replay_action` should call
  `register_replay_mapper(backend_type, mapper)` at import time with the same
  logic as a module-level function, or set `provides_checkpoint_state = True` and
  self-restore from `get_state`/`set_state`. A restore that a stateless mapper
  cannot express is a custom `sim.checkpoint.restore.class_path` strategy.
- **Unexpected backend failures are counted and re-raised (BREAKING for flaky
  backends).** The shipped social backends wrapped their custom-mode action
  dispatchers in `except Exception` and returned `Error performing <action>: ...`
  as an ordinary observation, so a database failure was indistinguishable from a
  rejected like — and `backend_action_errors` stayed at zero while nothing
  landed. Those handlers now catch only `ValueError`, the platform's language for
  a legitimate rejection (unknown user, missing post id). Anything else
  increments `backend_action_errors` and re-raises into the engine's per-turn
  isolation: the turn is lost and counted (`agent_turn_failures`), the step
  continues. A run against a backend that raises unexpectedly therefore reports
  degraded health where it used to report clean health, and records no action
  where it used to record a failure message. A custom backend that raised a
  non-`ValueError` for an expected rejection should raise `ValueError` instead.
- **`@app_action` rejects unannotated parameters (BREAKING for custom
  backends).** An action parameter with no type annotation used to fall back to
  `Any`, which degraded to a bare string in the generated tool schema and to an
  unparseable argument in generic mode. It now raises a `TypeError` at
  class-definition time — i.e. at import, naming the method and the parameters —
  so annotate every parameter of every `@app_action` method (`*args`/`**kwargs`
  and `self` are exempt).
- **A recommender that fails to initialize raises (BREAKING for incomplete
  installs).** `init_recsys` failures were swallowed, so a missing optional
  embedding dependency or an unsupported algorithm silently ran the whole
  scenario with no recommender — the one thing it had configured. A configured
  recsys type that cannot be initialized now raises. Install the extra, or drop
  the recsys type from the config, to keep the run going.
- **The `current_user` actor-argument alias is gone.** `agent_name` is the only
  runtime-injected actor parameter, and the anti-impersonation guard now covers
  exactly that name. A backend action that declared `current_user` never received
  injection anyway; an agent that supplies it now gets the ordinary
  `Unexpected argument(s): current_user` rejection.

### Health and telemetry

- **Routing fallbacks are counted.** A branch router that falls back (an unusable
  answer or a raised routing call under `on_invalid: random|first`) increments the
  new `routing_fallbacks` [run-health counter](usage.md#run-health), alongside
  `harness_tool_failures`, which now also reaches run health and the manifest.
- **Three more health counters, and a narrowed one.** `recsys_update_failures`
  counts scheduled recommendation refreshes that failed (the run continues on the
  previous rows, which is why this one is counted rather than raised — unlike an
  *initial* `init_recsys` failure, above); `exposure_log_failures` counts
  exposure rows that could not be written to `exposure_events.jsonl`, where a
  bare `pass` used to hide the loss; and `action_argument_coercion_failures`
  counts action arguments that did not match their declared parameter type and
  were passed through as raw strings, which used to be folded into
  `action_parse_failures` — so that counter now means only "the action was
  dropped", and a run's `action_parse_failures` may read lower than on 0.3.0 for
  the same behavior. All are ordinary
  [run-health counters](usage.md#run-health): they appear in the run-end degraded
  warning, `run_manifest.json`'s `health` block, and `RunArtifact.health`, so a
  run that hits one now ends with a degraded-health warning it did not print
  before.
- **`effective_config.yaml` is redacted.** Both copies are written with every
  non-empty `api_key` masked as `**redacted**`, so a run directory is shareable
  even when a key was set in config rather than the environment. Nothing reads
  credentials back from it.

### Only if you tracked `main` between releases

Each item below was introduced and changed *within* the 0.4.0 development
cycle. None ever appeared in a release, so nothing on 0.3.0 can hit them.

- **`sim.engine.step.params.chain_execution` was removed.** The multi-GM flow-chain
  traversal mode is selected by `sim.engine.step.built_in` — `multi_gm`
  (concurrent, default), `multi_gm_serial` (legacy row-major), or
  `multi_gm_staged` (column-major with a per-stage barrier). A config that still
  sets `chain_execution` raises a `ValueError` with a migration hint. Map the old
  value to the matching `built_in`.
- **View `layout: tabs` is rejected.** The analysis view schema accepted
  `grid`, `rows`, and `tabs`, but no tabbed renderer was ever written, so a view
  asking for `tabs` was silently rendered as a grid. `parse_view` now raises
  `View layout 'tabs' is not implemented; use grid or rows`. Change the view
  document to `layout: grid` for the layout it was actually getting. See
  [Analysis panels](analysis_panels.md).
- **Branch routers are now plain callables.**
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

### New, opt-in, no migration required

Every addition below is off or absent unless configured, so an upgraded 0.3.0
config keeps its behavior.

- **Scale-out execution.** `sim.engine.executor: asyncio` runs turns as coroutines
  on one event loop (sync-only agents and models keep working on helper threads);
  `sim.engine.step.params.gm_concurrency_caps` throttles one GM below the global
  limit; `sim.checkpoint.save.built_in: sharded` writes manifest + NDJSON shards
  instead of one JSON per step.
- **Mid-run interventions.** A top-level `interventions` schedule fires
  participation changes, bans, component retuning, turn-policy and router swaps,
  and action/observation injection at step boundaries. See
  [Configuration](configuration.md).
- **Interactive run control.** `sim.engine.control` (`stdin` or `control_file`)
  gates the episode loop for play / pause / step / stop; Studio drives it on the
  live run view.
- **Self-describing runs.** Every run writes `run_manifest.json` (status, layout,
  health counters, LLM usage, artifact paths, git/version/lockfile provenance).
  Load runs through `silisocs.evaluations.run_artifact.load_run` / `load_study`
  rather than rediscovering the file layout. `silisocs doctor` checks an
  environment.
- **New backends and agents.** `env=messaging` (agent-to-agent direct messages),
  `env=public_goods` and the `SimultaneousRoundGame` referee base for
  simultaneous-move repeated games, and experimental
  [harness agents](harness_agents.md) that embed a real agent harness as one
  `Agent`.
- **Agent memory policies.** `sim.memory.built_in` selects `window` (the previous
  behavior, still the default), `retrieval`, or `summarizing`.
- **Probe targeting.** Per-probe `deployment` overrides, `sample_k` /
  `sample_fraction` caps, and the `at` anchor (`pre_step` / `post_step` /
  `run_end`). See [Probes](probes.md).
- **Generated config reference.** [config_reference.md](config_reference.md) lists
  the default value of every packaged config key and is verified against the YAML
  by a test.
