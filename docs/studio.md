# Silisocs Studio

Studio is the supported visual product for scenario design, launch, live
monitoring, platform inspection, analysis, study comparison, and report export.

```sh
pip install "silisocs[studio]"
silisocs-studio --output-root outputs --port 8765
```

Open `http://127.0.0.1:8765`. The API is available at `/api/docs`.
Localhost mutations require no configuration. Binding beyond localhost requires
`STUDIO_AUTH_TOKEN`; Studio sends the browser-stored value as a Bearer token.

## Product workflow

Studio organizes the complete workflow around filesystem-backed objects:

1. **Scenarios**: edit setting, agents, backend capabilities, probes, and views.
   The form and YAML mirror are bidirectional; the YAML files under
   `scenarios/<name>/conf/` remain the source of truth.
2. **Preflight**: validate config semantics, provider requirements, action names,
   and estimated calls/tokens before launching.
3. **Live**: follow queued or running jobs, process logs, artifact growth, steps,
   and usage. Active runs automatically open in Watch mode.
4. **Runs**: inspect manifest health and provenance, open a backend-declared
   platform visualizer, render analysis views, compare the effective config to
   the scenario baseline, and export a self-contained report.
5. **Studies**: author `study.yaml`, fan conditions and seeds through the same
   queue, watch the progress board, compare results, and inspect hypotheses.

Press `Ctrl+K` from any page to navigate to an object. Object pages also register
contextual commands such as launch, switch tab, and select an analysis view.

## General extension model

Studio does not branch on backend names or manually encode backend actions.
Generic surfaces consume:

- run and study artifacts;
- backend action catalogs discovered from `@app_action` declarations;
- backend visualizer capabilities;
- declarative panel/view and form contracts;
- job records and SSE events.

A custom backend becomes launchable when it is registered through the backend
factory or referenced through the supported class-path configuration. Studio
obtains its action picker from `BackendApp.declared_action_catalog()` without
constructing the backend.

### Optional platform visualizer

A backend can publish a read-only platform viewer without a Studio code change:

```python
from silisocs.environments.backends.base import SocialBackendApp, VisualizerSpec


class MyWorld(SocialBackendApp):
    visualizer = VisualizerSpec(
        env_var="MY_WORLD_DB",
        module="my_world.viewer",
        default_port=8100,
        port_env="MY_WORLD_PORT",
    )
```

The runtime records the declaration and database path in `run_manifest.json`.
Studio allocates a dynamic port and launches the declared module. A backend that
has no database or visualizer remains fully usable; the Platform tab shows a
capability-aware empty state.

### Analysis panels and views

Analysis is artifact-driven. Register a panel with `@register_panel`, a package
entry point, or a configured class path. Views are YAML arrangements of panels
and can ship with a scenario under `conf/views/*.yaml`.

See [Analysis panels](analysis_panels.md) for contracts and examples. Panels
should use semantic event roles when available and retain a generic table or
metric fallback when a domain does not expose social content/network semantics.

### Composer fields

Register a dynamic choice source when a field's options come from runtime
capabilities:

```python
register_choice_provider("my_backend.objects", discover_objects, deferred=True)

Field(
    key="env.backend.params.object_types",
    widget="chips",
    label="Object types",
    group="Backend",
    choices_from="my_backend.objects",
    choices_depend_on=("env.backend.class_path",),
)
```

Deferred providers load after the form shell and rerun only when a declared
dependency changes. A genuinely custom control can use
`widget="class_path:mypkg.MyWidget"`; it implements
`render(field, value, files)`. Unknown YAML keys survive round trips.

### Complete Studio pages

Installed packages can add navigation surfaces through the
`silisocs.studio_pages` entry-point group. The entry point returns a
`StudioPage(name, label, href, router)`. Settings inventories discovered pages,
panels, views, form schemas, and choice/preview providers.

## URLs and API

Every workflow object has a stable URL:

- `/scenarios/<name>`
- `/runs/<run-id>?tab=analyze&view=network`
- `/studies/<id>?tab=board`
- `/live?job=<job-id>`

The control plane exposes launch, stop, study, and viewer operations under
`/api`. Job liveness uses server-sent events at `/api/jobs/<id>/stream`; panel
refreshes are invalidated by artifact stream rather than backend type.

## Deployment

Studio is a local, single-user workspace by default. For a shared lab host:

```sh
export STUDIO_AUTH_TOKEN="$(openssl rand -hex 24)"
silisocs-studio --host 0.0.0.0 --port 8765
```

Put TLS and identity at the reverse proxy. Studio deliberately does not add an
account or tenancy model.

## Visual consistency

`silisocs.design` owns light/dark tokens, generated CSS, the canonical Plotly
template, and Jinja component macros. New charts should return Plotly JSON through
the panel contract so the template is applied consistently by the renderer.
