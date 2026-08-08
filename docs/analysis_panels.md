# Analysis Panels and Views

Silisocs analysis extensions consume typed artifacts. They do not discover files
inside a run directory themselves. Load artifacts with `load_run()` or
`load_study()`, then pass them to a panel or view.

## Create a panel

Subclass `Panel`, declare its identity and scope, and return one of the portable
output types: `Figure`, `Table`, `Markdown`, `Html`, or `Grid`.

```python
from silisocs.analysis import Panel, event_frame, line_figure, register_panel

@register_panel
class ContagionCurve(Panel):
    name = "contagion_curve"
    title = "Contagion curve"
    scope = "run"
    requires = frozenset({"action_events"})
    semantics = frozenset({"health.infection"})

    def build(self, artifact, params):
        counts = event_frame(artifact).where(tag="health.infection").count_by("episode")
        episodes = sorted(counts)
        return line_figure(
            x=episodes,
            series={"Infections": [counts[episode] for episode in episodes]},
            x_title="Episode",
            y_title="Events",
        )
```

Installed packages can expose panel classes through the
`silisocs.panels` entry-point group. One-off panels can be referenced by
`class_path` without registration — Studio's by-name panel endpoints (which
back live refresh) resolve such a panel by scanning the shipped view files that
reference it, so registration is never required for refresh to work. A panel
entry point that fails to import is skipped with a warning rather than breaking
every analysis surface, and a panel that raises while building renders an error
card in its slot rather than failing the whole view.

Scope determines which gates apply: `requires`, `semantics`, and `needs_tags`
read a **run's** manifest, so declaring any of them on a `scope="study"` panel
is a registration-time `ValueError` (they would otherwise be silently ignored).
Study panels gate with `applicable(artifact)` instead.

### There are exactly two renderers

A `PanelOutput` is turned into HTML in two places, and adding a new output type
means teaching **both** (or neither):

1. **Server** — the Jinja macro `render_output` in
   `silisocs/studio/templates/_output.html`. It paints the run and study pages
   on first load, and the static report exporter
   (`silisocs.analysis.report`) renders through the same macro rather than a
   third copy.
2. **Client** — `silisocs/studio/static/panels.js` (`renderPanel`). It repaints
   a panel *without* a navigation: panel controls (`setPanelParam`), the Watch
   tab's live refresh, the study board, and both Explore surfaces, which build
   panels from the JSON the panel API returns.

Two are needed because one surface has no DOM to patch and the other must not
re-navigate. The report exporter also reuses `panels.js` to bring its figures
and network graphs up, so the hydration path is shared too. A scope that lays
stat grids out differently declares it once, as `data-grid-class` on the panel
grid, which both renderers honour.

### `Html` output must escape untrusted content

`Html` and `Markdown` are emitted **verbatim** by every renderer (Studio, the
static report, notebooks) — there is no sandbox. Run data (post text, agent
names, probe responses) originates from model output, so a panel that builds
`Html` from it **must** escape it, or it is a stored-XSS vector:

```python
import html
from silisocs.analysis import Html

def build(self, artifact, params):
    name = next(artifact.iter_actions())["source_user"]
    return Html(f"<p>{html.escape(str(name))}</p>")   # never f"<p>{name}</p>"
```

Prefer `Figure`/`Table`/`Markdown` when they fit; reach for `Html` only for
custom markup, and escape every interpolated value. Colors in a custom `Figure`
should come from `silisocs.design.tokens` (`action_color`, `CATEGORICAL_COLORS`,
`GROUP_COLORS`) so charts stay theme-consistent across renderers.

### Interactive controls

A panel stays a pure function of `(artifact, params)`; interactivity is a shell
concern. Declare `controls` as a tuple of `Control(kind, param, label, choices)`
and the shell renders the widget, re-requesting the panel with the new `param`:

```python
from silisocs.analysis import Control, Panel

class AgentTimeline(Panel):
    controls = (
        Control(kind="episode_slider", param="episode", label="Episode"),
        Control(kind="agent_select", param="agent", label="Agent"),
    )
```

