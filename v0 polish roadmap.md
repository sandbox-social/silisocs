# v0 Polish Roadmap

This roadmap captures nice-to-have and product-completeness work that would make
SiliSocS feel easier, more visual, and more complete. It complements
`v0 improvements.md`, which focuses on correctness and near-term cleanup.

## Product Goal

SiliSocS should feel like a polished research product:

- A new user can install it, run a scenario, inspect results, and understand the
  next step without reading source code.
- A researcher can design scenarios and studies through guided workflows, then
  trust the artifacts and dashboards.
- A framework contributor or coding agent can extend the runtime from clear
  docs, stable interfaces, and discoverable examples.

## P0: One Coherent User Journey

### 1. Unify launch, run history, and analysis

**Current state:** The Streamlit launcher can create/edit scenarios and run
simulations. The Dash analytics app handles deeper post-run analysis. Backend
visualizers exist separately for Twitter-like and Reddit-like SQLite state.

**Gap:** Users have to know which UI to launch and when to switch tools.

**Plan:**

- Add installed commands:
  - `silisocs-dashboard` for the Streamlit launcher.
  - `silisocs-analysis-dashboard` for the Dash analytics app.
  - Optional: `silisocs-visualizer` with backend auto-detection from run output.
- Add a Run History landing view that lists recent runs, health status, metrics,
  artifacts, and available visualizers.
- From a finished run, offer one-click links/actions to:
  - open analysis dashboard,
  - open backend visualizer,
  - open generated config,
  - rerun with modified overrides,
  - resume from checkpoint.

**Done when:**

- Users can start from one command and discover the whole run lifecycle.

### 2. Make run artifacts self-describing

**Current state:** Runs write many useful files, but meaning and completeness are
spread across docs and code.

**Gap:** A user or agent needs a run manifest to know what happened.

**Plan:**

- Write a top-level `run_manifest.json` for every run.
- Include scenario name, command, config path, output schema version, artifact
  paths, backend type, GM layout, checkpoint policy, probe settings, LLM usage,
  and run health.
- Teach dashboards and evaluators to load through the manifest first, with
  fallback to legacy file discovery.

**Done when:**

- Any tool can load a run from one manifest instead of guessing file layout.

### 3. Add a first-run tutorial command

**Current state:** Quickstart docs are good, but the CLI does not guide the first
run.

**Plan:**

- Add `silisocs doctor` for environment checks, optional extras, API keys, and
  basic config validation.
- Add `silisocs tutorial` or `silisocs quickstart` that runs a scripted-model
  demo, prints artifact locations, and offers the next command to inspect it.

**Done when:**

- A new user can verify their setup and produce a viewable run in one guided path.

## P1: Visualizations and Dashboards

### 4. Build a unified run explorer

**High-value views:**

- Timeline of actions by episode and action type.
- Probe trends with confidence bands across seeds.
- Agent activity heatmap.
- Social graph evolution over time.
- Exposure graph: which agents saw which posts and from which source.
- Conversation tree for posts/replies.
- Prompt/response browser filtered by agent, step, GM, action type, and failure.
- Run-health panel with degraded turns, parse failures, backend errors, retries,
  and dropped actions.
- Cost panel with token usage and spend by phase, model, agent class, and GM.

**Architecture note:** Make run loading a deeper Module with a small Interface
around `run_manifest.json`; dashboards, evaluators, and notebooks should not each
rediscover logs differently. This improves leverage and locality.

**Done when:**

- A researcher can explain what happened in a run from one explorer.

### 5. Add study-level dashboards

**Current state:** Study tooling exists, and docs describe analysis outputs, but
interactive cross-condition analysis is limited.

**Plan:**

- Add a study explorer for conditions, seeds, hypotheses, and evaluator outputs.
- Show condition comparisons, seed variability, effect sizes, and status
  summaries.
- Link each aggregate result back to the underlying runs.
- Support export to Markdown, CSV, and notebook cells.

**Done when:**

- A completed study can be reviewed without hand-building plots.

### 6. Improve backend visualizers

**Plan:**

- Auto-detect Twitter-like vs Reddit-like DBs from a run directory.
- Show platform state at selected episode/checkpoint when possible.
- Add search by user/post, conversation navigation, and event replay.
- Link backend posts to action events, exposure events, and prompt logs.

**Done when:**

- Backend visualizers feel like inspectors for simulation state, not separate
  demo servers.

## P1: Easier Scenario and Study Creation

### 7. Make guided creation first-class

**Current state:** `agent_docs/skills/new-scenario.md` and
`agent_docs/skills/new-study.md` define strong guided workflows, but they are
agent-facing docs rather than product features.

**Plan:**

- Add CLI commands for guided prompts:
  - `silisocs new-scenario --interactive`
  - `silisocs new-study --interactive`
- Let users choose between scripted terminal prompts, dashboard forms, or coding
  agent guided workflows.
- Validate generated files immediately and show the exact run command.

**Done when:**

- Users can create valid scenarios and studies without hand-writing YAML.

### 8. Add scenario templates and examples gallery

**Plan:**

- Provide templates for:
  - small social debate,
  - misinformation spread,
  - resource market,
  - virtual space,
  - multi-GM orchestration,
  - fixed-agent seeded scenario,
  - probe-heavy study.
- Add a gallery page with screenshots, output examples, and "run this" commands.
- Add smoke tests that keep gallery examples runnable.

**Done when:**

- Users can start from a concrete example that matches their intent.

### 9. Generate config reference from defaults

