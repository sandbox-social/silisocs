# Silisocs Studio — Unified Visual Framework & Extensible Analysis Design

Status: implemented. Phases 1-5 are complete and the repository has crossed the
Studio-only retirement boundary. The legacy Streamlit and Dash applications,
commands, dependencies, package data, and compatibility shim have been removed.

**End state, stated up front:** one product — **Silisocs Studio** — a SaaS-grade,
API-first web application in which a user operates **purely visually end to end**:
design a scenario, preflight it, launch it, watch it live, inspect the platform,
analyze the run, compose a study, compare conditions, and publish a report — without
touching a terminal (while every screen shows the exact CLI/YAML it stands for).
Streamlit and the Dash app are **fully retired**. The
product must be visually distinguished: not "a framework with a UI," but a designed
instrument (art direction in Part 4.5).

Implementation ledger (kept current):

| Piece | Status |
|---|---|
| Panel contract, registry, entry-points (`silisocs/analysis/panel.py`) | ✅ shipped |
| View spec + `build_view` + `requires` gating (`analysis/views.py`) | ✅ shipped |
| 6 built-in panels (health, action trends, probe trends, agent inspector, recent events, condition comparison w/ CIs) | ✅ shipped |
| `silisocs-report` self-contained HTML exporter | ✅ shipped |
| Action vocabulary registry (`evaluations/vocabulary.py`) | ✅ shipped |
| Backend `visualizer` classvar + derived discovery | ✅ shipped |
| Studio shell: run browser, run page (Analyze tab), `/api/runs`, `/api/.../views/{view}`, `/api/panels`, `/api/views`, tokens.css from `silisocs.design` | ✅ shipped |
| Design package, light/dark tokens, Plotly theme, component macros | ✅ shipped |
| Complete panel catalog, controls, scenario views, self-contained reports | ✅ shipped |
| SQLite job queue, snapshots, stop/reconcile, SSE, Watch, logs/config, viewers | ✅ shipped |
| Declarative FormSchema composer, two-way YAML, previews, preflight, view editor | ✅ shipped |
| Study definition/board/compare/hypothesis surface and notebook affordance | ✅ shipped |
| Lab auth, command palette, plugin inventory/pages | ✅ shipped |
| Retire Streamlit, Dash, their commands/dependencies, and the token shim | ✅ shipped |

---

## Part 0 — The design philosophy, stated once

Silisocs has a house style for extensibility, used everywhere in the runtime:

- **A slot grammar**: every pluggable thing is `{built_in: name | class_path: pkg.Cls, params: {...}}`.
- **A registry idiom**: `register_llm_provider`, `register_replay_mapper`,
  `register_panel`, `register_event_semantics` — decorator/function registration,
  import-before-run, no core edits.
- **A typed artifact layer**: `load_run(dir) -> RunArtifact`, `load_study(dir) ->
  StudyArtifact` — manifest-first, streaming event iterators, the ONE way to read a
  finished run.
- **Duck-typed contracts** over inheritance gates.

The whole visual layer speaks this language. Nothing below invents a new plugin
mechanism; it extends the existing three (slot grammar, registry, artifact layer) into
the visualization domain. Corollary for the UI itself: **every visual surface is a
projection of a YAML/JSON artifact the user could write by hand**, and every screen
offers the "behind the curtain" toggle showing exactly that artifact. Visual-first,
never visual-only.

---

## Part 1 — The object model (the nouns of the language)

| Noun | Status | Definition |
|---|---|---|
| **Event** | ✅ | One JSONL row. Four streams: `action`, `exposure`, `probe`, `harness`. |
| **Run** | ✅ | `RunArtifact` — identity, health, usage, GM layout, streaming event access. |
| **Study** | ✅ | `StudyArtifact` — plan, organized summary (means + replicate CIs), repro lock. Tree: hypotheses → conditions → (scenario × seed) → Runs. |
| **Metric** | ✅ | The `eval.json` shape (`agents` / `aggregated` / `summary`). |
| **Event semantics** | ✅ | Open action tags and semantic payload fields, derived from backend action declarations. |
| **Panel** | ✅ | Pure function: artifact (+ params) → `PanelOutput`. The unit of analysis extension. |
| **View** | ✅ | Named YAML composition of panels — a page. |
| **Job** | ✅ | A control-plane record of one launched process (run, study, or viewer server): pid, status, config snapshot, output dir, log path. Part 4.4. |
| **FormSchema** | ✅ | The declarative field schema the composer renders forms from. Part 6. |