Built-in `kind`s are `episode_slider`, `agent_select`, `probe_select`,
`backend_select` (the run's manifest-declared backend types; the shell renders
it only when a run has more than one, so single-backend runs see no extra
control), `select` (static `choices`), and `text` (a free-text input). A `kind`
the shell does not know degrades to the same text input bound to the same param
— a custom kind is never silently inert, it just isn't pretty until the shell
grows a widget for it. Params travel as `p.<panel>.<param>` query args, so a
control state is deep-linkable. A panel whose `params` value is missing must
fall back to a sensible default (e.g. most-active agent, all episodes, all
backends). Controls render at both scopes: study panels' controls appear on the
study page exactly like run panels' (facet-driven kinds — episodes, agents,
probes, backends — enumerate run data and are skipped at study scope).

### Run reference cells

A `Table` cell may be a **run reference** — a mapping `{"run_path": "<run
directory>", "text": "<label>"}` — instead of a scalar. The panel stays
portable: Studio resolves the path against its run catalog and renders a link
to the run's page (a path it cannot resolve, or any other renderer — static
reports, notebooks — shows just the text). The built-in `study_progress` board
panel emits one per completed replicate, which is how a study board row links
to its run. Any panel, run- or study-scope, may emit them.

## Compose a view

Views use the same slot grammar as runtime configuration:

```yaml
view:
  name: lab_overview
  title: Lab overview
  scope: run
  layout: grid
  panels:
    - built_in: health_summary
    - built_in: action_trends
      params: {cumulative: true}
    - class_path: mylab.panels.ContagionCurve
```

Use `build_view(load_view(...), artifact)` in Python. Studio exposes the same
result at `/api/runs/{id}/views/{view}`, and `silisocs-report` renders it to an
HTML artifact:

```bash
silisocs-report outputs/path/to/run --view overview -o report.html
```

Built-in run views are `overview`, `network`, `content`, `market`, `probes`, and
`cost`; built-in study views are `comparison`, `hypotheses`, and `progress`. A
scenario can ship additional views under `conf/views/*.yaml` — run-scope views
join the run page's navigation, and study-scope views (`scope: study`) become
selectable by every study that declares the scenario, via `?view=<name>` on the
study page and `/api/studies/{id}/compare`. Studio enumerates those files and
never accepts a request-provided filesystem path. Views that a run's backend
cannot feed are not offered at all — see the next section.

## Backend-neutral semantics

Every backend automatically works with panels that consume the common artifact
contract: health, raw actions, action trends, probes, harness telemetry, and
model usage. No Studio switch statement maps backend names to panels.

Every successful `@app_action` call made through the backend invocation API is
logged automatically with its actor, stable action label, processed arguments,
and returned message. Backends return `ActionResult(committed=False)` for a
rejected or idempotent call so it does not enter the committed-action stream:

```python
from silisocs import ActionResult
from silisocs.environments.backends.base import app_action

@app_action(
    tags=("health.infection",),
    fields={"health.subject": "person_id"},
)
def infect(self, agent_name: str, person_id: str) -> ActionResult:
    if person_id in self.infected:
        return ActionResult("Already infected.", committed=False)
    self.infected.add(person_id)
    return ActionResult("Infected.", data={"person_id": person_id})
```

Plain string returns are committed successes. Use `log=False` for successful
reads that should not be action events and `log_as="stable_label"` when the
logged label should differ from the Python method name. Direct
`_log_action_event(...)` calls are reserved for commits outside an invoked
action, such as a scheduled world update.

Panels that need domain meaning, such as a threaded-content browser or an
interaction graph, consume **open action tags** and **semantic fields** declared
on `@app_action`. Tags are arbitrary namespaced strings; the first tag is the
primary category used by behavior charts. Fields map a stable semantic name to
one or more paths in the logged payload. Decorator declarations are derived
into `EventSemantics` and copied to `run_manifest.json`, so Studio can render a
finished run without importing its backend.

Custom panels consume normalized, immutable events instead of parsing raw JSONL
rows:

```python
from silisocs.analysis import event_frame, probe_frame

events = event_frame(artifact)
infections = events.where(tag="health.infection")
subjects = infections.values("health.subject")
per_episode = infections.by_episode()
responses = probe_frame(artifact)
```

`EventFrame` also provides `where(label=, actor=, episode=, pred=)`,
`group_by(...)`, `count_by(...)`, and lazy `to_pandas()`. `probe_frame()` returns
normalized `ProbeEvent` records across the supported probe row shapes. Common
figures and tables can be built with `line_figure`, `bar_figure`, and `table`.

