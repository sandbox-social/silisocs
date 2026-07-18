# Unified Exploration Model

## Goal

Replace the current sequence of separate overview, platform, analysis, and
comparison destinations with one exploration workspace. A user should move
from a run-wide signal to an episode, cohort, entity, event, or comparison
without losing context.

The model is artifact-first and backend-neutral. Backends publish capabilities
and typed event data; exploration surfaces consume those contracts. Studio must
not branch on a built-in backend name to decide what can be explored.

## Product Model

The workspace has four stable regions:

1. **Scope bar**: run or study, time window, cohorts, event tags, and comparison
   baseline.
2. **Scene**: the primary interactive visualization selected by the current
   lens.
3. **Inspector**: details for the selected entity, event, interval, or cohort.
4. **Evidence rail**: related panels, probes, configuration changes, and source
   events supporting the current selection.

Users change lenses without changing the underlying query:

- **Pulse**: activity, state, and outcomes over simulation time.
- **Entities**: agents and domain objects, with relationships where available.
- **Events**: the canonical event stream, grouped by open tags and labels.
- **Space**: a backend-provided spatial, network, market, or platform scene.
- **Compare**: conditions, seeds, cohorts, or time windows aligned on one axis.

A lens is available only when the artifact advertises the required
capabilities. `Space` can therefore represent a feed, resource market,
geographic world, organizational graph, or a future domain without a
social-backend switch in Studio.

## Canonical State

One serializable `ExplorationState` owns navigation:

```text
scope:
  kind: run | study
  ids: [artifact ids]
time:
  start: episode or timestamp
  end: episode or timestamp
selection:
  entity_ids: []
  event_ids: []
  cohort_ids: []
filters:
  labels: []
  tags: []
  actors: []
  backend_types: []
comparison:
  dimension: null | condition | seed | cohort | time_window
  baseline: null | id
lens:
  name: pulse | entities | events | space | compare
  scene: capability-selected scene id
```

This state lives in the URL. Every scene, inspector, and evidence panel reads
the same state, making back/forward navigation, sharing, and reloads exact.

## Extension Contracts

### Exploration scenes

Projects register scene implementations through the same class-path and
trusted-project discovery model as scenario authoring. A scene declares:

- stable id and title
- run or study scope
- required artifact capabilities
- supported selection kinds
- optional controls described as typed parameters
- renderer output type

The scene receives an `ExplorationQuery`, not a backend object. It returns
typed visual data or an existing analysis output. Built-in scenes are
enumerated from their runtime registry; project scenes are classified from
their contract or optional manifest.

### Artifact capabilities

Capabilities describe data rather than products:

- event streams and available fields
- entity catalogs and entity kinds
- relationships or edges
- scalar and time-series metrics
- spatial coordinates
- backend-native viewer surfaces
- probes and evaluation outputs
- interventions and configuration changes

Capability values may include schemas and cardinality estimates. The workspace
can then select controls, loading strategy, and valid scenes without knowing a
backend name.

### Query service

Add one query layer over `RunArtifact` and `StudyArtifact`:

```python
query_events(query) -> EventPage
query_entities(query) -> EntityPage
query_series(query) -> SeriesBundle
query_relationships(query) -> GraphBundle
query_evidence(query) -> list[EvidenceItem]
```

Providers implement only the capabilities they own. The default provider reads
canonical artifacts. A project provider may use a database or remote service
behind the same interface.

## Performance Architecture

- Return an inexpensive capability/index document with the initial page.
- Fetch scenes only when selected; never build every panel during navigation.
- Page event and entity results with stable cursors.
- Maintain small artifact indexes for episode, label, tag, actor, and entity id.
- Cancel obsolete requests when exploration state changes.
- Cache query results by artifact fingerprint plus normalized query.
- Virtualize long tables, feeds, and entity lists.
- Stream live append deltas into the query cache used for completed runs.
- Keep Plotly, Cytoscape, and backend viewer code in lazy assets.

Budgets:

- exploration shell response: under 300 ms for a local completed run
- first useful scene: under 1 second when indexes exist
- filter response: under 150 ms for cached local indexed queries
- no route parses all event streams unless the selected query requires it

## Delivery Sequence

### 1. State and shell

Status: implemented.

- Implement `ExplorationState` parsing and URL serialization.
- Add `/explore/run/{id}` and `/explore/study/{id}`.
- Build the scope bar, scene host, inspector, and evidence rail.
- Redirect current Analyze entry points to equivalent exploration state while
  retaining old API routes during migration.

### 2. Capability document

Status: implemented.

- Project current panel and backend declarations into one artifact capability
  document.
- Expose it through a lightweight API route.
- Gate lenses and controls exclusively from this document.

### 3. Query service

Status: implemented for run events, entities, episode series, relationships, and
evidence. Results are memoized by artifact fingerprint plus normalized query, so
a completed run answers repeated cross-filters from cache while a live run
growing on disk invalidates itself.

- Introduce typed query and result objects.
- Adapt current action, exposure, probe, metric, and study readers.
- Add cursor paging and artifact-fingerprint caches.
- Preserve malformed-line I/O robustness at the artifact boundary.

### 4. Core scenes

Status: initial Pulse, Events, Entities, Space, and study Compare scenes are
implemented. They load only when selected and share the URL query language.

- Pulse from current health, trend, and outcome panels.
- Events from canonical event frames with open label and tag filters.
- Entities from actor and entity aggregates.
- Compare from current study comparison outputs.
- Space as a capability-selected adapter for embedded viewers or project
  scenes.

### 5. Cross-filtering and evidence

Status: implemented for run scope.

- Selecting a chart interval sets the time window; selecting an entity refocuses
  every scene on that actor. Both pull selection-scoped evidence.
- Evidence items deep-link to a source episode, actor, probe, or filter through
  their own `refs`. Relationship and evidence queries back the entity lens.
- Playback (a global episode transport) drives the same shared window.
- Study-scope comparison selection alignment remains follow-up.

### 6. Live convergence

Status: implemented for run scope (archive convergence). A running artifact
re-renders the same scenes as its logs grow — the fingerprint-keyed query cache
invalidates itself — and stops when the run reaches a terminal status.

- Use the same scenes for running and completed artifacts. (done)
- Feed job event deltas directly into exploration query caches. (follow-up; the
  re-render path already picks up growth without a delta push)
- Replace the separate Watch analysis surface after parity is verified.
  (follow-up)

## Verification

- A contract test proves a custom project scene appears without Studio edits.
- Capability tests prove unsupported lenses are absent without backend-name
  checks.
- URL round-trip tests cover every exploration state field.
- Query tests cover paging, filters, cache invalidation, and live appends.
- Browser tests cover cross-filtering, history navigation, keyboard use,
  reduced motion, and narrow layouts.
- Performance tests enforce shell and indexed-query budgets on representative
  small and large artifacts.