Rules that hold everywhere: panels never touch the filesystem layout (artifact
iterators only); panels are read-only; jobs are the only things that mutate the world,
and only through the control-plane API.

---

## Part 2 — The analysis seam: Panels and Views

### 2.1 Contract (shipped)

`Panel` declares `name/title/scope/requires` and implements
`build(artifact, params) -> PanelOutput` where `PanelOutput = Figure | Table |
Markdown | Html | Grid`. Figures are Plotly JSON — the interchange that makes panels
render identically in Studio, notebooks, and static reports. Registration: built-ins,
`@register_panel`, `silisocs.panels` entry-points, or `class_path` in a view spec.
`requires` naming an event stream is enforced at view build (absent stream → an
explanatory note, never an empty chart).

### 2.2 View spec (shipped)

YAML in the slot grammar; shippable per scenario (`scenarios/<name>/conf/views/`).
Over HTTP only built-in/registered view names are served; view *files* are a
Python/CLI feature (prevents request-named file loads).

### 2.3 The complete built-in panel catalog (target)

The shipped six plus the following. This is the full set required for "complete
analysis" parity with (and beyond) the Dash app and the study notebook:

| Panel | Scope | Output | Notes |
|---|---|---|---|
| `interaction_network` | run | Html (cytoscape.js block) | The marquee view. Nodes = actors (present even without follow edges), edges = follows + per-episode interactions colored deterministically from their action labels. Params: `episode`, `layout` (force/preset), `highlight` (agent). Ships with a deterministic layout seed so screenshots are stable. |
| `content_feed` | run | Html | Read-only feed/thread browser rendered from action events (post/reply trees, per-episode filter). The "what did they actually say" panel; links into the platform visualizer when its server is live. |
| `agent_timeline` | run | Figure | One agent's actions over episodes (dot strip), params: `agent`. Drill-down target from `agent_inspector` rows. |
| `exposure_funnel` | run | Figure | Exposure→action conversion per episode from `exposure_events` (`requires={"exposure_events"}`). |
| `behavior_breakdown` | run+study | Figure | Stacked action-category bars via `vocabulary_for(backend_type)` — the first vocabulary consumer; backend-generic by construction. |
| `probe_distribution` | run | Figure | Per-probe response histogram/violin at a chosen anchor step, params: `probe`, `step`. |
| `token_usage` | run | Figure | Cost/tokens per episode and per model from `llm_usage`. |
| `hypothesis_board` | study | Grid(Html cards) | Statement/IV/prediction/status/finding cards; `follows_from` chains rendered as a small DAG. |
| `per_agent_distributions` | study | Figure | Strip plots of per-agent metrics by condition (from `runs.json` per-agent dicts). |
| `study_progress` | study | Table | condition × scenario × seed grid with `RUN_COMPLETE` status — also the Watch-station board. |

New built-in views: `network` (interaction_network + agent_timeline), `content`
(content_feed + recent_events), `cost` (token_usage + health_summary); study views
`comparison` (existing), `hypotheses` (hypothesis_board + per_agent_distributions),
`progress` (study_progress).

**Panel↔shell control contract** (rigor for interactive params): a panel may declare
`controls: ClassVar[tuple[Control, ...]]` — e.g. `Control(kind="episode_slider",
param="episode")`, `Control(kind="agent_select", param="agent")`. The shell renders
the control, re-requests the panel with the new param (`GET
/api/runs/{id}/panels/{name}?params=...`), and deep-links it (`?panel.network.episode=4`).
Panels stay pure; interactivity is a shell concern. Static reports render the default
params and list the controls in a caption.

### 2.4 Dash retirement (complete)

With `interaction_network` + `content_feed` shipped, the Dash app had no unique
capability left (its heatmap returned as `behavior_breakdown`). Its application,
command, dependencies, and tests are deleted. Analysis now has one extension surface:
the backend-neutral panel/view contracts under `silisocs.analysis`.

