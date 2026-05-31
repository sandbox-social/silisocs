# Documentation Coverage and Freshness

This page tracks what the documentation covers, where it is stale, and which
runtime surfaces need explicit docs before they should be treated as stable.

Last reviewed: 2026-05-11.

## Coverage Matrix

| Area | Source of truth | Primary docs | Status |
|---|---|---|---|
| Runtime entrypoint and config composition | `src/silisocs/runtime/runner.py`, `src/silisocs/conf/` | [Configuration](configuration.md), [Usage](usage.md) | Covered; keep examples synced with packaged defaults |
| Agent runtime and persona pipeline | `src/silisocs/agents/` | [Building Agents](building_agents.md), [Simulation Extensibility API](simulation_extensibility_api.md) | Covered; custom runtime checkpointing should stay visible |
| Backend app contract | `src/silisocs/environments/backends/base.py`, `factory.py` | [Backends](backends.md), [Environment Layer](environment_layer.md) | Covered; `BackendApp` is the core contract |
| Timeline/recsys capability interface | `twitter_like/`, `reddit_like/`, `mastodon/` | [Backends](backends.md), [Configuration](configuration.md) | Covered; `SocialBackendApp` wording is capability-scoped |
| Reference-world backends | `resource_market/`, `virtual_space/`, packaged env/agent/scenario configs | [Backends](backends.md), [Environment Layer](environment_layer.md) | Covered; keep examples synced with packaged defaults |
| GM component slots | `environments/gm/components/` | [Environment Layer](environment_layer.md), [Simulation Extensibility API](simulation_extensibility_api.md) | Covered; strict `params` behavior must be called out |
| Flow and multi-GM routing | `gm/game_master.py`, `simulation_engines/multi_gm.py` | [Multi-GM Architecture](multi_gm_architecture.md), `agent_docs/architecture.md` | Covered but duplicated; public docs should be canonical |
| Engine policies | `src/silisocs/simulation_engines/policies/` | [Environment Layer](environment_layer.md), [Simulation Extensibility API](simulation_extensibility_api.md) | Covered after path correction |
| Evaluation probes and studies | `evaluations/probes/`, study docs | [Probes](probes.md), [Study Guide](study_guide.md), [Study Schema](study_schema.md) | Covered; update when evaluator APIs change |
| Dashboard | `src/silisocs/dashboard/launch_app.py` | [Dashboard](dashboard.md) | Covered; advanced config controls need periodic UI sync |
| Extension API docs | Public extension contracts | [Simulation Extensibility API](simulation_extensibility_api.md) | Curated API reference; generated internal pages are not shipped |

## Stale or Risky Content Register

| Issue | Impact | Required action |
|---|---|---|
| Old `src/silisocs/engines/...` paths | Misleads contributors; code now lives under `simulation_engines` | Replace stale paths in public docs |
| Backend capability wording | Hides that all backends are peer environment choices | Reframe docs around named backends; mention `SocialBackendApp` only for timeline/recsys component requirements |
| `sim.enable_engine_multi_flow` references | Incorrect config knob | Use `sim.engine.step.built_in: flow` |
| Prompt addition key mismatch | Users may set a no-op key | Document `sim.prompt_additions.action_count_guidance` |
| Advanced flow docs duplicated across `docs/` and `agent_docs/` | Divergence risk | Keep public docs canonical; use `agent_docs` as agent-facing deep dives |
| Backend boundary refactor log in docs | Useful history but not a user guide | Keep linked from roadmap or remove before release docs if it becomes stale |

## Extension Surface Coverage

Each extensible simulator part should have one documented shape:

- Agent: implements `name`, `observe(str)`, `act(action_spec) -> ActionOutput`; optional
  `get_state()` and `set_state()` for checkpoint restore.
- Backend app: subclasses `BackendApp`, implements
  `initialize(agent_names, **kwargs)`, optionally overrides `update(...)` and
  `observe(...)`, and exposes actions with `@app_action`.
- Timeline/recsys backend capability: subclasses `SocialBackendApp` when
  timeline, feed formatting, recommendation update, or parsed social action
  components are needed.
- GM component: selected from `env.gm.components.<slot>`, receives YAML
  `params`, and implements direct native methods for its slot. Native
  components do not expose Concordia lifecycle hooks.
- Engine turn policy: selected from `sim.engine.turn_policy`, receives YAML
  `params`, and implements the turn policy interface.
- Probe schedule: selected from `evals.probes.schedule`, receives YAML
  `params`, and implements the probe schedule interface.
- Probe/evaluator: configured under `evals.probes` or study evaluator presets,
  with output written to probe event artifacts.

## Freshness Process

When changing runtime behavior, update docs in the same pull request:

1. Search docs for old names, paths, and config keys.
2. Update the reference page for the changed interface.
3. Update one workflow guide or recipe if users need a new invocation pattern.
4. Add or update a contract test for the extension surface.
5. Run `uv run properdocs build --strict`.

The minimum release-readiness marker is: no known stale paths/config keys in
public docs, strict docs build succeeds, and the coverage matrix names every
stable extension surface.
