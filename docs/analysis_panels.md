# Analysis Panels and Views

Silisocs analysis extensions consume typed artifacts. They do not discover files
inside a run directory themselves. Load artifacts with `load_run()` or
`load_study()`, then pass them to a panel or view.

## Create a panel

Subclass `Panel`, declare its identity and scope, and return one of the portable
output types: `Figure`, `Table`, `Markdown`, `Html`, or `Grid`.

```python
from silisocs.analysis import Figure, Panel, register_panel

@register_panel
class ContagionCurve(Panel):
    name = "contagion_curve"
    title = "Contagion curve"
    scope = "run"
    requires = frozenset({"action_events"})

    def build(self, artifact, params):
        rows = list(artifact.iter_actions())
        return Figure({
            "data": [{"type": "scatter", "x": list(range(len(rows))),
                      "y": [1] * len(rows)}],
            "layout": {"xaxis": {"title": "Event"}},
        })
```

Installed packages can expose panel classes through the
`silisocs.panels` entry-point group. One-off panels can be referenced by
`class_path` without registration.

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

Built-in run views are `overview`, `network`, `content`, `probes`, and `cost`;
built-in study views are `comparison`, `hypotheses`, and `progress`. A scenario
can ship additional views under `conf/views/*.yaml`; Studio enumerates those
files and never accepts a request-provided filesystem path.

## Backend-neutral semantics

Every backend automatically works with panels that consume the common artifact
contract: health, raw actions, action trends, probes, harness telemetry, and
model usage. No Studio switch statement maps backend names to panels.

Panels that need domain meaning, such as a threaded-content browser or an
interaction graph, consume optional **semantic capabilities**. Register them in
the backend package:

```python
from silisocs.evaluations.vocabulary import (
    ActionVocabulary,
    EventSemantics,
    register_action_vocabulary,
    register_event_semantics,
)

register_action_vocabulary(
    "my_world",
    ActionVocabulary(creates_content=frozenset({"publish"})),
)
register_event_semantics(
    "my_world",
    EventSemantics(
        roles={
            "content.root": frozenset({"publish"}),
            "content.reply": frozenset({"respond"}),
            "network.follow": frozenset({"connect"}),
        },
        fields={
            "content.id": ("object.id",),
            "content.parent_id": ("object.parent",),
            "content.text": ("object.body",),
            "network.target_actor": ("target.name",),
        },
    ),
)
```

Alternatively, a custom `BackendApp` can declare the same portable mapping on
the class. The runtime copies it into `run_manifest.json`, so Studio can render
it later without importing the backend implementation:

```python
class MyWorld(BackendApp):
    event_semantics = {
        "roles": {"content.root": ("publish",)},
        "fields": {"content.id": ("object.id",), "content.text": ("object.body",)},
    }
```

Role and field names are open, namespaced strings. A custom panel can introduce
another namespace without changing `RunArtifact`, Studio, or a backend base
class. If a backend does not register the roles a specialized built-in panel
understands, that panel renders no compatible domain objects; generic panels
continue to work. This explicit declaration is preferable to guessing meaning
from arbitrary function names.

`Panel.requires` is enforced when a view is built: a panel whose named event
stream (`action_events`, `exposure_events`, `probe_events`, `harness_events`)
is absent from the run renders an explanatory note instead of being built.
Requirement names outside those streams are informational only.

Reports are self-contained: the vendored Plotly and Cytoscape bundles are
inlined, so exported HTML has no CDN or server dependency.

## Studio

Install the optional web dependencies and launch the artifact browser:

```bash
pip install "silisocs[studio]"
silisocs-studio --output-root outputs --port 8765
```

Studio serves its OpenAPI documentation at `/api/docs`. Panels are read-only;
launching belongs to the generic job control plane. Brand CSS custom properties
are generated from `silisocs.design` at `/assets/tokens.css`.