### 2.5 Platform-visualizer parity (shipped)

`VisualizerSpec` classvar on `SocialBackendApp`; discovery derives from the backend
registry. Custom backends are auto-detected everywhere.

---

## Part 3 — The end-to-end visual journey (the acceptance narrative)

The definition of "complete unification": a first-year grad student does ALL of the
following without a terminal, and a power user can see the YAML/CLI equivalent at
every step.

1. **Home** — opens Studio, sees recent runs with health chips, a "New scenario" and
   "New study" call to action, and the live badge if anything is running.
2. **Design** — clicks New scenario. Names it. Fills the composer: setting/event text,
   agent classes (persona source picker with live row preview from JSON/HF datasets),
   backend picker with its action catalog shown as toggleable chips, probe editor
   (question, type, schedule, anchor), engine knobs behind an "Advanced" reveal. A
   right-hand **YAML mirror** shows the exact files being authored under
   `scenarios/<name>/conf/`; editing either side updates the other (Part 6).
3. **Preflight** — a panel computes: agents × steps × turn policy → estimated actions,
   LLM calls, tokens, and dollar cost per configured model; validation findings
   (missing keys, unknown actions, provider env vars) as fix-in-place links. A red
   preflight blocks Launch; a yellow one warns.
4. **Launch** — one button. Studio's control plane snapshots the config, queues the
   job, and navigates to the run page which is already in **Watch** mode. The dialog
   shows the equivalent `uv run silisocs ...` command with a copy button.
5. **Watch** — the run page streams: status ribbon (step k/N, elapsed, est. cost so
   far), live log drawer, the `overview` view auto-updating (SSE-invalidated), and an
   embedded **live platform view** (the backend visualizer under Studio chrome) whose
   feed fills in as agents act. Pause/stop controls with confirmation.
6. **Inspect** — Platform tab: the full read-only twitter/reddit UI on the run's DB —
   threads, profiles, trending — served by the visualizer server the control plane
   manages (dynamic port, one click, no collisions).
7. **Analyze** — Analyze tab: view switcher (`overview / network / content / probes /
   cost` + any scenario-shipped views), panel controls (episode scrubber on the
   network, agent drill-down), every state deep-linkable.
8. **Study** — "Promote to study" from any run, or New study: the study composer
   builds `study.yaml` visually — hypotheses (statement/IV/prediction), conditions as
   **override diffs** against the scenario baseline, seeds, evaluators. Launch fans
   out through the same queue; the **progress board** (condition × scenario × seed
   grid) fills cell by cell.
9. **Compare** — study page: `comparison` (CI error bars), `hypotheses` board;
   clicking any cell opens that run's page.
10. **Publish** — "Export report": pick views, get one self-contained HTML file; or
    copy the deep link (lab mode) — plus "Open notebook" for the deep-dive artifact.

Every noun in this journey is a URL. Sharing a moment of a sim = sending a link.

---

## Part 4 — Silisocs Studio: the mature product spec

### 4.1 Stack (decided; unchanged)

FastAPI + Jinja + htmx/Alpine (zero build step, pip-only, `silisocs[studio]`),
plotly.js + cytoscape.js vendored as static assets, SQLite job store. JS islands
behind the API are the pressure valve if a surface outgrows htmx. SSE (native to
FastAPI via `StreamingResponse`) for liveness — no websocket dependency.

### 4.2 Information architecture

Left rail: `Home · Scenarios · Runs · Studies · Live(badge) · Settings`.

Object pages and their tabs (each tab a URL):

- **Scenario page**: `Design · Preflight · Views · History`.
- **Run page**: `Overview · Watch · Platform · Analyze · Config · Logs`. Overview =
  manifest card (status, health counters with tooltips, tokens/cost, provenance,
  game-master layout). Config = effective YAML, pretty-printed, **diffed against the
  scenario baseline** (additions highlighted). Logs = captured stdout with follow
  mode.
- **Study page**: `Board · Compare · Hypotheses · Definition` (Definition = study.yaml
  two-way with the composer).

