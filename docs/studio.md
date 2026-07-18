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
   and usage. Active runs automatically open in Watch mode. An **interactive
   launch** additionally shows Step / Play / Pause / End-run controls that drive
   the simulation one episode at a time (see below); active runs automatically
   open in Watch mode.
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

### Trusted project repositories

Connect any number of local projects directly from **Scenarios**, **Studies**, or
Settings, or pass a repeatable CLI option:

```sh
silisocs-studio --scenario-repo ../my-simulation-project
```

Each connected project has a unique nickname. Scenario origins and discovered
implementation choices use that nickname (for example,
`Policy lab -> CustomObservation`), while the saved scenario still contains the
unmodified runtime value `mypkg.components.CustomObservation`. Connected paths
and nicknames persist in `.silisocs/repositories.yaml` under the workspace root;
the `.silisocs/` directory is gitignored by this repository. This workspace
configuration stays local even when `SILISOCS_STUDIO_STATE` or `--state-dir`
redirects job databases and logs.

The project may contain `scenarios/` plus importable packages at its root or
under `src/`. Studio statically classifies class bases and structural method
signatures against the runtime contracts, without executing project modules
during discovery. The existing class-path seam imports only an implementation
the user selects. Agent, builder, backend, game-master, component, and policy
class paths then appear in free-entry composer controls. No Studio list
duplicates runtime built-ins; built-in names come from the factories that
construct them.

An optional `silisocs-studio.yaml` can publish metadata that cannot be inferred
from a class shape. Settings shows the detected contract, class path, origin,
and any discovery notice. Refresh discovery after changing project code.

Scenario saves remain in their source repository. Launches include every
connected project's root and `src/` on `PYTHONPATH`, and execute from the
scenario's project root. Study scenario selection also derives its config path
and generic `working_directory` from that source, so project-relative data
continues to resolve.

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
        # An ASGI app factory: Studio mounts it in-process.
        app_factory="my_world.viewer:create_viewer_app",
    )
```

`app_factory` names `module:function`, where the function takes a database path
and returns an ASGI app. Studio mounts it inside its own process at
`/viewers/<run id>/<backend>/`, so the Platform tab opens immediately: no
subprocess, no port to allocate, no waiting for a second copy of the web stack to
import. Its pages must address their own assets **relatively** (`assets/x.css`,
`fetch('api/...')`) so they resolve both standalone and under the mount prefix.

`module` remains the standalone `python -m` server, and Studio falls back to
launching it as a subprocess for a viewer that is not an ASGI app; the page then
polls `/api/viewers/<run id>/<backend>/status`, which reports `starting` until
the port actually accepts connections and `failed` (with the exit code) if the
process dies.

A backend that has no database or visualizer remains fully usable; the Platform
tab shows a capability-aware empty state.

### Interactive stepping

Choose **Interactive** beside Launch in the scenario editor (or send
`interactive: true` in the launch payload) to run the simulation under
episode-boundary control. Studio injects the runner's
`sim.engine.control` overrides and a `run.control` file path both processes agree
on, then the live view renders four controls:

- **Step** — advance exactly one episode, then hold.
- **Play** — run freely to `num_steps`.
- **Pause** — hold at the next episode boundary (an in-flight episode finishes).
- **End run** — stop cleanly after the current episode (writes the final
  checkpoint and manifest).

The buttons `POST /api/jobs/<id>/control`, which writes the run's control file;
the runner's `control_file` controller polls it and gates the loop. The client
derives the absolute target episode from the `step_started` / `step_finished`
events the existing SSE stream already emits, so no new stream is introduced.
Because control acts at boundaries and every paused loop still checkpoints per
step, a paused or ended interactive run is resume-stable. Studio launches remain
backend-neutral: the control channel names no backend action and adds no
scheduler branch — it only decides *whether* the next episode runs.

### Analysis panels and views

Analysis is artifact-driven. Register a panel with `@register_panel`, a package
entry point, or a configured class path. Views are YAML arrangements of panels
and can ship with a scenario under `conf/views/*.yaml`.

Studio shows a run only the panels its backend can feed: a panel declares the
semantic roles it reads (`Panel.semantics`) and the event streams it needs
(`Panel.requires`), and one that satisfies neither is left out rather than
rendered empty — with a footnote naming what was omitted and why. A view whose
subject panels all fail disappears from the run's navigation, and the scenario
composer applies the same rule to its view builder. See
[Analysis panels](analysis_panels.md) for the contract and a worked example.

Changing a panel control (an episode slider, an agent picker) refreshes that one
panel in place through `/api/runs/{id}/panels/{name}` and updates the URL, so the
view stays linkable without reloading the page — and without re-fetching the
vendored plot bundles, which are served with long-lived validators.

### Unified exploration

`/explore/run/<run-id>` and `/explore/study/<study-id>` provide one
capability-gated workspace for Pulse, Entities, Events, declared environment
viewers, and study comparison. The initial page reads only the manifest and
capability declarations; event streams are queried only after their scene is
selected. Time, actor, label, tag, entity, event, and comparison selections
live in the URL.

Scenes register through `register_scene`, the `silisocs.scenes` entry-point
group, or an `ExplorationScene` class-path contract. Connected project
repositories statically inventory `Panel` and `ExplorationScene` subclasses
alongside runtime components without importing the repository during scanning.
Studio gates every scene from declared features, never from a backend-name
branch.

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

**The components follow the backend.** A composed scenario runs against the
default (social) env group, whose GM components call `SocialBackendApp`-only
methods. Selecting a non-social backend therefore writes an explicit generic
pipeline (`app_initialize`/`app_observation`/`update: none`, plus
`action_mode: generic`) into the scenario's `env.yaml`, with `params: null` on
each slot so the social group's params are replaced rather than merged over.
Selecting a social backend again removes the block. A backend Studio cannot
import is left alone and reported by preflight.

**Scenario shapes.** The composer lists and edits both scenario layouts: the
default-variant shape (`world/default.yaml`, flat `env.yaml`, …) and
config-group variants named after the scenario (`world/resource_market.yaml`,
selected with `world=resource_market`). Editing a variant scenario writes back to
its own group file rather than creating a second option in the same group.

### Complete Studio pages

Installed packages can add navigation surfaces through the
`silisocs.studio_pages` entry-point group. The entry point returns a
`StudioPage(name, label, href, router)`. Settings inventories discovered pages,
panels, views, form schemas, and choice/preview providers.

## URLs and API

Every workflow object has a stable URL:

- `/scenarios/<name>`
- `/runs/<run-id>?tab=analyze&view=network`
- `/explore/run/<run-id>?lens=events&tag=create`
- `/studies/<id>?tab=board`
- `/explore/study/<id>?lens=compare&compare=condition`
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

`silisocs.design` owns light/dark tokens, bundled typography, generated CSS,
shared viewer icons, and the canonical Plotly and Matplotlib styling. Static
reports embed the same font and variables, while backend viewers consume the
shared design assets without inheriting backend-specific behavior. New charts
should return Plotly JSON through the panel contract so the template is applied
consistently by the renderer.

The visual source and reference images are documented in
[`docs/design/README.md`](design/README.md).