For a semantic shape shared across backend implementations, or a third-party
class you cannot decorate, call
`register_event_semantics(backend_type, EventSemantics(...))` (importable from
`silisocs.environments.backends.event_semantics`, the declaring layer, or via
the `silisocs.evaluations.vocabulary` re-export — one shared registry), or set a
class-level `event_semantics` declaration on the backend (an `EventSemantics`
or the portable `{roles, fields, labels}` mapping — the shipped social backends
declare theirs this way via `social_event_semantics(...)`). Resolution MERGES:
the explicit registration or class declaration wins per entry, and decorator
declarations fill everything they add — so decorating a new action on a backend
that also carries an explicit declaration always reaches the analysis surfaces.
A malformed class declaration raises at resolution (a manifest's semantics
block, being artifact data, still degrades leniently). The five built-in
behavior names (`creates_content`, `endorses`, `negative`, `social_graph`, and
`reads`) are color and ordering conventions only; any tag works throughout the
analysis system.

## Scoping panels to backend capabilities

A run only shows the panels that can say something about it. What a panel needs
is declared on the panel; whether the run supplies it decides:

| Declaration | Meaning | Satisfied when |
|---|---|---|
| `requires` | event streams the panel reads | ALL of them recorded by the run (`action_events`, `exposure_events`, `probe_events`, `harness_events`) |
| `semantics` | semantic roles the panel reads | ANY of them declared by a backend in the run |
| `needs_tags` | the panel groups actions by their open tags | a backend in the run declares at least one tag |

```python
@register_panel
class MarketActivityPanel(Panel):
    name = "market_activity"
    requires = frozenset({"action_events"})       # all-of
    semantics = frozenset({"market.trade", "market.listing"})  # any-of
```

A panel that fails either gate is **not rendered** — no placeholder card. The
view lists what it left out underneath ("Not shown: Probe trends (requires
probe_events — not recorded by this run)"), and `GET /api/runs/{id}/panels/{name}`
answers `409` with the same reason. A view is dropped from the run's navigation
entirely when the panels that give it its subject (those declaring `semantics`)
all fail: a market run has no Network tab, a social run has no Market tab. The
**scenario composer applies the same rule before any run exists** — pick a
backend and its view builder lists only the panels that backend can populate.

Declaring is what you want almost always: the backend describes itself, the
panel says what it reads, and every surface follows without knowing either by
name. Override `Panel.applicable(artifact)` only for a gate the declarations
cannot express — for example `action_alignment` inspects whether the run's
actions actually carry a `suggested_action`, which is optional telemetry rather
than a property of the backend type.

The predicate has two tiers, both in `analysis/views.py`.
`declared_skip_reason()` checks the declarations only (`requires`,
`semantics`, `needs_tags`) — it reads manifests, never event logs, so lazy
surfaces such as the exploration capability document can call it without
parsing a run's data. `skip_reason()` is the render-time check: the declared
tier plus `Panel.applicable(artifact)`, which may read event data. A panel
gated only by an `applicable()` override is therefore listed as available in
the capability document and resolves to "nothing in this run to show" when
rendered — a deliberate trade so that listing panels stays cheap. The
capability document carries each panel's declared reason string alongside
`available`, so exploration surfaces can show *why* a panel is missing (the
Run page's Analyze footnote and the Explore evidence rail both render it).

Worked example, both directions: `resource_market` declares `market.*` roles and
so shows `market_activity`/`market_ledger` and never a follow graph;
`twitter_like` registers `content.*`/`network.follow` and so shows the feed and
network and never a ledger. `virtual_space` is neither social nor a market, yet
by declaring `interaction.directed` (its agents talk to each other by name) it
lights up the interaction network — no panel code knows any backend's name.

Reports are self-contained: the vendored Plotly and Cytoscape bundles plus
Studio's `panels.js` are inlined, so exported HTML has no CDN or server
dependency — and no private copy of the rendering code.

## Studio

Install the optional web dependencies and launch the artifact browser:

```bash
pip install "silisocs[studio]"
silisocs-studio --output-root outputs --port 8765
```

Studio serves its OpenAPI documentation at `/api/docs`. Panels are read-only;
launching belongs to the generic job control plane. Brand CSS custom properties
are generated from `silisocs.design` at `/assets/tokens.css`.