Chrome: breadcrumbs; `Ctrl+K` command palette (jump to object, switch view, launch);
global status bar (running jobs, provider key status); toasts for job transitions;
skeleton loaders on every async panel; designed empty states (Part 4.5) — an empty
Studio teaches the journey rather than showing a blank table.

### 4.3 The API (complete surface)

```
# read plane
✅ GET  /api/runs                          run index (manifest-backed)
✅ GET  /api/runs/{id}                     one run (manifest + health + usage)
✅ GET  /api/runs/{id}/views/{view}        built view JSON
✅ GET  /api/runs/{id}/panels/{name}       one panel with query params (controls)
✅ GET  /api/runs/{id}/events/{stream}?since_index=N   paged event access
✅ GET  /api/panels · /api/views           plugin inventory
✅ GET  /api/scenarios · /api/scenarios/{name}          scenario library + form schema
✅ GET  /api/studies · /api/studies/{id}/board · /compare

# control plane (POST = mutating, localhost/token-gated)
✅ POST /api/launch                        {scenario|config_yaml, overrides} → job_id
✅ POST /api/studies/{id}/launch           queue the study supervisor job
✅ POST /api/jobs/{id}/stop                SIGTERM, then SIGKILL after grace
✅ GET  /api/jobs · /api/jobs/{id}         job records incl. viewer servers
✅ POST /api/viewers/{run_id}/{backend}    start platform visualizer → {url}
✅ POST /api/scenarios                     write scenario YAML from composer state
✅ POST /api/preflight                     validate + cost-estimate a config payload

# live plane
✅ SSE  /api/jobs/{id}/stream              event types: log_line, step_started,
                                           step_finished, artifact_grown{stream},
                                           status_changed, done{status}
```

SSE contract: events are JSON, `artifact_grown` carries `{stream, new_count}` so the
Watch page invalidates exactly the panels whose `requires` intersect the grown stream.
The UI never polls; `silisocs-report --watch` and notebooks may consume the same feed.

### 4.4 The control plane

**Job store** (`~/.silisocs/studio.db`, SQLite, one table + indexes):

```
jobs(id TEXT pk, kind TEXT run|study_run|viewer, status TEXT
       queued|running|finished|failed|killed|orphaned,
     pid INT, created_at/started_at/ended_at REAL, exit_code INT,
     scenario TEXT, config_snapshot_path TEXT, output_dir TEXT,
     log_path TEXT, parent_study TEXT NULL, port INT NULL)
```

Semantics with the rigor points spelled out:

- **Queue**: FIFO with a global concurrency limit (default 1 for runs; viewers
  unlimited) — protects API budgets when a study fans out. Per-study limit override.
- **Lifecycle**: `queued → running → finished|failed|killed`. On Studio startup,
  `running` rows whose pid is dead are marked `orphaned`, then reconciled against the
  output dir (`RUN_COMPLETE.json` / manifest `status`) — an orphaned-but-complete run
  is healed to `finished`.
- **Logs**: stdout+stderr captured to `log_path` (also the SSE source); retained with
  the run, tailed on the Logs tab.
- **Viewer servers**: started with dynamically allocated ports (bind :0, record the
  port), env-injected DB path, `start_new_session=True`; stopped via the same
  `/api/jobs/{id}/stop`; at most one live viewer per (run, backend) — re-request
  returns the existing URL.
- **Config snapshots**: every launch writes the exact YAML payload to
  `~/.silisocs/snapshots/<job_id>.yaml` before spawn — the job is reproducible even if
  the scenario file changes later.
- Viewer discovery and launch planning live in `silisocs.studio.viewers` and derive
  entirely from each backend's optional `VisualizerSpec` capability declaration.

### 4.5 Design system & art direction (the "visually stunning" spec)

**Design package: `silisocs/design/`** (the sole token and component source):

```
silisocs/design/
├── tokens.py      # color roles (light+dark), type scale, spacing, radii,
│                  # elevation, motion durations, categorical colors, semantic ramps
├── css.py         # tokens → CSS custom properties (:root + [data-theme=dark])
├── plotly.py      # tokens → a registered Plotly template ("silisocs")
└── components/    # Jinja macros: card, stat tile, badge, table, tabs, drawer,
                   # modal, toast, empty state, skeleton, kbd, diff view
```