**Current state:** Config docs are detailed but manually maintained.

**Plan:**

- Generate stable config tables from packaged YAML defaults and slot registries.
- Include default value, type, allowed values, and docstring.
- Fail docs CI when generated docs drift from config.

**Done when:**

- Users and coding agents can trust config docs as the current Interface.

## P1: Agent and Contributor Experience

### 10. Keep agent docs tool-neutral and discoverable

**Audit result:** The repo is not fundamentally Claude-centric. `CLAUDE.md`
points to `AGENTS.md`, and the main guide is written for LLM coding agents.
However, discoverability needed improvement, and a few stale paths existed.

**Completed in this pass:**

- Added `agent_docs/README.md`.
- Made `AGENTS.md` explicitly canonical for Claude Code, Codex, Cursor, and
  other repo-aware agents.
- Replaced stale `runtime/config.py`, `runtime/simulation.py`, and
  `run_experiment.py` references.
- Fixed the broken `agent_docs/architecture.md` link to the scenario design guide.
- Updated public scenario/study guide shortcut wording to be agent-neutral.

**Remaining plan:**

- Add an "Agent Workflows" section to public docs navigation if the docs system
  should expose contributor-agent workflows.
- Add a docs freshness check that catches missing linked files in `agent_docs/`.
- Add a short "How to hand work to another agent" guide or template if
  multi-agent coding becomes common.

**Done when:**

- Any coding agent can discover the same canonical workflows without tool-specific
  assumptions.

### 11. Add extension scaffolding commands

**Plan:**

- `silisocs scaffold agent`
- `silisocs scaffold backend`
- `silisocs scaffold gm-component`
- `silisocs scaffold evaluator`
- `silisocs scaffold study-evaluator`

Each scaffold should include a minimal implementation, config snippet, and
targeted test.

**Architecture note:** Scaffolding should target stable seams and avoid exposing
internal implementation details. The Module should provide leverage by encoding
the expected Interface once.

**Done when:**

- Extension authors can start with a compiling, tested adapter.

### 12. Add extension contract tests as public examples

**Plan:**

- Keep one minimal custom Adapter per seam in `examples/extensions/`.
- Test those examples in CI.
- Cross-link from docs and scaffolds.

**Done when:**

- Extension docs are executable, not just descriptive.

## P2: Research-Grade Analysis Polish

### 13. Add built-in evaluator registry and metric catalog

**Plan:**

- Make evaluators discoverable by ID, description, required artifacts, and output
  schema.
- Add common metrics:
  - action rates,
  - conversation depth,
  - exposure diversity,
  - agent-level inequality,
  - network centrality over time,
  - probe stability,
  - cross-seed effect size.

**Done when:**

- Study YAML can select metrics as easily as simulation YAML selects policies.

### 14. Add reproducibility and provenance reports

**Plan:**

- Generate a `REPRODUCIBILITY.md` per study/run.
- Include package version, git commit, dirty status, config hash, seed grid,
  model settings, prompt logging mode, checkpoint policy, and artifact hashes.
- Add a command to compare two runs or studies for config/provenance diffs.

**Done when:**

- A paper artifact can be audited without reconstructing context manually.

### 15. Add cost and scale planning

**Plan:**

- Estimate token cost before launching a run or study.
- Warn when conditions multiply into large grids.
- Provide "cheap smoke test" and "full study" modes.
- Surface expected checkpoint/storage volume.

**Done when:**

- Users understand likely cost and runtime before pressing run.

## P2: Packaging and Installation Polish

### 16. Smooth optional extras and commands

**Plan:**

- Add friendly error messages when dashboard/analysis/viz extras are missing.
- Print the exact install command for missing extras.
- Make every optional UI available through a console command.

**Done when:**

- Missing extras feel guided, not broken.

### 17. Add a compatibility matrix

**Plan:**

- Document supported Python versions, optional extras, backend capabilities,
  platform support, and known external-service requirements.
- Include a backend capability matrix for timeline, recsys, live server,
  checkpoint, replay, visualizer, and exposure logging support.

**Done when:**

- Users can tell which feature combinations are supported before trying them.

## P3: Advanced Product Features

### 18. Interactive simulation control

**Plan:**

- Pause/resume runs from the dashboard.
- Inject interventions from the UI.
- Preview intervention schedules on a run timeline.
- Compare "what happened" with "what would happen if intervention changed".

**Done when:**

- Interventions become an exploratory product feature, not just YAML.

### 19. Scenario linting and believability review

**Plan:**

- Add `silisocs scenario-lint`.
- Check agent counts, missing fields, inconsistent goals, unsupported actions,
  unreachable probes, impossible graph settings, and likely no-signal studies.
- Optionally run an LLM-assisted believability review.

**Done when:**

- Users get fast feedback before spending model budget.

### 20. Import/export workflows

**Plan:**

- Export runs and studies as portable archives.
- Import archives into another checkout.
- Add static HTML reports for sharing.

**Done when:**

- Results can move between machines and collaborators cleanly.

## Suggested Release Gates

- One command launches the primary dashboard.
- One command validates environment health.
- A run manifest exists and is consumed by dashboards/evaluators.
- Public docs, `AGENTS.md`, and `agent_docs/` have no stale file paths or broken
  links.
- At least one scenario and one study can be created from guided workflows and
  validated without hand edits.
- The dashboard can load flat and multi-GM run outputs.
- A user can inspect actions, probes, prompts, costs, run health, and backend
  state from linked UI surfaces.