Every chart everywhere applies the Plotly template (fonts, grid hairlines, ACTION
colors, hover style) — this single move does more for "looks like one product" than
any page redesign.

**Art direction — "the modern instrument."** The shipped Studio pages establish
it; this codifies it so every future page matches. Generated direction studies
live in `docs/design/`; they are references rather than runtime assets:

- **Typography as identity**: a contemporary system sans stack for display, UI,
  and data. Weight and scale establish hierarchy without oversized dashboard
  headings. Uppercase 10px eyebrows label sections. Numbers use tabular lining
  figures.
- **Color discipline**: cool near-white canvas (`#f2f6f6`), white surfaces, and one
  interface accent (teal `#0d9488`) for actions and selection. Categorical data
  deliberately uses the full multi-color ramp; semantic green/amber/red remains
  reserved for status.
- **Rounded construction**: 16px primary surfaces, 12px controls, 8px compact
  controls, and pill status labels. Hairline borders preserve information density;
  restrained elevation separates floating chrome and primary surfaces.
- **Density with air**: generous page margins and section spacing, but dense,
  bordered tables/grids inside cards — instrument, not brochure.
- **Dark theme**: full token set (`[data-theme=dark]`, slate surfaces matching the
  existing docs-site/Streamlit slate, same teal accent), user toggle persisted;
  light remains the default (data-reading surface), dark is first-class for demos
  and ambient dashboards. This resolves the former brand split — the *tokens* are
  the brand and both themes derive from them.
- **Motion**: 120–180ms ease-out on reveals, skeleton shimmer while panels build,
  live-updating numbers tick (no full-panel flash on SSE refresh — patch in place).
  `prefers-reduced-motion` honored.
- **Designed empty/error states**: every empty view carries a small illustration
  (single-color line style), one sentence, and the next action ("No probe events —
  add a probe in the scenario's Probes section"). Errors show the log tail inline.
- **The wow moments** (deliberately engineered, demo-critical): (1) Watch mode —
  platform feed filling up beside a live-updating action chart; (2) the network
  panel scrubbed across episodes, edges lighting up in action colors; (3) the
  composer's form↔YAML mirror editing both ways; (4) one-click report that looks
  print-quality.
- **Accessibility floor**: all token pairs AA contrast-checked (a `design/` unit test
  computes ratios), full keyboard nav, focus rings, aria labels on controls.

### 4.6 Deployment posture (unchanged)

Localhost single-user zero-config by default; lab mode = bind 0.0.0.0 behind a proxy
with `STUDIO_AUTH_TOKEN` (required for all POST/control-plane routes). Accounts and
tenancy remain explicit non-goals; API-first + on-disk state keeps a hosted variant a
deployment problem, not a redesign.

### 4.7 Extensibility surface (updated statuses)

| Extension | Mechanism | Status |
|---|---|---|
| Backend | registry / subclass | ✅ |
| Platform visualizer | `visualizer` classvar → auto-detected | ✅ |
| Analysis panel | `@register_panel` / entry-point / `class_path` | ✅ |
| Analysis view | YAML; per-scenario `conf/views/` | ✅ |
| Event semantics | `@app_action(tags=..., fields=...)` / `register_event_semantics` | ✅ |
| Evaluator/metric | presets / study `eval.py` | ✅ |
| Chart theming | `design.plotly` template override | ✅ |
| Panel controls | `Panel.controls` declarations | ✅ |
| Studio page | `silisocs.studio_pages` entry-point (router + nav entry) | ✅ |
| Composer field | FormSchema `class_path` field type (custom widgets) | ✅ |

All runtime-discoverable: `/api/panels`, `/api/views`, Settings → plugin inventory.

---

## Part 5 — Migration status

- **A. Analyze/Compare** — ✅ shipped (Studio browser + view renderer); Dash retired
  after the network/content panels landed (Part 2.4).
- **B. Watch + Inspect** — ✅ Part 4.3/4.4 SSE + viewer management + embedded
  platform view.
- **C. Launch + control plane** — ✅ Part 4.4; Studio owns the sole launch surface.
- **D. Design** — ✅ FormSchema/YAML composer, preflight, probes, views, history.
- **E. Retirement** — ✅ Streamlit and Dash codepaths are deleted. One product remains.

---

## Part 6 — The Design station (scenario composer), specified

The riskiest migration, so it gets the most rigorous spec. Principle: **the composer
is a renderer of a FormSchema over the existing `config_builder`, and YAML is always
the source of truth** — the GUI authors files under `scenarios/<name>/conf/`, never a
hidden store.

### 6.1 FormSchema — the form language

A declarative schema (Python data, one per config group, versioned with the config):

```python
Field(key="sim.llm.name", widget="text", label="Model", group="Model",
      help="Any provider model id", default_from_config=True)
Field(key="env.gm.backend.excluded_actions", widget="chips",
      choices_from="backend.action_catalog", advanced=False)
Field(key="agents.persona_pipeline.classes", widget="list[ClassEditor]", ...)
Field(key=..., widget="class_path:mypkg.widgets.MyWidget")   # extension seam
```

- Widgets: `text, number, select, chips, toggle, slider, yaml, list[...]`, plus the
  `class_path` escape hatch. `choices_from` binds to live registry data (backend
  action catalogs, discovered agent classes, provider presets) through registries
  and choice providers rather than backend-specific template branches.
- `advanced=True` fields render behind the Advanced reveal (flows, multi-GM
  orchestration, custom class paths) — preserving the "simple-first" UX rule.
- `visible_when` predicates handle conditional fields (e.g. social-backend controls).
- The schema is served at `/api/scenarios/{name}` so the form is data, not templates —
  new config knobs appear in the composer by adding a Field, not a page.

### 6.2 Two-way form ↔ YAML mirror

Right-hand pane always shows the generated files (tabs per file:
`world/default.yaml`, `agents/default.yaml`, `sim.yaml`, `env.yaml`, `eval.yaml`).
Mechanics: form state → FormSchema repository emission → YAML pane; YAML
edits parse → validate → re-hydrate form fields; unparseable YAML locks the form side
with an inline error rather than dropping edits; fields the schema doesn't know
survive round-trips untouched (they render in a "hand-authored" YAML-only section).
This is the "behind the curtain" principle as a *bidirectional* feature.

### 6.3 Validation & preflight

One pipeline, shared with the CLI: schema validation (existing config validators) →
semantic checks (unknown actions, missing data files, provider env vars) →
**cost model**: `agents × steps ×
participation-rate × turn-policy actions × (prompt+completion estimate per action
mode)` → calls/tokens/$ per configured model, reusing the study runner's agent-steps
math. Served by `POST /api/preflight`; rendered on the Preflight tab and inline above
the Launch button. Red findings block launch; the CLI equivalent
(`silisocs-config-dry-run`) is linked.

### 6.4 Probe and view editors

Probes: question list editor (type, choices, schedule, anchor, per-probe deployment
override) mapping 1:1 to `eval.probes`. Views: pick/reorder panels for the scenario's
shipped views (`conf/views/*.yaml`) with live preview against any prior run of the
scenario — "design how this scenario will be analyzed" next to how it behaves.

---

## Part 7 — The study surface, specified

1. **Study composer** (Definition tab): question → hypotheses (statement, IV,
   prediction, `follows_from`) → conditions as **override diffs** (a two-column
   picker: config key from the scenario baseline + per-condition values; renders
   exactly the Hydra overrides) → seeds → evaluations (preset picker + `eval.py`
   detection). Emits `study.yaml`, validated by `study_schema.py`; `reuse_existing`
   conditions get a run-picker from the run index.
2. **Board tab**: `study_progress` panel — condition × scenario × seed grid; cell
   states from `RUN_COMPLETE` markers + job store (queued/running/complete/failed/
   skipped); preflight total cost; per-cell drill-through to the run page; "Resume"
   re-posts to `/api/studies/{id}/launch` (idempotent, marker-aware — semantics the
   runner already has).
3. **Compare / Hypotheses tabs**: the study-scope panels (Part 2.3). The notebook
   remains the publication artifact; a "Open notebook" affordance and panel-importing
   notebook template close the loop.
4. Schema addition (backward-compatible): optional top-level `views:` in `study.yaml`.

---

## Part 8 — The Watch station, specified

- Run page auto-enters Watch while its job is `queued/running`.
- **Status ribbon**: step k/N (from `step_finished` SSE events), elapsed, actions
  committed, est. cost so far (usage accumulates in sim_metrics between steps),
  stop/pause controls (stop = control plane; pause deferred — requires engine
  support, out of scope).
- **Live views**: the Analyze renderer + SSE invalidation (`artifact_grown` →
  rebuild affected panels, patch DOM in place; no flashing). Works because panels
  are pure functions of a growing artifact and the JSONL iterators tolerate partial
  trailing lines.
- **Live platform view**: control plane starts the backend visualizer on the run's
  DB; Watch embeds it (iframe under Studio chrome, tokens.css injected via the
  visualizer's existing template) with an "open full" affordance. The visualizers'
  5s feed auto-refresh (shipped in the demo phase) already makes this live.
- **Log drawer**: `log_line` SSE events, follow toggle, severity highlighting,
  the same content persisted for the Logs tab post-run.
- Studies: the Board tab is the Watch surface; cells flip live via the same job
  events.

---

## Part 9 — Phased plan with acceptance gates

**Phase 1 — the language** ✅ DONE (panels/views/vocabulary/report/visualizer
registry + tests + docs).

**Phase 2 — Analyze completion + design package. ✅ SHIPPED** Deliverables: `silisocs/design/`
(tokens incl. dark set, css.py, plotly template, component macros),
`interaction_network`, `content_feed`, `behavior_breakdown`, `token_usage`,
`agent_timeline` panels; panel `controls` contract + `/api/runs/{id}/panels/{name}`;
scenario `conf/views/` loading; Dash capability replacement.
*Gate:* Dash app deletable with zero capability loss; every chart on every surface
uses the `silisocs` Plotly template; network panel scrubs episodes on the showcase
run; contrast unit test green.

**Phase 3 — control plane + Watch + Inspect. ✅ SHIPPED** Deliverables: job store + queue +
`/api/launch|jobs|stop|viewers` + snapshots + orphan healing; SSE stream; Watch mode
(ribbon, live views, log drawer, embedded platform view); study progress board
(read side).
*Gate:* launch → watch → inspect → analyze completed entirely in Studio on a real
4o-mini run; kill Studio mid-run and restart — job heals correctly; two backends
viewable concurrently without port collisions.

**Phase 4 — composers + study surface. ✅ SHIPPED** Deliverables: FormSchema engine + scenario
composer with two-way YAML mirror; preflight endpoint + cost model; probe/view
editors; study composer + board (write side) + hypotheses/compare tabs.
*Gate:* the full Part 3 journey (steps 1–10) executed purely visually by someone who
has never used the CLI, recorded as the demo; every screen's "show YAML/CLI" toggle
present.

**Phase 5 — maturity + retirement. ✅ SHIPPED** Command palette, Settings/plugin inventory,
dark-theme polish pass, lab mode (token auth on POST), `silisocs.studio_pages`
entry-point, and deletion of the Streamlit + Dash codepaths.
*Gate:* one product; docs rewritten around Studio; `pip install "silisocs[studio]"`
+ `silisocs-studio` is the quickstart.

Each phase is independently demoable; gates are testable statements, not vibes.

---

## Part 10 — Decisions (defaults chosen, flag to veto)

1. Plotly JSON as figure interchange; cytoscape.js via `Html` output for networks. ✅ in force.
2. End state Studio-only; Streamlit/Dash deleted in Phase 5. **Confirmed.**
3. No Node/React; htmx + vendored plotly/cytoscape; JS islands behind the API are the pressure valve. ✅ in force.
4. API-first; POST routes token-gated in lab mode. Default: yes.
5. Panels read-only; mutation only via the control plane. ✅ in force.
6. `silisocs/design/` package supersedes the deleted `visual_tokens.py` shim. **In force.**
7. Light theme default, dark theme first-class via the same tokens. Default: yes.
8. SSE (not websockets) for the live plane. Default: yes.
9. Pause-mid-run deferred (needs engine support); stop only. Default: yes.
